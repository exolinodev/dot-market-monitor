"""One year of actual hourly observations and their config; no predictions/labels."""
import math
import hashlib
import json
import re
import pandas as pd
from common import read_json, write_json, freshness
from observation_common import block, utc, iso


METRICS = {
    'spot_midprice_usd': 'USD', 'perp_mark_usd': 'USD', 'open_interest_dot': 'DOT',
    'funding_rate_absolute_api': 'absolute_API_rate', 'quote_mark_basis_bps': 'bps',
    'spot_signed_volume_1h_dot': 'DOT', 'perp_signed_volume_1h_dot': 'DOT',
}


def observation_record(data, flows, config_hash, max_skew=60):
    reference = data['generated_at_utc']
    dot = data['markets']['DOTUSD']
    spot = dot.get('spot') or {}
    perp = dot.get('perp') or {}
    ticker = perp.get('ticker') or {}
    values = {}

    def add(name, value, stamp, sources, method):
        good = (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
                and freshness(stamp, utc(reference), 120)['fresh'])
        values[name] = {'value': float(value) if good else None, 'unit': METRICS[name],
                        'source_timestamp_utc': iso(stamp) if good else None,
                        'source_ids': sources, 'method': method}

    add('spot_midprice_usd', spot.get('quote_midprice'), spot.get('quote_timestamp_utc'),
        ['DOTUSD.spread'], 'spread_midpoint')
    stamp = ticker.get('server_time_utc')
    add('perp_mark_usd', ticker.get('mark_price'), stamp, ['DOTPERP.ticker'], 'exchange_mark')
    instrument = perp.get('instrument') or {}
    units_valid = instrument.get('base') == 'DOT' and instrument.get('quote') == 'USD' and instrument.get('contractSize') == 1
    add('open_interest_dot', ticker.get('open_interest') if units_valid else None, stamp,
        ['DOTPERP.ticker', 'DOTPERP.instrument'], 'validated_contract_size_1_DOT')
    add('funding_rate_absolute_api', ticker.get('funding_rate'), stamp, ['DOTPERP.ticker'], 'unmodified_API_funding_rate')
    price, mark = values['spot_midprice_usd'], values['perp_mark_usd']
    aligned = (price['value'] is not None and price['value'] > 0 and mark['value'] is not None
               and abs((utc(price['source_timestamp_utc'])-utc(mark['source_timestamp_utc'])).total_seconds()) <= max_skew)
    add('quote_mark_basis_bps', (mark['value']/price['value']-1)*10000 if aligned else None,
        stamp, ['DOTUSD.spread', 'DOTPERP.ticker'], 'mark_vs_spot_quote_midpoint')
    for venue, source in [('spot', 'DOTUSD.trades'), ('perp', 'DOTPERP.trades')]:
        current = ((flows.get('data') or {}).get(venue) or {}).get('current') or {}
        value = (current.get('data') or {}).get('signed_volume_dot') if current.get('status') == 'ok' else None
        # Window sums are stored for auditing, not subtracted across overlapping windows.
        add(venue+'_signed_volume_1h_dot', value, current.get('asof_utc'), [source], 'complete_trailing_60m_signed_volume')
    return {'schema_version': 1, 'observed_at_utc': iso(reference), 'config_sha256': config_hash, 'values': values}


