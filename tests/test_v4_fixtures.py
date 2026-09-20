"""Frozen runner evidence must retain exact bytes and distinguish HTML from data."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent / 'fixtures' / 'v4'


def test_runner_response_integrity():
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    assert manifest['github_run_id'] == '35536030863'
    for entry in manifest['endpoints'].values():
        raw = (ROOT / entry['file']).read_bytes()
        assert len(raw) == entry['bytes']
        assert hashlib.sha256(raw).hexdigest() == entry['sha256']


def test_analytics_requires_json_not_http_success():
    manifest = json.loads((ROOT / 'manifest.json').read_text())['endpoints']
    for name in ('kraken_oi', 'kraken_liquidations'):
        assert manifest[name]['http_status'] == 200
        assert manifest[name]['json'] is False
    for kind in ('open-interest', 'liquidation-volume'):
        data = json.loads((ROOT / f'kraken_analytics_{kind}.json').read_text())
        assert data['errors'] == []
        assert len(data['result']['timestamp']) == len(data['result']['data']) == 60
