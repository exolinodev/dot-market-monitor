"""Private, read-only demo evidence bundles; no reconciliation or send authority."""
import hashlib
import json
import os
from pathlib import Path
import re

from exchange_history import account_id, capture_executions, capture_orders, capture_triggers, verify_capture, milliseconds
from kraken_execution import READS, ExecutionError, _create, _json_bytes, _response, _sync_directory, capture_readback

HISTORIES = {'executions': (capture_executions, 'execution_history'),
             'orders': (capture_orders, 'order_history'),
             'triggers': (capture_triggers, 'trigger_history')}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def inventory(root):
    result = {}
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ExecutionError('Symlink in demo evidence')
        if path.is_dir():
            continue
        if not path.is_file():
            raise ExecutionError('Nonregular demo evidence')
        if path == root / 'bundle.json':
            continue
        result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def capture_bundle(directory, client, expected_account, key_fingerprint, since, through=None, *, max_pages=100):
    """Caller supplies one authenticated client for every read in this bundle.

    Fingerprint provenance is established by the CLI constructing that client,
    not by the fingerprint string or a locally replayable hash on its own.
    """
    expected_account = account_id(expected_account)
    if not isinstance(key_fingerprint, str) or not re.fullmatch('[a-f0-9]{64}', key_fingerprint):
        raise ExecutionError('API key fingerprint required')
    start = milliseconds(since)
    end = milliseconds(through) if through is not None else start
    if start < 1 or start > end or type(max_pages) is not int or not 1 <= max_pages <= 100:
        raise ExecutionError('Invalid demo capture window/page budget (1–100)')
    root = Path(directory)
    old_mask = os.umask(0o077)
    try:
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        _sync_directory(root.parent)
        capture_readback(root / 'readback', client)
        if through is None:
            from datetime import timedelta
            from cycles import iso
            from exchange_reconciliation import exchange_time
            latest = max(exchange_time(json.loads((root/'readback'/f'{name}.raw').read_bytes())['serverTime']) for name in READS)
            if latest.microsecond % 1000:
                latest += timedelta(microseconds=1000 - latest.microsecond % 1000)
            through = iso(latest)
            if milliseconds(through) < start:
                raise ExecutionError('Readback predates requested history start')
        complete = True
        for name, (capture, _) in HISTORIES.items():
            report = capture(root / name, client, expected_account, since, through, max_pages=max_pages)
            complete = complete and report['coverage_complete']
        manifest = {'version': 1, 'environment': 'demo', 'account_uid': expected_account,
                    'api_key_fingerprint': key_fingerprint, 'since_utc': since, 'through_utc': through,
                    'history_coverage_complete': complete, 'atomic_snapshot': False,
                    'authorizes_execution': False, 'files': inventory(root)}
        raw = _json_bytes(manifest)
        _create(root / 'bundle.json', raw)
        verify_bundle(root, expected_account, key_fingerprint, digest(raw))
        return {'bundle_sha256': digest(raw), 'history_coverage_complete': complete,
                'authorizes_execution': False}
    finally:
        os.umask(old_mask)


def verify_bundle(directory, expected_account, key_fingerprint, expected_sha256):
    """Replay local integrity against a separately retained acquisition hash.

    This cannot authenticate fabricated local evidence, establish atomicity, or
    turn a capture with stale/noncontemporaneous readbacks into trading approval.
    """
    root = Path(directory)
    if root.is_symlink() or not root.is_dir() or (root / 'bundle.json').is_symlink():
        raise ExecutionError('Regular demo bundle required')
    raw = (root / 'bundle.json').read_bytes()
    if digest(raw) != expected_sha256:
        raise ExecutionError('Demo bundle hash mismatch')
    manifest = json.loads(raw)
    if (manifest['version'] != 1 or manifest['environment'] != 'demo'
            or manifest['account_uid'] != account_id(expected_account)
            or manifest['api_key_fingerprint'] != key_fingerprint
            or manifest['authorizes_execution'] is not False or manifest['atomic_snapshot'] is not False):
        raise ExecutionError('Demo bundle identity/policy mismatch')
    if inventory(root) != manifest['files']:
        raise ExecutionError('Demo bundle files changed')
    if {p.name for p in root.iterdir()} != {'bundle.json', 'readback', *HISTORIES}:
        raise ExecutionError('Unexpected demo bundle artifacts')
    readback = root / 'readback'
    expected_names = {'manifest.json'} | {name + ext for name in READS for ext in ('.raw', '.json')}
    if {p.name for p in readback.iterdir()} != expected_names:
        raise ExecutionError('Unexpected readback artifacts')
    metadata = {}
    for endpoint, field in READS.items():
        response = (readback / (endpoint + '.raw')).read_bytes()
        item = json.loads((readback / (endpoint + '.json')).read_bytes())
        if item['sha256'] != digest(response) or item['bytes'] != len(response):
            raise ExecutionError('Readback evidence changed')
        value = _response(item['http_status'], response, field)
        if not isinstance(value[field], dict if endpoint == 'accounts' else list):
            raise ExecutionError('Malformed readback collection')
        metadata[endpoint] = item
    if json.loads((readback / 'manifest.json').read_bytes()) != {'version': 1, 'environment': 'demo', 'responses': metadata}:
        raise ExecutionError('Readback manifest differs from raw evidence')
    complete = True
    for name, (_, source) in HISTORIES.items():
        report = verify_capture(root / name, expected_account, manifest['since_utc'], manifest['through_utc'], source=source)
        complete = complete and report['coverage_complete']
    if complete is not manifest['history_coverage_complete']:
        raise ExecutionError('Bundle coverage differs from history')
    return manifest
