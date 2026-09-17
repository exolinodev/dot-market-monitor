#!/usr/bin/env python3
"""Publish a manual dispatch or a ChatGPT create-file submission, with equal checks."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from oracle_submission import read_push_submission, read_pr_submission, publish_submission, strict_json, git, full_sha


def main():
    if os.environ.get('GITHUB_RUN_ATTEMPT', '1') != '1':
        raise ValueError('Oracle publication reruns are forbidden; use a fresh analysis')
    event_name=os.environ.get('GITHUB_EVENT_NAME','workflow_dispatch')
    archive = False
    if event_name=='push':
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        envelope=read_push_submission(event,os.environ['GITHUB_SHA'])
    elif event_name=='pull_request_target':
        event=json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        head=full_sha(event['pull_request']['head']['sha'])
        # Fetch an object for data inspection only. Always keep the trusted main checkout.
        git(Path('.'), 'fetch', 'origin', head)
        envelope=read_pr_submission(event,os.environ['GITHUB_REPOSITORY'])
        archive=True
    elif event_name=='workflow_dispatch':
        envelope={'schema_version':1,'snapshot_commit':os.environ['SNAPSHOT_COMMIT'],
                  'forecast':strict_json(os.environ['FORECAST_JSON'])}
        archive=True
    else:
        raise ValueError('Unsupported publication event')
    print(publish_submission(envelope,datetime.now(timezone.utc),archive_submission=archive))


if __name__=='__main__': main()
