"""Wait at most 90 seconds until the assigned UTC boundary plus eight seconds."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from cycles import resolve, utc


def wait(boundary, clock=lambda: datetime.now(timezone.utc), sleep=time.sleep):
    delay = (utc(boundary) - clock()).total_seconds() + 8
    if delay > 90:
        raise ValueError('Boundary wait exceeds 90 seconds')
    if delay > 0:
        sleep(delay)
    return max(0, delay)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--boundary', required=True)
    parser.add_argument('--run-kind', choices=['full', 'light'], required=True)
    args = parser.parse_args()
    resolve(datetime.now(timezone.utc), args.run_kind, args.boundary)
    print(f'Waited {wait(args.boundary):.3f}s for boundary + 8s')
