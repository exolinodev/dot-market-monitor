"""Import verified acquisition bundles into a locked demo journal, never send."""
import json
import os
from pathlib import Path
import shutil

from cycles import iso, utc
from demo_evidence import HISTORIES, verify_bundle
from exchange_reconciliation import exchange_time
from kraken_execution import READS, ExecutionError, _sync_directory


def observation(directory, expected_account, key_fingerprint, bundle_sha256):
    """Require a quiet, fully covered readback interval, without claiming atomicity."""
    root = Path(directory)
    manifest = verify_bundle(root, expected_account, key_fingerprint, bundle_sha256)
    if not manifest['history_coverage_complete']:
        raise ExecutionError('Incomplete history cannot become a journal observation')
    responses = {name: json.loads((root/'readback'/f'{name}.raw').read_bytes(), parse_float=str)
                 for name in READS}
    times = [exchange_time(value['serverTime']) for value in responses.values()]
    earliest, latest = min(times), max(times)
    start, end = utc(manifest['since_utc']), utc(manifest['through_utc'])
    if not start <= earliest <= latest <= end:
        raise ExecutionError('History does not cover all readback times')
    histories = {name: json.loads((root/name/'manifest.json').read_bytes()) for name in HISTORIES}
    events = [utc(fill['fillTime']) for fill in histories['executions']['fills']]
    events += [utc(event['at_utc']) for name in ('orders', 'triggers')
               for event in histories[name][name[:-1] + '_events']]
    # Any account activity during collection makes absence/quantity comparisons
    # unsafe; require a later acquisition rather than infer an ordering.
    if any(earliest <= stamp <= end for stamp in events):
        raise ExecutionError('Account activity overlaps readbacks; acquire a later observation')
    return {'account_uid': expected_account, 'api_key_fingerprint': key_fingerprint,
            'reference_utc': iso(end), 'readback_start_utc': iso(earliest),
            'readback_end_utc': iso(latest), 'open_orders': responses['openorders']['openOrders'],
            'positions': responses['openpositions']['openPositions'], 'accounts': responses['accounts']['accounts'],
            'bundle_sha256': bundle_sha256, 'quantity_reconciled': False,
            'authorizes_execution': False}, histories


def import_observation(store, directory, bundle_sha256):
    """Caller holds the journal lock across authenticated acquisition and import.

    The hash must come from that acquisition, not from a caller-edited manifest.
    Retain raw evidence in the journal so later replay rechecks the same source.
    """
    store._require_lock()
    identity = store.identity
    value, histories = observation(directory, identity['account_uid'], identity['api_key_fingerprint'], bundle_sha256)
    parent = store.root/'evidence'
    source_path, destination_path = Path(directory).resolve(), (parent/bundle_sha256).resolve()
    if (source_path == destination_path or source_path in destination_path.parents
            or destination_path in source_path.parents):
        raise ExecutionError('Acquisition and journal evidence paths must not overlap')
    if parent.is_symlink():
        raise ExecutionError('Journal evidence directory is a symlink')
    if not parent.exists():
        parent.mkdir(mode=0o700)
        _sync_directory(store.root)
    destination = parent/bundle_sha256
    if not destination.exists():
        shutil.copytree(directory, destination, symlinks=True)
        for path in destination.rglob('*'):
            if path.is_file() and not path.is_symlink():
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
        for path in sorted((p for p in destination.rglob('*') if p.is_dir() and not p.is_symlink()), reverse=True):
            _sync_directory(path)
        _sync_directory(destination)
        _sync_directory(parent)
    # Revalidate the copied source before any journal state can refer to it.
    copied, copied_histories = observation(destination, identity['account_uid'], identity['api_key_fingerprint'], bundle_sha256)
    if copied != value or copied_histories != histories:
        raise ExecutionError('Acquisition changed during import')
    for name, (_, source) in HISTORIES.items():
        value[source + '_sha256'] = store.artifact(histories[name])
    return store.capture(value)


def verify_journal_observation(store, value):
    if (store.root/'evidence').is_symlink():
        raise ExecutionError('Journal evidence directory is a symlink')
    rebuilt, histories = observation(store.root/'evidence'/value['bundle_sha256'],
                                    store.identity['account_uid'], store.identity['api_key_fingerprint'], value['bundle_sha256'])
    from demo_journal import sha
    for name, (_, source) in HISTORIES.items():
        ident = sha(histories[name])
        if store._read_artifact(ident) != histories[name]:
            raise ExecutionError('Journal history differs from acquired evidence')
        rebuilt[source + '_sha256'] = ident
    if rebuilt != value:
        raise ExecutionError('Journal observation differs from acquired evidence')
