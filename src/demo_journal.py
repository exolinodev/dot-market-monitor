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
    if kind != 'reconciliation' and 'latest_reconciliation' in result:
        result['latest_reconciliation'] = None
    if kind == 'capture':
        result['latest_capture'] = payload['artifact_sha256']
    elif kind == 'baseline':
        if result.get('baseline_capture') is not None or result['operations'] or result['ownership']:
            raise ExecutionError('Baseline cannot be reset after operations')
        if payload['capture_sha256'] != result['latest_capture']:
            raise ExecutionError('Baseline must bind latest capture')
        result['baseline_capture'] = payload['capture_sha256']
    elif kind == 'reconciliation':
        result['latest_reconciliation'] = payload['artifact_sha256']
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
    elif kind == 'order_filled':
        client_id = payload['client_id']
        owner = result['ownership'][client_id]
        if owner['status'] == 'terminal':
            raise ExecutionError('Order is already terminal')
        if payload['capture_sha256'] != result['latest_capture']:
            raise ExecutionError('Filled-order resolution requires the latest capture')
        owner.update(status='terminal', exchange_order_id=payload['exchange_order_id'],
                     terminal_reason='filled', resolution_sha256=payload['capture_sha256'])
        for operation in result['operations'].values():
            action = operation['action']
            if action['params']['cliOrdId'] == client_id and operation['status'] != 'resolved':
                operation.update(status='resolved', resolution_sha256=payload['capture_sha256'],
                                 outcome='filled' if action['endpoint'] == 'sendorder' else 'order_already_filled')
    elif kind in ('order_terminal', 'trigger_cancelled'):
        owner = result['ownership'][payload['client_id']]
        if owner['status'] == 'terminal':
            raise ExecutionError('Order is already terminal')
        if payload['capture_sha256'] != result['latest_capture']:
            raise ExecutionError('Terminal resolution requires the latest capture')
        owner.update(status='terminal', exchange_order_id=payload['exchange_order_id'],
                     terminal_reason=payload['reason'], resolution_sha256=payload['capture_sha256'])
        for operation in result['operations'].values():
            if operation['action']['params']['cliOrdId'] == payload['client_id'] and operation['status'] != 'resolved':
                operation.update(status='resolved', resolution_sha256=payload['capture_sha256'], outcome='order_terminal')
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
                if 'bundle_sha256' in artifact:
                    checked_hash(artifact['bundle_sha256'])
                    from demo_observation import verify_journal_observation
                    verify_journal_observation(self, artifact)
                reference = utc(artifact['reference_utc'])
                if self._state['latest_capture']:
                    previous = self._read_artifact(self._state['latest_capture'])
                    if reference < utc(previous['reference_utc']):
                        raise ExecutionError('Capture predates previous observation')
        if event['type'] == 'baseline':
            from demo_position import validate_baseline
            validate_baseline(self, payload['capture_sha256'])
        if event['type'] == 'reconciliation':
            from demo_position import position_report
            if self._read_artifact(payload['artifact_sha256']) != position_report(self):
                raise ExecutionError('Reconciliation differs from journal and acquired evidence')
        if event['type'] == 'resolve_send':
            self._validate_presence(payload)
        if event['type'] == 'order_filled':
            self._validate_full_fill(payload)
        if event['type'] == 'order_terminal':
            self._validate_terminal(payload)
        if event['type'] == 'trigger_cancelled':
            self._validate_trigger_cancelled(payload)

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


    def _validate_full_fill(self, payload):
        from decimal import Decimal, localcontext
        from exchange_reconciliation import deduplicate_fills, numeric
        client_id = payload['client_id']
        owner = self._state['ownership'][client_id]
        origin = self._state['operations'][owner['origin_operation_id']]
        if origin['status'] == 'prepared':
            raise ExecutionError('Never-dispatched intent cannot have exchange fills')
        action = origin['action']
        params = action['params']
        # An edit can change the order's total quantity. Until that effective
        # contract is independently reconstructed, original send size is not
        # sufficient evidence of completion (including an in-flight edit).
        if any(o['action']['params']['cliOrdId'] == client_id and o['action']['endpoint'] == 'editorder'
               for o in self._state['operations'].values()):
            raise ExecutionError('Edited order needs effective-size recovery evidence')
        capture = self._read_artifact(payload['capture_sha256'])
        history = self._read_artifact(payload['history_sha256'])
        prior = self._read_artifact(action['capture_sha256'])
        if capture.get('execution_history_sha256') != payload['history_sha256']:
            raise ExecutionError('History is not bound to the observation')
        if (history.get('source') != 'execution_history' or history.get('environment') != 'demo'
                or history.get('account_uid') != self.identity['account_uid']
                or history.get('coverage_complete') is not True):
            raise ExecutionError('Complete account-bound execution history required')
        start, end = utc(prior['reference_utc']), utc(capture['reference_utc'])
        if end <= start or utc(history['since_utc']) > start or utc(history['through_utc']) < end:
            raise ExecutionError('Execution history does not cover the send lifetime')
        exchange_id = payload['exchange_order_id']
        if not isinstance(exchange_id, str) or not exchange_id:
            raise ExecutionError('Exchange order identity required')
        if owner.get('exchange_order_id') and owner['exchange_order_id'] != exchange_id:
            raise ExecutionError('Filled order differs from known exchange identity')
        if any(row.get('cliOrdId') == client_id or row['order_id'] == exchange_id for row in capture['open_orders']):
            raise ExecutionError('Claimed filled order is still open')
        with localcontext() as context:
            context.prec = 34
            total = Decimal(0)
            identity_linked = owner.get('exchange_order_id') == exchange_id
            for fill in deduplicate_fills(history['fills']):
                if fill['cliOrdId'] != client_id and fill['order_id'] != exchange_id:
                    continue
                if (fill['order_id'] != exchange_id or fill['cliOrdId'] not in (None, client_id)
                        or fill['symbol'] != params['symbol'] or fill['side'] != params['side']):
                    raise ExecutionError('Fill identity or economics differ from intent')
                if not start <= utc(fill['fillTime']) <= end:
                    raise ExecutionError('Matching fill outside observed send lifetime')
                identity_linked = identity_linked or fill['cliOrdId'] == client_id
                total += numeric(fill['size'], positive=True)
            if not identity_linked and total:
                raise ExecutionError('No proven link between client and exchange order IDs')
            if total != numeric(params['size'], positive=True):
                raise ExecutionError('Actual fills do not equal dispatched total quantity')

    def resolve_filled_order(self, client_id, capture_sha256, history_sha256, exchange_order_id):
        return self.append('order_filled', {'client_id': client_id, 'capture_sha256': capture_sha256,
                           'history_sha256': history_sha256, 'exchange_order_id': exchange_order_id})


    def _validate_terminal(self, payload):
        from decimal import Decimal, localcontext
        from exchange_reconciliation import numeric, deduplicate_fills
        client_id = payload['client_id']
        owner = self._state['ownership'][client_id]
        origin = self._state['operations'][owner['origin_operation_id']]
        if origin['status'] == 'prepared':
            raise ExecutionError('Terminal history cannot resolve a never-dispatched send')
        params = origin['action']['params']
        if params['orderType'] == 'stp':
            raise ExecutionError('Stop order lifecycle requires trigger-history evidence')
        capture = self._read_artifact(payload['capture_sha256'])
        history = self._read_artifact(payload['history_sha256'])
        prior = self._read_artifact(origin['action']['capture_sha256'])
        start, end = utc(prior['reference_utc']), utc(capture['reference_utc'])
        if capture.get('order_history_sha256') != payload['history_sha256'] or end <= start:
            raise ExecutionError('Terminal history is not bound to a later observation')
        if (history.get('source') != 'order_history' or history.get('environment') != 'demo'
                or history.get('account_uid') != self.identity['account_uid'] or history.get('coverage_complete') is not True
                or utc(history['since_utc']) > start or utc(history['through_utc']) < end):
            raise ExecutionError('Complete account-bound order lifetime required')
        candidates = [e for e in history['order_events'] if e['event_id'] == payload['event_id']]
        if len(candidates) != 1:
            raise ExecutionError('Unique terminal event required')
        event = candidates[0]
        if event['kind'] not in ('OrderCancelled', 'OrderRejected') or payload['reason'] != {'OrderCancelled': 'cancelled', 'OrderRejected': 'rejected'}[event['kind']]:
            raise ExecutionError('Event does not prove order termination')
        order = event['order']
        exchange_id = payload['exchange_order_id']
        if (order['account_uid'] != self.identity['account_uid'] or order['order_id'] != exchange_id
                or not exchange_id or (owner.get('exchange_order_id') and owner['exchange_order_id'] != exchange_id)
                or (order['client_id'] != client_id and not (order['client_id'] is None and owner.get('exchange_order_id') == exchange_id))):
            raise ExecutionError('Terminal order identity differs from dispatched intent')
        allowed_types = {'lmt': ('Limit',), 'mkt': ('Market', 'IoC')}
        if (order['symbol'] != params['symbol'] or order['direction'] != ('Buy' if params['side'] == 'buy' else 'Sell')
                or order['reduce_only'] != params['reduceOnly'] or order['order_type'] not in allowed_types[params['orderType']]):
            raise ExecutionError('Terminal order economics differ from intent')
        at = utc(event['at_utc'])
        if not start <= at <= end:
            raise ExecutionError('Terminal event outside send lifetime')
        for other in history['order_events']:
            other_order = other.get('order', {})
            if (other['event_id'] != event['event_id'] and other_order.get('order_id') == exchange_id
                    and utc(other['at_utc']) >= at):
                raise ExecutionError('Terminal event is superseded or chronologically ambiguous')
        if any(r.get('cliOrdId') == client_id or r['order_id'] == exchange_id for r in capture['open_orders']):
            raise ExecutionError('Terminated order remains open in readback')
        with localcontext() as context:
            context.prec = 34
            maximum = numeric(params['size'], positive=True)
            for operation in self._state['operations'].values():
                action = operation['action']
                if (action['params']['cliOrdId'] == client_id and action['endpoint'] == 'editorder'
                        and operation['status'] != 'prepared' and 'size' in action['params']):
                    maximum = max(maximum, numeric(action['params']['size'], positive=True))
            quantity, filled = numeric(order['quantity']), numeric(order['filled'])
            if quantity < 0 or quantity > maximum or filled < 0 or filled > quantity:
                raise ExecutionError('Terminal quantity exceeds persisted authorization')
            execution = self._read_artifact(capture['execution_history_sha256'])
            if (execution.get('source') != 'execution_history' or execution.get('environment') != 'demo'
                    or execution.get('account_uid') != self.identity['account_uid'] or execution.get('coverage_complete') is not True
                    or utc(execution['since_utc']) > start or utc(execution['through_utc']) < end):
                raise ExecutionError('Terminal recovery requires complete fill evidence too')
            total = Decimal(0)
            for fill in deduplicate_fills(execution['fills']):
                if fill['order_id'] != exchange_id and fill['cliOrdId'] != client_id: continue
                if (fill['order_id'] != exchange_id or fill['cliOrdId'] not in (None, client_id)
                        or fill['symbol'] != params['symbol'] or fill['side'] != params['side']
                        or not start <= utc(fill['fillTime']) <= at):
                    raise ExecutionError('Terminal fill contradicts order history')
                total += numeric(fill['size'], positive=True)
            if total != filled:
                raise ExecutionError('Terminal filled quantity differs from actual fills')

    def resolve_terminal_order(self, client_id, capture_sha256, history_sha256, event_id, exchange_order_id, reason):
        return self.append('order_terminal', {'client_id': client_id, 'capture_sha256': capture_sha256,
                           'history_sha256': history_sha256, 'event_id': event_id,
                           'exchange_order_id': exchange_order_id, 'reason': reason})


    def _validate_trigger_cancelled(self, payload):
        from exchange_reconciliation import numeric, deduplicate_fills
        client_id = payload['client_id']
        owner = self._state['ownership'][client_id]
        origin = self._state['operations'][owner['origin_operation_id']]
        params = origin['action']['params']
        if origin['status'] == 'prepared' or params['orderType'] != 'stp' or payload['reason'] != 'cancelled':
            raise ExecutionError('Only dispatched stop triggers can use cancellation recovery')
        capture = self._read_artifact(payload['capture_sha256'])
        history = self._read_artifact(payload['history_sha256'])
        prior = self._read_artifact(origin['action']['capture_sha256'])
        start, end = utc(prior['reference_utc']), utc(capture['reference_utc'])
        if capture.get('trigger_history_sha256') != payload['history_sha256'] or end <= start:
            raise ExecutionError('Trigger history is not bound to a later observation')
        if (history.get('source') != 'trigger_history' or history.get('environment') != 'demo'
                or history.get('account_uid') != self.identity['account_uid'] or history.get('coverage_complete') is not True
                or utc(history['since_utc']) > start or utc(history['through_utc']) < end):
            raise ExecutionError('Complete account-bound trigger lifetime required')
        candidates = [e for e in history['trigger_events'] if e['event_id'] == payload['event_id']]
        if len(candidates) != 1 or candidates[0]['kind'] != 'OrderTriggerCancelled':
            raise ExecutionError('Explicit trigger cancellation event required')
        event = candidates[0]; order = event['order']; exchange_id = payload['exchange_order_id']
        if (not exchange_id or order['order_id'] != exchange_id or order['account_uid'] != self.identity['account_uid']
                or (owner.get('exchange_order_id') and owner['exchange_order_id'] != exchange_id)
                or (order['client_id'] != client_id and not (order['client_id'] is None and owner.get('exchange_order_id') == exchange_id))):
            raise ExecutionError('Cancelled trigger identity differs from dispatched intent')
        side = 'Buy' if params['side'] == 'buy' else 'Sell'
        if (order['symbol'] != params['symbol'] or order['direction'] != side or order['reduce_only'] != params['reduceOnly']
                or order['trigger_signal'] != 'MarkPrice' or order['trigger_side'] != ('Above' if side == 'Buy' else 'Below')):
            raise ExecutionError('Cancelled trigger policy differs from intent')
        maximum = numeric(params['size'], positive=True)
        prices = {numeric(params['stopPrice'], positive=True)}
        for operation in self._state['operations'].values():
            action = operation['action']
            if action['params']['cliOrdId'] == client_id and action['endpoint'] == 'editorder' and operation['status'] != 'prepared':
                if 'size' in action['params']: maximum = max(maximum, numeric(action['params']['size'], positive=True))
                if 'stopPrice' in action['params']: prices.add(numeric(action['params']['stopPrice'], positive=True))
        if not 0 <= numeric(order['quantity']) <= maximum or numeric(order['trigger_price']) not in prices:
            raise ExecutionError('Cancelled trigger differs from authorized size/stop price')
        at = utc(event['at_utc'])
        if not start <= at <= end:
            raise ExecutionError('Trigger cancellation outside send lifetime')
        for other in history['trigger_events']:
            item = other.get('order', {})
            if item.get('order_id') != exchange_id and item.get('client_id') != client_id: continue
            if other['kind'] == 'OrderTriggerActivated':
                raise ExecutionError('Activated trigger requires child-order and fill reconciliation')
            if other['event_id'] != event['event_id'] and utc(other['at_utc']) >= at:
                raise ExecutionError('Trigger cancellation is superseded or ambiguous')
        if any(r.get('cliOrdId') == client_id or r['order_id'] == exchange_id for r in capture['open_orders']):
            raise ExecutionError('Cancelled trigger remains open')
        execution = self._read_artifact(capture['execution_history_sha256'])
        if (execution.get('source') != 'execution_history' or execution.get('environment') != 'demo'
                or execution.get('account_uid') != self.identity['account_uid'] or execution.get('coverage_complete') is not True
                or utc(execution['since_utc']) > start or utc(execution['through_utc']) < end):
            raise ExecutionError('Trigger cancellation requires complete execution evidence')
        if any(f['order_id'] == exchange_id or f['cliOrdId'] == client_id for f in deduplicate_fills(execution['fills'])):
            raise ExecutionError('Trigger has executions; reconcile activated order instead')

    def resolve_cancelled_trigger(self, client_id, capture_sha256, history_sha256, event_id, exchange_order_id):
        return self.append('trigger_cancelled', {'client_id': client_id, 'capture_sha256': capture_sha256,
                           'history_sha256': history_sha256, 'event_id': event_id,
                           'exchange_order_id': exchange_order_id, 'reason': 'cancelled'})

    def establish_flat_baseline(self):
        return self.append('baseline', {'capture_sha256': self._state['latest_capture']})

    def reconcile_position(self):
        from demo_position import position_report
        report = position_report(self)
        ident = self.artifact(report)
        self.append('reconciliation', {'artifact_sha256': ident})
        return ident, report
