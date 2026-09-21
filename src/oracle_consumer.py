"""Bounded, exact-field projections for consumers with truncated file tools.

The authoritative snapshot is unchanged. Every record has a JSON Pointer into it;
omitted fields are unknown, never reconstructed. Hashes are computed by Python.
"""
from copy import deepcopy
from pathlib import Path
from oracle_common import canonical, digest
from common import write_json

PART_BYTES = 10000
OBSERVATION_COMPONENTS = ('price_levels', 'anchored_vwap', 'historical_context',
    'market_relative', 'flow_windows', 'volume_profile', 'spot_perp_history',
    'cross_venue', 'scheduled_events', 'input_lineage')


def pointer(parts):
    return '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in parts)


def build_consumer_bundle(snapshot):
    snapshot_hash = digest(snapshot)
    sections = {}

    def add(section, *path):
        value = snapshot
        for key in path:
            if not isinstance(value, dict) or key not in value:
                return
            value = value[key]
        sections.setdefault(section, []).append({'path': pointer(path), 'value': deepcopy(value)})

    dot = ('markets', 'DOTUSD')
    for key in ('meta', 'errors', 'relative_strength_dot_btc', 'global_market', 'correlation_beta'):
        add('overview', key)
    for key in ('spot', 'spot_perp_basis'):
        add('overview', *dot, key)
    for key in ('ticker', 'status', 'source_ids'):
        add('overview', *dot, 'perp', key)
    for key in ('schema_version', 'contract', 'config_sha256', 'status', 'reference_at_utc'):
        add('overview', *dot, 'observations', key)
    add('overview', 'breadth', 'windows')
    add('overview', 'breadth', 'source_ids')

    context = snapshot['markets']['DOTUSD'].get('oracle_context', {})
    for key in sorted(context):
        if key != 'current_features':
            add('oracle', *dot, 'oracle_context', key)
    current = context.get('current_features') or {}
    for key in sorted(current):
        if key != 'features':
            add('oracle', *dot, 'oracle_context', 'current_features', key)
    for key in sorted(current.get('features') or {}):
        add('features', *dot, 'oracle_context', 'current_features', 'features', key)

    # All exported DOT timeframes retain closed OHLC and key indicator values.
    # Detailed structure is provided for the four main decision timeframes.
    for tf in sorted(snapshot['markets']['DOTUSD'].get('timeframes', {})):
        base = (*dot, 'timeframes', tf)
        for key in ('status', 'fresh', 'interval_minutes', 'source_ids', 'calculation_at_utc'):
            add('timeframes', *base, key)
        for key in ('asof_utc', 'open', 'high', 'low', 'close', 'volume', 'warmup_unavailable'):
            add('timeframes', *base, 'last_closed', key)
        for key in ('ema20', 'ema50', 'ema100', 'ema200', 'rsi14', 'macd', 'macd_hist', 'atr14'):
            add('timeframes', *base, 'last_closed', 'indicators', key)
        if tf in ('1w', '1d', '4h', '1h'):
            add('structure', *base, 'structure')
    add('execution', *dot, 'execution_context')
    add('intraday', *dot, 'intraday')
    add('intraday', *dot, 'perp', 'historical_data')
    add('timing', *dot, 'time_fibs')
    for key in OBSERVATION_COMPONENTS:
        add('observations', *dot, 'observations', 'components', key)
    for key in sorted(snapshot.get('sources', {})):
        add('sources', 'sources', key)

    files = {}
    manifest = {
        'schema_version': 1, 'projection_version': 'oracle-consumer-v2',
        'snapshot_path': 'data/llm_snapshot.json', 'snapshot_sha256': snapshot_hash,
        'snapshot_generated_at_utc': snapshot['meta']['generated_at_utc'],
        'scope': 'exact_selected_fields; omitted fields remain unknown; no intrabar confirmation',
        'part_max_bytes': PART_BYTES, 'parts': [],
        'observation_components': [k for k in OBSERVATION_COMPONENTS if k in
            snapshot['markets']['DOTUSD'].get('observations', {}).get('components', {})],
    }

    def wrap(records):
        return {'schema_version': 1, 'snapshot_sha256': snapshot_hash, 'records': records}

    def size(value):
        return len((canonical(value) + '\n').encode())

    def split(record):
        if size(wrap([record])) <= PART_BYTES:
            return [record]
        value = record['value']
        if not isinstance(value, (dict, list)) or not value:
            raise ValueError('Consumer field exceeds bounded part size')
        items = sorted(value.items()) if isinstance(value, dict) else enumerate(value)
        return [child for key, item in items for child in split({
            'path': record['path'] + pointer([key]), 'value': item})]

    for section, records in sections.items():
        groups = [[]]
        for record in records:
            for small in split(record):
                if size(wrap(groups[-1] + [small])) > PART_BYTES:
                    groups.append([])
                groups[-1].append(small)
        for n, group in enumerate(groups, 1):
            name = f'{section}-{n:02d}.json'
            part = wrap(group)
            files[name] = part
            manifest['parts'].append({'path': name,
                'section': section, 'sha256': digest(part), 'bytes': size(part)})
    return manifest, files


def write_consumer_bundle(snapshot, data_dir):
    manifest, files = build_consumer_bundle(snapshot)
    root = Path(data_dir) / 'oracle' / 'consumer'
    root.mkdir(parents=True, exist_ok=True)
    # These projections are rolling collector output, not immutable forecasts.
    for stale in root.glob('*.json'):
        if stale.name not in files and stale.name != 'index.json':
            stale.unlink()
    for name, value in {**files, 'index.json': manifest}.items():
        write_json(root / name, value)
    return manifest
