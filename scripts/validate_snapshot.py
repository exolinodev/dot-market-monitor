"""Offline schema validation; --live additionally checks a real fresh core run."""
import argparse
import json
from datetime import datetime,timezone
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from output import validate_snapshot

parser=argparse.ArgumentParser()
parser.add_argument('--path',type=Path,default=Path(__file__).resolve().parents[1]/'data'/'llm_snapshot.json')
parser.add_argument('--live',action='store_true')
args=parser.parse_args()
data=json.loads(args.path.read_text())
validate_snapshot(data)
if args.live:
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(data['meta']['generated_at_utc'].replace('Z','+00:00'))).total_seconds()
    assert -5<=age<=5400, f'Stale document: {age}s'
    for name in ['DOTUSD','BTCUSD','DOTBTC']:
        market=data['markets'][name]
        assert market['spot']['current_price'] is not None and market['spot']['freshness']['fresh'],name+' missing fresh price'
        assert all(x['last_closed'] and x['live'] for x in market['timeframes'].values()),name+' incomplete timeframes'
    assert data['markets']['DOTUSD']['perp']['ticker'] is not None,'Perp ticker unavailable in smoke test'
assert args.path.stat().st_size<500_000,'Compact snapshot exceeds 500 KB budget'
print(f"Validated schema v2: {data['meta']['status']}; {len(data['sources'])} sources; {args.path.stat().st_size:,} bytes")
print('Source errors:',len(data['errors']))
print('DOT timeframes:',', '.join(data['markets']['DOTUSD']['timeframes']))
