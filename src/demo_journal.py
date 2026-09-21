"""Account-bound durable control journal for a future single-writer demo runner.

No exchange calls, implicit initialization, state repair, or outcome inference.
Readbacks and persisted plans must be independently verified by the orchestrator.
"""
from contextlib import contextmanager
from copy import deepcopy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re

from cycles import utc
from exchange_history import account_id
from kraken_execution import ExecutionError, _create, _json_bytes, _sync_directory


def sha(value):
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def checked_hash(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value):
        raise ExecutionError('Immutable SHA-256 required')
    return value


def initialize(directory, account_uid, api_key_fingerprint):
    """Explicit, exclusive setup; pin an already verified demo account/key pair."""
    identity = {'version': 1, 'environment': 'demo', 'instrument': 'PF_DOTUSD',
                'account_uid': account_id(account_uid), 'api_key_fingerprint': checked_hash(api_key_fingerprint)}
    root = Path(directory)
    root.mkdir(mode=0o700, parents=False, exist_ok=False)
    _sync_directory(root.parent)
    _create(root / 'identity.json', _json_bytes(identity))
    _create(root / 'head.json', _json_bytes({'sequence': 0, 'sha256': sha(identity)}))
    (root / 'events').mkdir()
    (root / 'artifacts').mkdir()
    _sync_directory(root)
    return identity


@contextmanager
def locked(directory, expected_account, expected_key_fingerprint):
    """Nonblocking advisory lock held across plan/dispatch/readback by the caller."""
    root = Path(directory)
    if root.is_symlink() or not root.is_dir():
        raise ExecutionError('Existing regular demo journal required')
    for path in (root / 'identity.json', root / 'head.json', root / 'events', root / 'artifacts'):
        if path.is_symlink() or not path.exists():
            raise ExecutionError('Incomplete or symlinked demo journal')
    descriptor = os.open(root / 'runner.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    acquired = False
    journal = None
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            raise ExecutionError('Another demo runner owns this journal') from None
        journal = Journal(root, expected_account, expected_key_fingerprint)
        yield journal
    finally:
        if acquired:
            if journal is not None: journal._active = False
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def transition(state, event):
    """Replay the control state; an acknowledgement is not a resolved mutation."""
    result = deepcopy(state)
    payload, kind = event['payload'], event['type']
    if kind == 'capture':
        result['latest_capture'] = payload['artifact_sha256']
    elif kind == 'prepare':
        action = payload['action']
        ident = action['operation_id']
        checked_hash(action['plan_sha256'])
        if not re.fullmatch(r'[a-f0-9]{40}', action['trusted_head']):
            raise ExecutionError('Action requires immutable publication provenance')
        if ident in result['operations']:
            raise ExecutionError('Operation ID already recorded')
        if any(o['status'] != 'resolved' for o in result['operations'].values()):
            raise ExecutionError('A prior mutation is still unresolved')
        if action['capture_sha256'] != result['latest_capture']:
            raise ExecutionError('Action does not bind the latest verified capture')
        endpoint, params = action['endpoint'], action['params']
        if endpoint not in ('sendorder', 'editorder', 'cancelorder'):
            raise ExecutionError('Unsupported demo mutation')
        client_id = params['cliOrdId']
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', ident) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', client_id):
            raise ExecutionError('Invalid stable operation/client identity')
        if action['role'] not in ('ENTRY', 'STOP', 'T1', 'T2', 'T3', 'CLOSE'):
            raise ExecutionError('Unknown order role')
        if endpoint == 'sendorder':
            from exchange_reconciliation import numeric
            numeric(params['size'], positive=True)
            if params['symbol'] != 'PF_DOTUSD' or params['reduceOnly'] is not (action['role'] != 'ENTRY'):
                raise ExecutionError('Send has wrong instrument or reduce-only policy')
            if ident != client_id or client_id in result['ownership']:
                raise ExecutionError('Send identity must be new and equal cliOrdId')
            result['ownership'][client_id] = {'role': action['role'], 'status': 'pending',
                                               'origin_operation_id': ident}
        elif client_id not in result['ownership'] or result['ownership'][client_id]['role'] != action['role']:
            raise ExecutionError('Mutation has no matching durable owner')
        result['operations'][ident] = {'action': deepcopy(action), 'status': 'prepared'}
    elif kind in ('dispatch', 'response'):
        ident = payload['operation_id']
        operation = result['operations'][ident]
        if kind == 'dispatch':
            if operation['status'] != 'prepared':
                raise ExecutionError('Mutation was already dispatched; never send twice')
            operation['status'] = 'unknown'
        else:
            if operation['status'] != 'unknown':
                raise ExecutionError('Response requires one unresolved dispatch')
            operation['status'] = 'acknowledged'
            operation['response_sha256'] = payload['artifact_sha256']
    elif kind == 'resolve_send':
        ident = payload['operation_id']
        operation = result['operations'][ident]
        action = operation['action']
        if action['endpoint'] != 'sendorder' or operation['status'] not in ('unknown', 'acknowledged'):
            raise ExecutionError('Only dispatched sends can use order-presence resolution')
        if payload['capture_sha256'] != result['latest_capture'] or payload['capture_sha256'] == action['capture_sha256']:
            raise ExecutionError('Resolution requires a new independently verified capture')
        client_id = action['params']['cliOrdId']
        owner = result['ownership'][client_id]
        owner.update(status='open', exchange_order_id=payload['exchange_order_id'])
        operation.update(status='resolved', resolution_sha256=payload['capture_sha256'])
    else:
        raise ExecutionError('Unknown demo journal event')
    return result


