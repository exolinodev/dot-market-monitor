"""Probe the fixed Kraken demo host without credentials; retain raw evidence."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'src'))
from demo_market import DemoMarketClient, capture_market


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True, help='New directory; parent must exist')
    args = parser.parse_args(argv)
    result = capture_market(args.output_dir, DemoMarketClient())
    print(json.dumps({'available': result['available'], 'reason': result.get('reason'),
                      'authorizes_execution': False}, sort_keys=True))
    return 0 if result['available'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
