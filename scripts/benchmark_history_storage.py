"""Compare lossless history encodings using exactly the same recorded observations."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from common import dumps
from history import decode_history, encode_history


def benchmark(path):
    rows = decode_history(json.loads(Path(path).read_bytes()))
    legacy = dumps(rows).encode()
    candidate = dumps(encode_history(rows)).encode()
    if dumps(decode_history(json.loads(candidate))).encode() != legacy:
        raise ValueError('History encoding changed observation values')
    return {'observations': len(rows), 'legacy_bytes': len(legacy),
            'columnar_bytes': len(candidate), 'saved_bytes': len(legacy) - len(candidate),
            'lossless': True, 'comparison': 'same observations, identical numeric precision and retention'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('history', type=Path)
    args = parser.parse_args()
    print(json.dumps(benchmark(args.history), indent=2))