class Journal:
    """Use only inside locked(); no public reset, rebind, or manual resolve switch."""
    def __init__(self, root, expected_account, expected_key_fingerprint):
        self.root, self._active = root, True
        self.identity = json.loads((root / 'identity.json').read_bytes())
        expected = {'version': 1, 'environment': 'demo', 'instrument': 'PF_DOTUSD',
                    'account_uid': account_id(expected_account), 'api_key_fingerprint': checked_hash(expected_key_fingerprint)}
        if self.identity != expected:
            raise ExecutionError('Demo journal account/key identity mismatch')
        self._events, self._state = [], {'latest_capture': None, 'operations': {}, 'ownership': {}}
        previous = sha(self.identity)
        for index, path in enumerate(sorted((root / 'events').iterdir()), 1):
            if path.is_symlink() or not path.is_file() or path.name != f'{index:08d}.json':
                raise ExecutionError('Demo journal gap or irregular event')
            event = json.loads(path.read_bytes())
            expected_hash = event.pop('sha256')
            if event['sequence'] != index or event['previous_sha256'] != previous or sha(event) != expected_hash:
                raise ExecutionError('Demo event chain changed')
            self._check_artifact(event)
            self._state = transition(self._state, event)
            previous = expected_hash
            self._events.append({**event, 'sha256': expected_hash})
        if (root / '.head.next').exists() or json.loads((root / 'head.json').read_bytes()) != {'sequence': len(self._events), 'sha256': previous}:
            raise ExecutionError('Demo journal head differs from durable event chain')
        self.tip = previous

    @property
    def state(self):
        return deepcopy(self._state)

    def _require_lock(self):
        if not self._active:
            raise ExecutionError('Demo journal lock is no longer held')

    def artifact(self, value):
        self._require_lock()
        ident = sha(value)
        path = self.root / 'artifacts' / (ident + '.json')
        raw = _json_bytes(value)
        if path.exists():
            if path.is_symlink() or path.read_bytes() != raw:
                raise ExecutionError('Immutable demo artifact changed')
        else:
            _create(path, raw)
        return ident

    def _read_artifact(self, ident):
        checked_hash(ident)
        path = self.root / 'artifacts' / (ident + '.json')
        if path.is_symlink(): raise ExecutionError('Demo artifact is a symlink')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != ident:
            raise ExecutionError('Demo artifact hash mismatch')
        return json.loads(raw)

    def _check_artifact(self, event):
        payload = event['payload']
        if 'artifact_sha256' in payload:
            artifact = self._read_artifact(payload['artifact_sha256'])
            if event['type'] == 'capture':
                if (artifact['account_uid'] != self.identity['account_uid'] or
                        artifact['api_key_fingerprint'] != self.identity['api_key_fingerprint']):
                    raise ExecutionError('Capture account/key differs from journal')
                reference = utc(artifact['reference_utc'])
                if self._state['latest_capture']:
                    previous = self._read_artifact(self._state['latest_capture'])
                    if reference < utc(previous['reference_utc']):
                        raise ExecutionError('Capture predates previous observation')
        if event['type'] == 'resolve_send':
            self._validate_presence(payload)

    def _validate_presence(self, payload):
        capture = self._read_artifact(payload['capture_sha256'])
        action = self._state['operations'][payload['operation_id']]['action']
        params = action['params']
        prior = self._read_artifact(action['capture_sha256'])
        if utc(capture['reference_utc']) <= utc(prior['reference_utc']):
            raise ExecutionError('Resolution requires a later observation')
        matches = [row for row in capture['open_orders'] if row.get('cliOrdId') == params['cliOrdId']]
        if len(matches) != 1:
            raise ExecutionError('New readback does not prove unique order presence')
        row = matches[0]
        from exchange_reconciliation import numeric
        if (row['order_id'] != payload['exchange_order_id'] or not row['order_id'] or
                row['symbol'] != params['symbol'] or row['side'] != params['side'] or
                row['reduceOnly'] != params['reduceOnly'] or
                numeric(row['filledSize']) < 0 or numeric(row['unfilledSize']) < 0 or
                numeric(row['filledSize']) + numeric(row['unfilledSize']) != numeric(params['size'])):
            raise ExecutionError('Observed order differs from dispatched intent')
        actual_type = 'stp' if row['orderType'] == 'stop' else row['orderType']
        if actual_type != params['orderType']:
            raise ExecutionError('Observed order type differs from dispatched intent')
        for key in ('limitPrice', 'stopPrice'):
            if key in params and numeric(row[key]) != numeric(params[key]):
                raise ExecutionError('Observed order price differs from dispatched intent')
        if 'triggerSignal' in params and row.get('triggerSignal') != params['triggerSignal']:
            raise ExecutionError('Observed stop trigger differs from intent')

    def append(self, kind, payload):
        self._require_lock()
        event = {'sequence': len(self._events) + 1, 'previous_sha256': self.tip,
                 'type': kind, 'payload': deepcopy(payload)}
        self._check_artifact(event)
        next_state = transition(self._state, event)
        event['sha256'] = sha(event)
        _create(self.root / 'events' / f'{event["sequence"]:08d}.json', _json_bytes(event))
        _create(self.root / '.head.next', _json_bytes({'sequence': event['sequence'], 'sha256': event['sha256']}))
        (self.root / '.head.next').replace(self.root / 'head.json')
        _sync_directory(self.root)
        self._state, self.tip = next_state, event['sha256']
        self._events.append(event)
        return deepcopy(event)

    def capture(self, verified_capture):
        ident = self.artifact(verified_capture)
        self.append('capture', {'artifact_sha256': ident})
        return ident

    def prepare(self, action):
        return self.append('prepare', {'action': action})

    def before_dispatch(self, operation_id):
        return self.append('dispatch', {'operation_id': operation_id})

    def response(self, operation_id, response_evidence):
        ident = self.artifact(response_evidence)
        return self.append('response', {'operation_id': operation_id, 'artifact_sha256': ident})

    def resolve_present_send(self, operation_id, capture_sha256, exchange_order_id):
        return self.append('resolve_send', {'operation_id': operation_id, 'capture_sha256': capture_sha256,
                                            'exchange_order_id': exchange_order_id})
