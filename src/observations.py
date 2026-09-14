"""Additive data-only assembly shared by collection and offline snapshot replay."""
import hashlib
import json
from pathlib import Path
from decimal import Decimal
from urllib.parse import urlsplit, parse_qs
import pandas as pd
import jsonschema
from common import freshness, read_json
from coinbase import CoinbaseClient, PRODUCT_URL, QUOTE_URL
from kraken import KrakenClient
from timeframes import decode_candles
from observation_common import block, utc, iso
from observation_candles import price_level_observations, anchored_vwap, historical_context, market_relative
from observation_flow import flow_windows, volume_profile
from observation_history import ObservationArchive, observation_record, history_observations
from event_calendar import scheduled_events

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ('price_levels', 'anchored_vwap', 'historical_context', 'market_relative',
              'flow_windows', 'volume_profile', 'spot_perp_history', 'cross_venue', 'scheduled_events', 'input_lineage')


def configuration(config_dir=None):
    directory = Path(config_dir) if config_dir is not None else ROOT/'config'
    config = {name: json.loads((directory/filename).read_text()) for name, filename in [
        ('observations', 'observations.json'), ('time_fibs', 'time_fibs.json'), ('events', 'scheduled_events.json')]}
    schema = json.loads((ROOT/'schema'/'observations.config.schema.json').read_text())
    jsonschema.Draft202012Validator(schema).validate(config['observations'])
    settings = config['observations']
    if settings['context_minimum_samples'] > settings['context_baseline_bars']:
        raise ValueError('Minimum sample count cannot exceed baseline window')
    if Decimal(settings['volume_profile_bin_width_usd']) <= 0:
        raise ValueError('Volume profile bin width must be positive')
    digest = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    return config, digest


def cross_venue(data, quote, max_skew=60):
    sources = ['DOTUSD.depth', 'COINBASE.DOTUSD.book']
    reference = data['generated_at_utc']
    kraken = data['markets']['DOTUSD'].get('orderbook') or {}
    meta = data['sources'].get('DOTUSD.depth') or {}
    stamp = meta.get('received_at_utc')
    k = None
    if meta.get('fresh') and kraken and freshness(stamp, utc(reference), 120)['fresh']:
        k = {'venue': 'kraken', 'market': 'DOTUSD', 'market_type': 'spot', 'base': 'DOT', 'quote': 'USD',
             'bid_usd': kraken['best_bid'], 'ask_usd': kraken['best_ask'], 'midprice_usd': kraken['midprice'],
             'spread_bps': kraken['spread_bps'], 'bid_size_dot': kraken['best_bid_size'],
             'ask_size_dot': kraken['best_ask_size'], 'source_timestamp_utc': iso(stamp),
             'timestamp_kind': 'received_at'}
    if quote is not None and not freshness(quote.get('source_timestamp_utc'), utc(reference), 120)['fresh']:
        quote = None
    c = {**quote, 'timestamp_kind': 'source'} if quote else None
    skew = abs((utc(k['source_timestamp_utc'])-utc(c['source_timestamp_utc'])).total_seconds()) if k and c else None
    good = skew is not None and skew <= max_skew
    return block({'kraken': k, 'coinbase': c, 'timestamp_skew_seconds': skew,
                  'coinbase_minus_kraken_mid_bps': (c['midprice_usd']/k['midprice_usd']-1)*10000 if good else None},
        status='ok' if good else 'partial', reason=None if good else 'quote_missing_stale_or_unaligned',
        sources=sources, asof=reference, coverage={'maximum_timestamp_skew_seconds': max_skew,
        'comparable_quote_currency': 'USD', 'comparison_method': 'spot_book_midpoints',
        'order_identity_available': False})


