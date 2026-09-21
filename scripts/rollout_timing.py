"""Report quarter timing against a trusted, immutable main revision; no network."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rollout_timing import report

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--repo', type=Path, default=Path('.'))
    p.add_argument('--head', required=True, help='Full SHA of fetched trusted main; full history required')
    p.add_argument('--start', required=True, help='Inclusive UTC quarter boundary')
    p.add_argument('--end', required=True, help='Exclusive UTC quarter boundary; at least 48h for acceptance')
    a = p.parse_args()
    print(json.dumps(report(a.repo, a.head, a.start, a.end, datetime.now(timezone.utc)), indent=2))
