"""One demo mutation from fresh acquisition, held account lock and verified plans.

Default committed policy disables this path. No live host, retry loop, account
initialization or inference that an acknowledgement means execution.
"""
from datetime import datetime, timezone
import json
from uuid import uuid4

from cycles import utc
from demo_evidence import capture_bundle
from demo_observation import import_observation
from demo_position import acquired
from demo_preflight import entry_preflight, policy
from demo_protection import protection_preview
from exchange_reconciliation import exchange_time
from kraken_execution import DemoAttempts, ExecutionError, MUTATIONS, _sync_directory
from ledger import digest
from order_executor import git


def _directory(parent, name):
    path = parent/name
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ExecutionError('Regular private runner directory required')
    if not path.exists():
        path.mkdir(mode=0o700)
        _sync_directory(parent)
    return path


def dispatch_once(directory, repo, head, forecast_id, store, client, *, protection=False,
                  clock=lambda: datetime.now(timezone.utc)):
    """Acquire, validate, attempt at most one mutation, then acquire readback.

    Caller constructs the fixed-host DemoClient from protected environment keys
    and holds this journal's lock throughout. Captures are create-only below the
    private journal. Unresolved operations refuse even a new forecast/client ID.
    Entry sending also requires verified exchange margin evidence; the current
    preflight does not yet provide it and therefore cannot dispatch entries.
    """
    store._require_lock()
    settings = policy(repo, head)
    if not settings['enabled']:
        raise ExecutionError('Committed demo execution policy is disabled')
    if git(repo, 'rev-parse', 'origin/main').decode().strip() != head:
        raise ExecutionError('Dispatch requires the current fetched main head')
    if client.key_fingerprint != store.identity['api_key_fingerprint']:
        raise ExecutionError('Demo client key differs from journal identity')
    if any(x['status'] != 'resolved' for x in store.state['operations'].values()):
        raise ExecutionError('Recover the unresolved operation before dispatch')
    baseline = acquired(store, store.state['baseline_capture'])
    captures = _directory(store.root, 'captures')
    attempts_root = _directory(store.root, 'attempts')

    def capture():
        path = captures/uuid4().hex
        result = capture_bundle(path, client, store.identity['account_uid'],
                                client.key_fingerprint, baseline['reference_utc'])
        return import_observation(store, path, result['bundle_sha256'])

    capture()
    _, report = store.reconcile_position()
    if not report['quantity_reconciled']:
        raise ExecutionError('Acquired position has unresolved discrepancies')
    reference = clock()
    if protection:
        preflight = protection_preview(directory, repo, head, forecast_id, store, reference)
        action = preflight['next_action']
    else:
        preflight = entry_preflight(directory, repo, head, forecast_id, store, reference)
        if not preflight['entry_checks_passed'] or not preflight['exchange_margin_requirement_verified']:
            raise ExecutionError('Entry checks or exchange margin proof incomplete')
        params = preflight['entry_request']
        action = {'operation_id': params['cliOrdId'], 'endpoint': 'sendorder', 'role': 'ENTRY',
                  'params': params, 'capture_sha256': preflight['capture_sha256'],
                  'plan_sha256': preflight['plan_sha256'], 'trusted_head': head}
    if action is None:
        return {'mode': 'demo', 'mutation_attempted': False, 'result': 'no_action',
                'capture_sha256': store.state['latest_capture']}
    action = {k: v for k, v in action.items() if k != 'authorizes_execution'}
    operation = action['operation_id']
    if (attempts_root/operation).exists() or (attempts_root/operation).is_symlink():
        raise ExecutionError('Retained attempt exists; never dispatch it again')
    source = acquired(store, preflight['capture_sha256'])
    preflight_sha = store.artifact(preflight)
    action['preflight_sha256'] = preflight_sha
    store.prepare(action)

    def barrier(status, raw):
        # Recheck after the transport's last read, immediately before its POST.
        store._require_lock()
        if git(repo, 'rev-parse', 'origin/main').decode().strip() != head:
            raise ExecutionError('Fetched main changed before dispatch')
        at = utc(clock())
        age = (at - utc(source['readback_start_utc'])).total_seconds()
        if not 0 <= age <= settings['max_readback_age_seconds']:
            raise ExecutionError('Account evidence expired before dispatch')
        body = json.loads(raw, parse_float=str)
        stamp = exchange_time(body['serverTime'])
        if not utc(source['readback_end_utc']) <= stamp <= at:
            raise ExecutionError('Final open-order read has an invalid server time')
        if sorted(body['openOrders'], key=digest) != sorted(source['open_orders'], key=digest):
            raise ExecutionError('Open orders changed after reconciliation')
        if not protection and at >= utc(preflight['entry_deadline_utc']):
            raise ExecutionError('Entry expired before dispatch')
        # After this durable barrier even a crash before the socket is unknown.
        store.before_dispatch(operation)

    response = None
    try:
        response = DemoAttempts(attempts_root, client).mutate(operation, action['endpoint'],
                                                            action['params'], before_send=barrier)
    except (ExecutionError, KeyError, ValueError):
        # Raw attempt files remain. Never resend, infer rejection, or reset.
        pass
    if response is not None:
        metadata = json.loads((attempts_root/operation/'response.json').read_bytes())
        store.response(operation, {'attempt_relative_path': 'attempts/'+operation,
                                  'response_metadata': metadata,
                                  'assessment': response[MUTATIONS[action['endpoint']]]})
    dispatched = store.state['operations'][operation]['status'] != 'prepared'
    readback = None
    if dispatched:
        try:
            readback = capture()
        except (ExecutionError, KeyError, ValueError):
            # A missing/overlapping readback leaves the operation unresolved.
            # Its incomplete raw directory remains available for diagnosis.
            pass
    return {'mode': 'demo', 'operation_id': operation, 'mutation_attempted': dispatched,
            'operation_status': store.state['operations'][operation]['status'],
            'preflight_sha256': preflight_sha, 'post_capture_sha256': readback,
            'recovery_required': True, 'fill_verified': False}
