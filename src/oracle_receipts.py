"""Readable, immutable input verification receipts; never model-generated hashes."""
import gzip
import hashlib
import json
from pathlib import Path
from oracle_common import digest
from oracle_forecasts import validate_forecast
from observation_common import utc


def make_receipt(envelope, directory):
    root = Path(directory) / 'oracle'
    f = envelope['forecast']
    forecast_path = 'forecasts/' + utc(f['created_at_utc']).strftime('%Y/%m/%d') + '/' + f['forecast_id'] + '.json'
    persisted = json.loads((root / forecast_path).read_text())
    if persisted != f:
        raise ValueError('Receipt forecast readback mismatch')
    input_path = 'inputs/' + f['snapshot_sha256'] + '.json.gz'
    payload = (root / input_path).read_bytes()
    snapshot = json.loads(gzip.decompress(payload))
    validate_forecast(persisted, snapshot)
    return {'schema_version': 1, 'verification': 'python-decompressed-canonical-sha256-v1',
        'forecast_id': f['forecast_id'], 'forecast_path': forecast_path,
        'forecast_sha256': digest(f), 'snapshot_commit': envelope['snapshot_commit'],
        'snapshot_sha256': digest(snapshot), 'submission_sha256': digest(envelope),
        'input_path': input_path, 'input_bytes': len(payload),
        'input_git_blob_sha1': hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()}


def verify_receipt(receipt, directory):
    path = Path(directory) / 'oracle/submissions' / (receipt['forecast_id'] + '.json')
    envelope = json.loads(path.read_text())
    if receipt != make_receipt(envelope, directory):
        raise ValueError('Publication receipt differs from actual bound input')
    return True
