#!/usr/bin/env python3
"""Publish a real consumer forecast create-only, or evaluate/regenerate offline."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from oracle_forecasts import persist_forecast
from oracle_context import build_context
from common import write_json


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--data-dir',type=Path,default=Path('data'))
    sub=p.add_subparsers(dest='command',required=True)
    publish=sub.add_parser('publish');publish.add_argument('forecast',type=Path)
    publish.add_argument('--snapshot',type=Path,required=True)
    sub.add_parser('evaluate')
    args=p.parse_args()
    if args.command=='publish':
        print(persist_forecast(json.loads(args.forecast.read_text()),json.loads(args.snapshot.read_text()),
            args.data_dir/'oracle',datetime.now(timezone.utc)))
    else:
        data=json.loads((args.data_dir/'latest.json').read_text())
        c=build_context(data,args.data_dir,persist=True)
        print(json.dumps({'status':c['status'],'reference':c['reference_at_utc']}))


if __name__=='__main__': main()