def history_observations(records, current, days=365, tolerance=20, minimum=168):
    reference = utc(current['observed_at_utc'])
    # Match the archive state after this hourly slot has been saved: exclude the
    # current UTC hour and reserve one retention slot for the current record.
    # Repeated runs within an hour must replay identically after replacement.
    past = sorted([r for r in records if reference-pd.Timedelta(days=days) <= utc(r['observed_at_utc'])
                   and utc(r['observed_at_utc']).floor('h') < reference.floor('h')],
                  key=lambda r: utc(r['observed_at_utc']))[-(days*24-1):]
    rows = {}
    for hours in (1, 4, 24, 168):
        target = reference-pd.Timedelta(hours=hours)
        previous = min(past, key=lambda r: (abs((utc(r['observed_at_utc'])-target).total_seconds()), r['observed_at_utc'])) if past else None
        if previous and abs((utc(previous['observed_at_utc'])-target).total_seconds()) > tolerance*60:
            previous = None
        values = {}
        for name in METRICS:
            if 'signed_volume' in name:
                continue
            a = current['values'][name]
            b = (previous or {}).get('values', {}).get(name, {})
            good = (a['value'] is not None and b.get('value') is not None
                    and a['unit'] == b.get('unit') and a['method'] == b.get('method')
                    and a['source_ids'] == b.get('source_ids'))
            elapsed = None
            if good:
                elapsed = (utc(a['source_timestamp_utc'])-utc(b['source_timestamp_utc'])).total_seconds()/60
                good = elapsed > 0 and abs(elapsed-hours*60) <= tolerance
            values[name] = {'current_value': a['value'], 'reference_value': b.get('value') if good else None,
                'absolute_change': a['value']-b['value'] if good else None, 'unit': a['unit'],
                'source_timestamp_utc': a['source_timestamp_utc'],
                'reference_source_timestamp_utc': b.get('source_timestamp_utc') if good else None,
                'elapsed_minutes': elapsed if good else None, 'status': 'ok' if good else 'unavailable'}
        rows[f'{hours}h'] = block(values, status='ok' if all(v['status'] == 'ok' for v in values.values()) else 'partial',
            asof=reference, coverage={'target_reference_utc': iso(target), 'tolerance_minutes': tolerance,
            'actual_reference_utc': previous['observed_at_utc'] if previous else None})
    funding = current['values']['funding_rate_absolute_api']['value']
    baseline = [r['values']['funding_rate_absolute_api']['value'] for r in past
                if utc(r['observed_at_utc']) >= reference-pd.Timedelta(days=30)
                and r['values']['funding_rate_absolute_api']['value'] is not None
                and all(r['values']['funding_rate_absolute_api'][key] == current['values']['funding_rate_absolute_api'][key]
                        for key in ('unit', 'method', 'source_ids'))]
    percentile = (100*(sum(v < funding for v in baseline)+.5*sum(v == funding for v in baseline))/len(baseline)
                  if funding is not None and len(baseline) >= minimum else None)
    return block({'changes': rows, 'funding_history': {
        'current_value': funding, 'historical_percentile': percentile, 'sample_count': len(baseline),
        'minimum_samples': minimum, 'lookback_days': 30, 'unit': 'absolute_API_rate',
        'status': 'ok' if percentile is not None else 'unavailable'}}, asof=reference,
        status='ok' if all(r['status'] == 'ok' for r in rows.values()) and percentile is not None else 'partial',
        sources=['DOTUSD.spread', 'DOTPERP.ticker', 'DOTPERP.instrument'],
        coverage={'history_file': 'data/raw/observation_history.json.gz', 'available_prior_records': len(past),
                  'retention_days': days, 'synthetic_backfill': False, 'current_utc_hour_excluded': True})


class ObservationArchive:
    def __init__(self, path):
        self.path = path
        self.content = read_json(path, {'schema_version': 1, 'records': [], 'configurations': {}})
        if (self.content.get('schema_version') != 1 or not isinstance(self.content.get('records'), list)
                or not isinstance(self.content.get('configurations'), dict)):
            raise ValueError('Invalid observation archive')
        previous = None
        for digest, entry in self.content['configurations'].items():
            actual = hashlib.sha256(json.dumps(entry['configuration'], sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
            if actual != digest:
                raise ValueError('Archived configuration hash mismatch')
            utc(entry['recorded_at_utc'])
        for record in self.records:
            stamp = utc(record['observed_at_utc'])
            if previous is not None and stamp.floor('h') <= previous.floor('h'):
                raise ValueError('Archive must contain unique, ordered UTC hours')
            previous = stamp
            if not re.fullmatch('[0-9a-f]{64}', record.get('config_sha256', '')) or record['config_sha256'] not in self.content['configurations']:
                raise ValueError('Archived configuration is missing')
            if record.get('schema_version') != 1 or set(record['values']) != set(METRICS):
                raise ValueError('Invalid archived observation')
            for name, value in record['values'].items():
                if value['unit'] != METRICS[name] or (value['value'] is not None and
                        (isinstance(value['value'], bool) or not isinstance(value['value'], (int, float)) or not math.isfinite(value['value']))):
                    raise ValueError('Invalid archived units or number')
                if value['value'] is not None:
                    if not freshness(value['source_timestamp_utc'], stamp, 120)['fresh']:
                        raise ValueError('Archived value was not fresh at observation time')

    @property
    def records(self):
        return self.content['records']

    def update(self, current, configuration, days=365):
        now = utc(current['observed_at_utc'])
        if any(utc(record['observed_at_utc']) > now for record in self.records):
            raise ValueError('Cannot replace later archived observations with an earlier snapshot')
        buckets = {}
        for record in sorted([*self.records, current], key=lambda r: utc(r['observed_at_utc'])):
            stamp = utc(record['observed_at_utc'])
            if now-pd.Timedelta(days=days) <= stamp <= now:
                buckets[stamp.floor('h')] = record
        records = list(buckets.values())[-days*24:]
        configs = dict(self.content['configurations'])
        configs.setdefault(current['config_sha256'], {'recorded_at_utc': iso(now), 'configuration': configuration})
        used = {r['config_sha256'] for r in records}
        self.content = {'schema_version': 1, 'records': records,
                        'configurations': {k: v for k, v in configs.items() if k in used}}
        write_json(self.path, self.content, compressed=True)
