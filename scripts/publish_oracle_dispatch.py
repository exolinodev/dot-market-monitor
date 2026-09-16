#!/usr/bin/env python3
"""Publish a manual dispatch or a ChatGPT create-file submission, with equal checks."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from oracle_submission import read_push_submission, publish_submission


def main():
    event_name=os.environ.get('GITHUB_EVENT_NAME','workflow_dispatch')
    if event_name=='push':
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        envelope=read_push_submission(event,os.environ['GITHUB_SHA'])
    elif event_name=='workflow_dispatch':
        envelope={'schema_version':1,'snapshot_commit':os.environ['SNAPSHOT_COMMIT'],
                  'forecast':json.loads(os.environ['FORECAST_JSON'])}
    else:
        raise ValueError('Unsupported publication event')
    print(publish_submission(envelope,datetime.now(timezone.utc)))


if __name__=='__main__': main()
