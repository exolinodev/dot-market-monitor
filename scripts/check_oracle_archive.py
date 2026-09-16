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


def check(base,head='HEAD',repo=Path('.')):
    paths=['data/oracle/submissions','data/oracle/forecast_keys','data/oracle/forecasts','data/oracle/inputs','data/oracle/outcomes','data/oracle/outcome_inputs']
    changes=subprocess.check_output(['git','diff','--no-renames','--name-status',base,head,'--',*paths],cwd=repo,text=True)
    for line in changes.splitlines():
        status,path=line.split('\t',1)
        if status!='A': raise ValueError('Immutable Oracle artifact modified or removed: '+path)
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
    return True


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--head',default='HEAD')
    a=p.parse_args();check(a.base,a.head);print('Oracle archive integrity OK')
