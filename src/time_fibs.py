"""Pure UTC time projections from explicitly configured confirmed spot pivots.

No candle discovery, price projections, wave recognition, or wall-clock reads.
Configuration is the reviewed anchor record, independent of rolling pivot history.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parents[1] / 'config' / 'time_fibs.json'
METHOD = 'confirmed_spot_pivots_same_degree'
SUPPORTED_RATIOS = {Decimal(r) for r in ('0.618', '1.000', '1.272', '1.618', '2.000', '2.618')}
MICROSECOND = timedelta(microseconds=1)
CLUSTER_MAX_SPAN = timedelta(hours=4)


def _utc(value):
    if not isinstance(value, str):
        raise ValueError('Timestamp must be an ISO-8601 string with a UTC offset')
    stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError('Timestamp must include a UTC offset')
    return stamp.astimezone(timezone.utc)


def _iso(stamp):
    text = stamp.astimezone(timezone.utc).isoformat().removesuffix('+00:00')
    return (text.rstrip('0').rstrip('.') if '.' in text else text) + 'Z'


def _position(reference, start, center, end):
    return {
        'state': 'upcoming' if reference < start else 'expired' if reference > end else 'active',
        'minutes_to_center': (center - reference).total_seconds() / 60 if reference <= center else None,
        'minutes_to_start': (start - reference).total_seconds() / 60 if reference <= start else None,
        'minutes_since_end': (reference - end).total_seconds() / 60 if reference > end else None,
    }


def cluster_projections(events, generated_at_utc, anchor_set_id):
    """Greedy, disjoint earliest-first groups, inclusive 4h span; no chaining.

    Repeated copies of the same duration/ratio are one event. Independence means
    at least two distinct source durations; same-time independent events count.
    """
    reference = _utc(generated_at_utc)
    unique = {}
    for event in events:
        key = (event['source_duration'], Decimal(str(event['ratio'])))
        if key in unique and unique[key] != event:
            raise ValueError('Conflicting copies of one projection')
        unique[key] = event
    ordered = sorted(unique.values(), key=lambda e: (_utc(e['projected_at_utc']), e['projection_id']))
    clusters = []
    i = 0
    while i < len(ordered):
        start = _utc(ordered[i]['projected_at_utc'])
        j = i + 1
        while j < len(ordered) and _utc(ordered[j]['projected_at_utc']) - start <= CLUSTER_MAX_SPAN:
            j += 1
        group = ordered[i:j]
        if len({event['source_duration'] for event in group}) < 2:
            # A nonqualifying prefix must not hide a later independent pair.
            i += 1
            continue
        i = j
        times = [_utc(event['projected_at_utc']) for event in group]
        middle = len(times) // 2
        center = times[middle] if len(times) % 2 else times[middle-1] + (times[middle] - times[middle-1]) / 2
        identity = '|'.join(event['projection_id'] for event in group)
        clusters.append({
            'cluster_id': anchor_set_id + ':' + hashlib.sha256(identity.encode()).hexdigest()[:12],
            'window_start_utc': _iso(start),
            'center_utc': _iso(center),
            'window_end_utc': _iso(times[-1]),
            'event_count': len(group),
            **_position(reference, start, center, times[-1]),
            'events': group,
        })
    return clusters


def _failure(status, reason, anchor_set_id=None):
    return {
        'status': status, 'reason': reason, 'method': METHOD,
        'timezone_calculation': 'UTC', 'anchor_set_id': anchor_set_id,
        'anchors': [], 'durations': None, 'projections': [],
        'symmetry_projections': [], 'clusters': [],
    }


def build_time_fibs(generated_at_utc, config, market='DOTUSD'):
    """Deterministic function of the snapshot timestamp and explicit config only.

    Fail the whole set on incomplete/invalid input; never substitute other pivots.
    Confirmation times are actual availability instants (confirming candle close).
    """
    anchor_set_id = None
    try:
        reference = _utc(generated_at_utc)
        if not isinstance(config, dict):
            raise ValueError('Time-fib config must be an object')
        active = config.get('active_anchor_sets', {})
        sets = config.get('anchor_sets', {})
        if not isinstance(active, dict) or not isinstance(sets, dict):
            raise ValueError('Anchor selection and anchor sets must be objects')
        anchor_set_id = active.get(market)
        if anchor_set_id is None:
            return _failure('unavailable', 'No explicit anchor set selected', None)
        if not isinstance(anchor_set_id, str) or not anchor_set_id:
            raise ValueError('Anchor set ID must be a nonempty string')
        if anchor_set_id not in sets:
            return _failure('unavailable', 'Selected anchor set is missing', anchor_set_id)
        selected = sets[anchor_set_id]
        if not isinstance(selected, dict):
            raise ValueError('Selected anchor set must be an object')
        if market != 'DOTUSD' or selected.get('market') != market:
            raise ValueError('Only DOTUSD spot anchor sets are supported')
        if selected.get('selection') != 'explicit_same_degree':
            raise ValueError('Same-degree selection must be explicit')
        records = selected.get('anchors')
        if records is None or records == []:
            return _failure('unavailable', 'Selected anchors are missing', anchor_set_id)
        if not isinstance(records, list) or len(records) != 3:
            raise ValueError('Exactly three anchors A, B, C are required')
        anchors = {}
        times = {}
        for record in records:
            if not isinstance(record, dict):
                raise ValueError('Anchor must be an object')
            label = record.get('id')
            if not isinstance(label, str) or label not in ('A', 'B', 'C') or label in anchors:
                raise ValueError('Anchor IDs must be unique A, B, C')
            if (record.get('market_type') != 'spot' or record.get('pivot_method') != 'fractal'
                    or record.get('candle_state') != 'closed' or record.get('confirmed') is not True):
                raise ValueError('Only confirmed fractal pivots from closed spot candles are allowed')
            if record.get('type') not in ('high', 'low'):
                raise ValueError('Anchor type must be high or low')
            price = Decimal(str(record.get('price')))
            if not price.is_finite() or not 0 < float(price) < float('inf'):
                raise ValueError('Anchor price must be finite and positive')
            stamp = _utc(record.get('time_utc'))
            confirmation = _utc(record.get('confirmed_at_utc'))
            if confirmation <= stamp:
                raise ValueError('Confirmation must follow the anchor')
            if confirmation > reference:
                return _failure('unavailable', 'Anchor is not confirmed as of snapshot time', anchor_set_id)
            times[label] = stamp
            anchors[label] = {key: record[key] for key in (
                'id', 'type', 'market_type', 'pivot_method', 'candle_state', 'confirmed')}
            anchors[label].update(price=float(price), time_utc=_iso(stamp), confirmed_at_utc=_iso(confirmation))
            if 'role' in record:
                if not isinstance(record['role'], str):
                    raise ValueError('Anchor role must be a string')
                anchors[label]['role'] = record['role']
        if not times['A'] < times['B'] < times['C']:
            raise ValueError('Anchor times must satisfy A < B < C')
        configured_ratios = selected.get('ratios')
        if not isinstance(configured_ratios, list) or not configured_ratios:
            raise ValueError('At least one supported ratio must be configured')
        ratios = [Decimal(str(value)) for value in configured_ratios]
        if any(not r.is_finite() or r not in SUPPORTED_RATIOS for r in ratios):
            raise ValueError('Unsupported Fibonacci time ratio')
        if len(set(ratios)) != len(ratios):
            raise ValueError('Duplicate Fibonacci time ratios are not allowed')
        durations = {'A_B': times['B'] - times['A'], 'B_C': times['C'] - times['B'], 'A_C': times['C'] - times['A']}

        def project(source, ratio):
            micros = Decimal(durations[source] // MICROSECOND) * ratio
            offset = timedelta(microseconds=int(micros.to_integral_value(rounding=ROUND_HALF_EVEN)))
            stamp = times['C'] + offset
            return {
                'projection_id': f'{source}_{ratio:.3f}', 'source_duration': source,
                'anchor_id': 'C', 'ratio': float(ratio), 'projected_at_utc': _iso(stamp),
                **_position(reference, stamp, stamp, stamp),
            }

        projections = [project(source, ratio) for source in ('A_B', 'B_C') for ratio in sorted(ratios)]
        projections.sort(key=lambda event: (_utc(event['projected_at_utc']), event['projection_id']))
        symmetry = [project('A_C', Decimal('0.500'))]
        return {
            'status': 'ok', 'method': METHOD, 'timezone_calculation': 'UTC',
            'anchor_set_id': anchor_set_id, 'reference_at_utc': _iso(reference),
            'anchors': [anchors[label] for label in ('A', 'B', 'C')],
            'durations': {key + '_hours': span.total_seconds() / 3600 for key, span in durations.items()},
            'projections': projections, 'symmetry_projections': symmetry,
            'clusters': cluster_projections(projections + symmetry, generated_at_utc, anchor_set_id),
        }
    except (ValueError, TypeError, InvalidOperation, OverflowError) as exc:
        return _failure('error', str(exc), anchor_set_id if isinstance(anchor_set_id, str) else None)


def load_time_fibs(generated_at_utc, config_path=CONFIG_PATH):
    """File boundary; absent/broken config does not break other market data."""
    try:
        config = json.loads(Path(config_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return _failure('unavailable', 'Time-fib config file is missing')
    except (OSError, ValueError) as exc:
        return _failure('error', 'Cannot read time-fib config: ' + str(exc))
    return build_time_fibs(generated_at_utc, config)
