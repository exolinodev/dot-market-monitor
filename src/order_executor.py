"""Load executable evidence from trusted Git and run isolated paper execution."""
import gzip
from datetime import timedelta
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from cycles import utc
from ledger import digest
from ledger_publication import instruction
from ledger_runtime import advance
from ledger_store import verify
from oracle_v4 import verify_forecast_plan
from kraken_execution import ExecutionError, entry_request


def git(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, stderr=subprocess.DEVNULL)


def materialize(repo, head, destination):
    """Exact Git blobs only; never trust local market/account/forecast edits."""
    if not re.fullmatch(r'[a-f0-9]{40}', head):
        raise ExecutionError('Full immutable trusted main SHA required')
    git(repo, 'merge-base', '--is-ancestor', head, 'origin/main')
    root = Path(destination)
    inventory = {}
    for row in git(repo, 'ls-tree', '-r', '-z', head, '--', 'data/').split(b'\0'):
        if not row: continue
        descriptor, raw_name = row.split(b'\t', 1)
        name = raw_name.decode()
        mode, kind, sha = descriptor.decode().split()
        inventory[name] = (mode, kind, sha)

    def copy(name):
        if name not in inventory:
            raise ExecutionError('Missing committed execution evidence: ' + name)
        mode, kind, sha = inventory[name]
        if mode != '100644' or kind != 'blob' or '..' in PurePosixPath(name).parts:
            raise ExecutionError('Execution evidence must be regular committed files')
        raw = git(repo, 'cat-file', 'blob', sha)
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return raw

    for name in inventory:
        if name.startswith(('data/ledger/', 'data/intraday/', 'data/funding/')):
            copy(name)
    # All persisted v4 plans are eligible runtime inputs, not merely the plan
    # chosen for CLI reporting. Each needs its original snapshot/forecast.
    for name in inventory:
        if name.startswith('data/ledger/plans/') and name.endswith('.json'):
            plan = json.loads((root / name).read_bytes())
            ident = plan['forecast_id']
            if not re.fullmatch(r'[A-Za-z0-9_-]+-oracle-v4', ident):
                raise ExecutionError('Executor requires published v4 plans')
            forecast_path = 'data/oracle/forecasts/' + utc(plan['created_at_utc']).strftime('%Y/%m/%d/') + ident + '.json'
            forecast = json.loads(copy(forecast_path))
            snapshot_hash = forecast['snapshot_sha256']
            if not re.fullmatch(r'[a-f0-9]{64}', snapshot_hash):
                raise ExecutionError('Invalid snapshot identity')
            copy('data/oracle/inputs/' + snapshot_hash + '.json.gz')
    return root / 'data'


def load_published(directory, repo, head, forecast_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]+-oracle-v4', forecast_id):
        raise ExecutionError('Published v4 forecast ID required')
    root = Path(directory)
    current = verify(root)
    from ledger_market import verify_market_input
    from ledger_publication import verify_instruction
    source_cache = {}
    for record in current['records']:
        event = record['input']
        if event['type'] == 'instruction':
            verify_instruction(repo, head, event)
        else:
            verify_market_input(root, current['genesis']['epoch_utc'], event, source_cache)
    plan = json.loads((root / 'ledger/plans' / (forecast_id + '.json')).read_bytes())
    path = root / 'oracle/forecasts' / utc(plan['created_at_utc']).strftime('%Y/%m/%d') / (forecast_id + '.json')
    forecast = json.loads(path.read_bytes())
    snapshot = json.loads(gzip.decompress((root / 'oracle/inputs' / (forecast['snapshot_sha256'] + '.json.gz')).read_bytes()))
    if verify_forecast_plan(forecast, snapshot, root) != plan:
        raise ExecutionError('Executable plan differs from its published forecast')
    event = instruction(repo, head, plan)
    return {'forecast': forecast, 'plan': plan, 'instruction': event,
            'config': current['genesis']['config'], 'state': current['state'], 'snapshot': snapshot}


def preview(directory, repo, head, forecast_id, reference):
    bound = load_published(directory, repo, head, forecast_id)
    plan, event = bound['plan'], bound['instruction']
    at = utc(reference)
    eligible = at >= utc(event['at_utc'])
    request = entry_request(plan, bound['config']) if plan['orders'] else None
    expired = bool(plan['orders'] and at >= min(utc(plan['orders'][0]['order']['valid_until_utc']),
        utc(event['publication']['at_utc']) + timedelta(minutes=bound['config']['max_order_age_minutes'])))
    return {'mode': 'demo', 'preview_only': True, 'trusted_head': head, 'forecast_id': forecast_id,
            'plan_sha256': digest(plan), 'publication': event['publication'],
            'effective_at_utc': event['at_utc'], 'eligible_time': eligible, 'entry_expired': expired,
            'entry_request': request, 'management': plan['management'],
            'authorizes_execution': False}


def run_paper(repo, head, forecast_id, boundary, output):
    """Create an isolated replayable candidate tree; never publish/reset an account."""
    point = utc(boundary)
    if point.second or point.microsecond or point.minute % 15:
        raise ExecutionError('Paper boundary must align to a UTC quarter')
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists() or output.is_symlink():
        raise ExecutionError('Paper output must be a new directory')
    with tempfile.TemporaryDirectory(prefix='.oracle-paper-executor-', dir=output.parent) as temp:
        stage = Path(temp) / 'candidate'
        data = materialize(repo, head, stage)
        bound = load_published(data, repo, head, forecast_id)
        summary = advance(data, repo, head, boundary)
        result = verify(data)
        report = {'mode': 'paper', 'trusted_head': head, 'forecast_id': forecast_id,
                  'plan_sha256': digest(bound['plan']), 'publication': bound['instruction']['publication'],
                  'summary': summary, 'replay': 'identical', 'trade_count': len(result['trades']),
                  'exchange_calls': 0, 'published': False}
        (stage / 'execution_report.json').write_text(json.dumps(report, indent=2) + '\n')
        # Exclusive destination reservation avoids replacing any existing account.
        output.mkdir()
        # An interrupted output is deliberately not reusable/overwritten.
        for path in stage.iterdir():
            path.rename(output / path.name)
        return report
