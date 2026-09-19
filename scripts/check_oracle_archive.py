#!/usr/bin/env python3
"""CI guard: immutable audit artifacts may only be added, never changed or removed."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from oracle_context import load_forecasts
from oracle_common import validate
from oracle_evaluator import verify_outcome
from oracle_forecasts import snapshot_strategy_key
from oracle_common import digest
from observation_common import utc
from oracle_submission import parse_submission
from oracle_receipts import verify_receipt

PATHS=['data/oracle/'+p for p in ('submissions','forecast_keys','forecasts','inputs','outcomes','outcome_inputs','receipts')]


def check_changes(changes, repo):
    for line in changes.splitlines():
        status,path=line.split('\t',1)
        if status!='A': raise ValueError('Immutable Oracle artifact modified or removed: '+path)


def check_additions(paths, repo):
    import re
    for path in paths:
        target=repo/path
        if target.is_symlink() or not target.is_file():
            raise ValueError('Archive artifacts must be regular files: '+path)
        if path.startswith('data/oracle/submissions/'):
            if not re.fullmatch(r'data/oracle/submissions/[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-oracle-v3\.json',path):
                raise ValueError('Invalid submission path: '+path)
            envelope=parse_submission(target.read_bytes())
            validate(envelope,'oracle_submission.schema.json')
            if target.stem!=envelope['forecast']['forecast_id']:
                raise ValueError('Submission filename mismatch')
            from oracle_forecasts import validate_forecast
            snapshot=json.loads(subprocess.check_output(['git','show',envelope['snapshot_commit']+':data/llm_snapshot.json'],cwd=repo))
            validate_forecast(envelope['forecast'],snapshot)


def check(base=None,head='HEAD',repo=Path('.'),staged=False):
    if staged:
        args=['git','diff','--cached','--no-renames','--name-status','--',*PATHS]
    else:
        if not base: raise ValueError('A trusted base commit is required')
        commits=subprocess.check_output(['git','rev-list',base+'..'+head],cwd=repo,text=True).split()
        for commit in commits:
            changes=subprocess.check_output(['git','diff','--no-renames','--name-status',commit+'^',commit,'--',*PATHS],cwd=repo,text=True)
            check_changes(changes,repo)
        args=['git','diff','--no-renames','--name-status',base,head,'--',*PATHS]
    changes=subprocess.check_output(args,cwd=repo,text=True)
    check_changes(changes,repo)
    check_additions([line.split('\t',1)[1] for line in changes.splitlines()],repo)
    # Validate all forecast bindings as well as the proposed additions.
    forecasts={f['forecast_id']:f for f in load_forecasts(repo/'data','2260-01-01T00:00:00Z')}
    for path in (repo/'data/oracle/forecast_keys').glob('*.json'):
        key=json.loads(path.read_text())
        f=forecasts.get(key['forecast_id'])
        if f is None: raise ValueError('Snapshot reservation has no published forecast')
        expected={'schema_version':1,'snapshot_sha256':f['snapshot_sha256'],
            'strategy_version':f['strategy_version'],'forecast_id':f['forecast_id'],
            'forecast_sha256':digest(f),
            'forecast_path':'forecasts/'+utc(f['created_at_utc']).strftime('%Y/%m/%d')+'/'+f['forecast_id']+'.json'}
        if key!=expected or path.stem!=snapshot_strategy_key(f):
            raise ValueError('Snapshot reservation differs from immutable forecast')
    for path in (repo/'data/oracle/outcomes').glob('*/*.json'):
        out=json.loads(path.read_text())
        if out['forecast_id'] not in forecasts: raise ValueError('Outcome has no published forecast')
        verify_outcome(out,forecasts[out['forecast_id']],repo/'data')
    for path in (repo/'data/oracle/receipts').glob('*.json'):
        receipt=json.loads(path.read_text())
        if path.stem!=receipt['forecast_id']: raise ValueError('Receipt filename mismatch')
        verify_receipt(receipt,repo/'data')
    return True


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base');p.add_argument('--head',default='HEAD');p.add_argument('--staged',action='store_true')
    a=p.parse_args();check(a.base,a.head,staged=a.staged);print('Oracle archive integrity OK')
