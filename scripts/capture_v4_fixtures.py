"""Capture bounded public responses on the runner; failures are evidence too."""
import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import requests


def endpoints(now):
    start = int(now.timestamp()) - 3600
    futures = 'https://futures.kraken.com'
    result = {
        'kraken_funding': (futures + '/derivatives/api/v4/historicalfundingrates', {'symbol': 'PF_DOTUSD'}),
        'kraken_oi': (futures + '/api/analytics/v1/open-interest', {'symbol': 'PF_DOTUSD', 'interval': '1h'}),
        'kraken_liquidations': (futures + '/api/analytics/v1/liquidations', {'symbol': 'PF_DOTUSD', 'interval': '1h'}),
        'bybit_funding': ('https://api.bybit.com/v5/market/funding/history', {'category': 'linear', 'symbol': 'DOTUSDT', 'limit': 10}),
        'bybit_oi': ('https://api.bybit.com/v5/market/open-interest', {'category': 'linear', 'symbol': 'DOTUSDT', 'intervalTime': '1h', 'limit': 10}),
        'okx_funding': ('https://www.okx.com/api/v5/public/funding-rate-history', {'instId': 'DOT-USDT-SWAP', 'limit': 10}),
        'okx_oi': ('https://www.okx.com/api/v5/public/open-interest', {'instType': 'SWAP', 'instId': 'DOT-USDT-SWAP'}),
    }
    # Documented at https://docs.kraken.com/api-reference/analytics/market-analytics
    for kind in ('open-interest', 'liquidation-volume'):
        result['kraken_analytics_' + kind] = (
            futures + '/api/charts/v1/analytics/PF_DOTUSD/' + kind,
            {'since': start, 'to': int(now.timestamp()), 'interval': 60})
    for kind in ('trade', 'mark'):
        for resolution in ('1m', '5m', '15m', '1h'):
            result[f'kraken_{kind}_{resolution}'] = (futures + f'/api/charts/v1/{kind}/PF_DOTUSD/{resolution}', {'from': start, 'to': int(now.timestamp())})
    return result


def capture(dest):
    dest.mkdir(parents=True, exist_ok=True)
    manifest = {'captured_at_utc': datetime.now(timezone.utc).isoformat(),
                'github_run_id': os.getenv('GITHUB_RUN_ID'), 'runner_os': os.getenv('RUNNER_OS'),
                'note': 'Public unauthenticated responses. HTTP success does not establish semantic suitability or funding units.', 'endpoints': {}}
    for name, (url, params) in endpoints(datetime.now(timezone.utc)).items():
        record = {'requested_url': requests.Request('GET', url, params=params).prepare().url,
                  'params': params}
        try:
            with requests.get(url, params=params, timeout=(10, 25), stream=True) as response:
                record.update(final_url=response.url, http_status=response.status_code)
                raw = b''
                for chunk in response.iter_content(65536):
                    raw += chunk
                    if len(raw) > 2_000_000:
                        raise ValueError('Response exceeds capture budget')
                # Preserve response body bytes exactly, including JSON whitespace/order.
                # requests decodes HTTP content encoding, but no JSON rewriting occurs.
                try:
                    json.loads(raw)
                    record['json'] = True
                except ValueError:
                    record['json'] = False
                filename = name + ('.json' if record['json'] else '.txt')
                (dest / filename).write_bytes(raw)
                record.update(file=filename, bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest())
        except (requests.RequestException, ValueError) as error:
            record['error'] = str(error)
        manifest['endpoints'][name] = record
        print(name, record.get('http_status'), record.get('error', ''), flush=True)
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dest', type=Path, required=True)
    capture(parser.parse_args().dest)