def build_observations(data, frames=None, spot_trades=None, perp_trades=None, quote=None,
                       archive=None, config_dir=None):
    reference = data['generated_at_utc']
    output = {'schema_version': 1, 'contract': 'measurements_only', 'reference_at_utc': iso(reference),
              'config_sha256': None, 'status': 'ok', 'components': {}}
    try:
        config, digest = configuration(config_dir)
    except Exception as exc:
        output.update(status='error')
        output['components'] = {name: block(status='error', reason='configuration: '+str(exc)) for name in COMPONENTS}
        return output, None, None
    output['config_sha256'] = digest
    cfg = config['observations']
    frames = frames or {}
    components = output['components']

    def compute(name, fn):
        try:
            components[name] = fn()
        except Exception as exc:
            components[name] = block(status='error', reason=str(exc))

    compute('price_levels', lambda: block({f'{m//60}h': price_level_observations(
        frames.get(('DOTUSD', m)), m, reference, cfg['price_levels_usd'], cfg['level_window_bars'])
        for m in cfg['level_timeframes_minutes']}, asof=reference,
        sources=[f'DOTUSD.ohlc.{m}' for m in cfg['level_timeframes_minutes']]))
    m = cfg['anchored_vwap_minutes']
    compute('anchored_vwap', lambda: anchored_vwap(frames.get(('DOTUSD', m)), m, reference,
                                                 data['markets']['DOTUSD'].get('time_fibs', {})))
    m = cfg['context_minutes']
    compute('historical_context', lambda: historical_context(frames.get(('DOTUSD', m)), m, reference,
        cfg['context_baseline_bars'], cfg['context_minimum_samples'], cfg['movement_window_bars']))
    compute('market_relative', lambda: market_relative(frames.get(('DOTUSD', 60)), frames.get(('BTCUSD', 60)),
        reference, cfg['relative_return_hours'], cfg['beta_estimation_hours']))
    compute('flow_windows', lambda: flow_windows(spot_trades, perp_trades, reference, cfg['flow_window_minutes']))
    compute('volume_profile', lambda: volume_profile(spot_trades, reference,
        cfg['volume_profile_window_minutes'], cfg['volume_profile_bin_width_usd']))
    current = observation_record(data, components['flow_windows'], digest, cfg['cross_venue_max_skew_seconds'])
    compute('spot_perp_history', lambda: history_observations(archive.records if archive else [], current,
        cfg['history_days'], cfg['history_reference_tolerance_minutes'], cfg['context_minimum_samples']))
    compute('cross_venue', lambda: cross_venue(data, quote, cfg['cross_venue_max_skew_seconds']))
    compute('scheduled_events', lambda: scheduled_events(config['events'], reference))

    def lineage():
        selected = config['time_fibs']['active_anchor_sets'].get('DOTUSD')
        anchor_set = config['time_fibs']['anchor_sets'].get(selected, {})
        recorded = anchor_set.get('selection_recorded_at_utc')
        return block({'anchor_set_id': selected,
            'selection_recorded_at_utc': iso(recorded) if recorded is not None else None,
            'selection_record_available_asof_snapshot': recorded is not None and utc(recorded) <= utc(reference),
            'duration_dependencies': {'A_B': ['A', 'B'], 'B_C': ['B', 'C'], 'A_C': ['A_B', 'B_C']},
            'identities': ['A_C = A_B + B_C', 'A_C_0.500 = midpoint(A_B_1.000, B_C_1.000)'],
            'cluster_event_count_semantics': 'arithmetic_projection_count',
            'shared_inputs': {'price_levels': ['DOTUSD closed OHLC', 'ATR14'],
                              'anchored_vwap': ['DOTUSD closed candle VWAP and volume', selected],
                              'historical_context': ['DOTUSD closed OHLC'],
                              'market_relative': ['DOTUSD closed hourly close', 'BTCUSD closed hourly close'],
                              'flow_windows': ['DOTUSD spot trades', 'DOTUSD perpetual trades'],
                              'volume_profile': ['DOTUSD spot trades']}}, asof=reference)
    compute('input_lineage', lineage)
    # Aggregate data availability, never a market assessment.
    level_data = components['price_levels'].get('data')
    if level_data and any(v['status'] != 'ok' for v in level_data.values()):
        components['price_levels']['status'] = 'partial'
    output['status'] = 'ok' if all(c['status'] == 'ok' for c in components.values()) else 'partial'
    return output, current, config


def replay_inputs(data, data_dir):
    """Use saved source receipt cutoffs and raw trades; never fetch during replay."""
    directory = Path(data_dir)
    raw = read_json(directory/'raw'/'latest.json.gz', {})
    if raw.get('generated_at_utc') is None or utc(raw['generated_at_utc']) != utc(data['generated_at_utc']):
        return {}, None, None, None
    cache = read_json(directory/'raw'/'ohlc_cache.json.gz', {})
    frames = {}
    for market, minutes in [('DOTUSD', 60), ('DOTUSD', 240), ('BTCUSD', 60)]:
        key = f'{market}.ohlc.{minutes}'
        meta = data['sources'].get(key, {})
        if key not in cache or not meta.get('fresh'):
            continue
        frame = decode_candles(cache[key])
        cutoff = min(utc(meta['received_at_utc']), utc(data['generated_at_utc']))
        frames[(market, minutes)] = frame[frame.index+pd.Timedelta(minutes=minutes) <= cutoff]
    payloads = [(entry['url'], raw['responses'][key]) for key, entry in raw.get('http_requests', {}).items()
                if key in raw.get('responses', {}) and entry.get('status') == 'ok']
    tapes = []
    for venue in ('spot', 'perp'):
        source = 'DOTUSD.trades' if venue == 'spot' else 'DOTPERP.trades'
        summary = data['markets']['DOTUSD'].get('trade_flow') if venue == 'spot' else data['markets']['DOTUSD']['perp'].get('trade_flow')
        chunks = []
        if data['sources'].get(source, {}).get('fresh') and summary and summary.get('freshness', {}).get('fresh'):
            for url, payload in payloads:
                parsed = urlsplit(url)
                params = parse_qs(parsed.query)
                if venue == 'spot' and parsed.hostname == 'api.kraken.com' and parsed.path.endswith('/Trades') and params.get('pair') == ['DOTUSD']:
                    chunks.append(KrakenClient.parse_spot_trades(payload))
                elif venue == 'perp' and parsed.hostname == 'futures.kraken.com' and parsed.path.endswith('/history') and params.get('symbol') == ['PF_DOTUSD']:
                    chunks.append(KrakenClient.parse_futures_trades(payload))
        frame = pd.concat(chunks).drop_duplicates('trade_id').sort_values('time').reset_index(drop=True) if chunks else None
        if frame is not None:
            frame.attrs = {'coverage_start': summary['coverage_start_utc'], 'coverage_end': summary['coverage_end_utc']}
        tapes.append(frame)
    values = dict(payloads)
    quote = CoinbaseClient.parse_quote(values[PRODUCT_URL], values[QUOTE_URL]) if PRODUCT_URL in values and QUOTE_URL in values else None
    return frames, *tapes, quote
