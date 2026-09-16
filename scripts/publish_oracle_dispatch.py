#!/usr/bin/env python3
"""Validate workflow input, bind a real existing main snapshot and publish create-only."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from oracle_forecasts import persist_forecast


def main():
    commit=os.environ['SNAPSHOT_COMMIT']
    if not re.fullmatch('[0-9a-f]{40}',commit): raise ValueError('Full snapshot commit SHA required')
    subprocess.run(['git','merge-base','--is-ancestor',commit,'origin/main'],check=True)
    snapshot=json.loads(subprocess.check_output(['git','show',commit+':data/llm_snapshot.json']))
    forecast=json.loads(os.environ['FORECAST_JSON'])
    print(persist_forecast(forecast,snapshot,Path('data/oracle'),datetime.now(timezone.utc)))


if __name__=='__main__': main()
