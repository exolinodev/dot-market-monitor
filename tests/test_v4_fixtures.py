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


def test_documented_market_facts():
    from datetime import datetime
    from decimal import Decimal

    def utc(value):
        return datetime.fromisoformat(value.replace('Z', '+00:00'))

    manifest = json.loads((ROOT / 'manifest.json').read_text())
    captured = utc(manifest['captured_at_utc']).timestamp()
    rates = json.loads((ROOT / 'kraken_funding.json').read_text())['rates']
    assert len(rates) == 8838
    assert (rates[0]['timestamp'], rates[-1]['timestamp']) == (
        '2025-09-17T08:00:00Z', '2026-09-20T20:00:00Z')
    gaps = [(a['timestamp'], b['timestamp']) for a, b in zip(rates, rates[1:])
            if (utc(b['timestamp']) - utc(a['timestamp'])).total_seconds() != 3600]
    assert gaps == [
        ('2025-11-01T15:00:00Z', '2025-11-01T18:00:00Z'),
        ('2025-11-02T03:00:00Z', '2025-11-02T05:00:00Z'),
        ('2025-11-19T04:00:00Z', '2025-11-19T06:00:00Z'),
        ('2025-12-04T08:00:00Z', '2025-12-04T10:00:00Z'),
        ('2026-02-04T11:00:00Z', '2026-02-04T13:00:00Z'),
        ('2026-02-13T17:00:00Z', '2026-02-13T19:00:00Z'),
    ]
    implied_marks = []
    for rate in rates[-24:]:
        absolute = Decimal(str(rate['fundingRate']))
        relative = Decimal(str(rate['relativeFundingRate']))
        implied_mark = absolute / relative
        implied_marks.append(implied_mark)
        # Dimensional equivalence only: not independent evidence of mark/sign.
        qty = Decimal('100')
        assert abs(qty * absolute - qty * implied_mark * relative) < Decimal('1e-25')
    assert abs(min(implied_marks) - Decimal('1.07578')) < Decimal('1e-8')
    assert abs(max(implied_marks) - Decimal('1.16636')) < Decimal('1e-8')

    for kind in ('trade', 'mark'):
        for resolution, seconds, count in [('1m', 60, 60), ('5m', 300, 12),
                                            ('15m', 900, 4), ('1h', 3600, 1)]:
            name = f'kraken_{kind}_{resolution}'
            payload = json.loads((ROOT / (name + '.json')).read_text())
            candles = payload['candles']
            assert len(candles) == count
            assert payload['more_candles'] is False
            times = [c['time'] / 1000 for c in candles]
            assert all(b - a == seconds for a, b in zip(times, times[1:]))
            assert times[-1] <= captured < times[-1] + seconds
            start = manifest['endpoints'][name]['params']['from']
            assert times[0] == (start // seconds + 1) * seconds
            for candle in candles:
                for field in ('open', 'high', 'low', 'close', 'volume'):
                    assert Decimal(candle[field]).is_finite()
                if kind == 'mark':
                    assert Decimal(candle['volume']) == 0
    for kind in ('open-interest', 'liquidation-volume'):
        result = json.loads((ROOT / f'kraken_analytics_{kind}.json').read_text())['result']
        times = result['timestamp']
        assert times[0] == utc('2026-09-20T19:36:00Z').timestamp()
        assert times[-1] == utc('2026-09-20T20:35:00Z').timestamp()
        assert all(b - a == 60 for a, b in zip(times, times[1:]))
        if kind == 'open-interest':
            assert all(len(row) == 4 for row in result['data'])
        else:
            assert all(Decimal(value) == 0 for value in result['data'])
