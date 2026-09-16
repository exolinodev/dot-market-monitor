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


def check(base,head='HEAD',repo=Path('.')):
    paths=['data/oracle/submissions','data/oracle/forecasts','data/oracle/inputs','data/oracle/outcomes','data/oracle/outcome_inputs']
    changes=subprocess.check_output(['git','diff','--no-renames','--name-status',base,head,'--',*paths],cwd=repo,text=True)
    for line in changes.splitlines():
        status,path=line.split('\t',1)
        if status!='A': raise ValueError('Immutable Oracle artifact modified or removed: '+path)
    # Validate all forecast bindings as well as the proposed additions.
    forecasts={f['forecast_id']:f for f in load_forecasts(repo/'data','2260-01-01T00:00:00Z')}
    for path in (repo/'data/oracle/outcomes').glob('*/*.json'):
        out=json.loads(path.read_text())
        if out['forecast_id'] not in forecasts: raise ValueError('Outcome has no published forecast')
        verify_outcome(out,forecasts[out['forecast_id']],repo/'data')
    return True


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--head',default='HEAD')
    a=p.parse_args();check(a.base,a.head);print('Oracle archive integrity OK')
