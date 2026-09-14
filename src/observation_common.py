"""Shared contracts for descriptive observations; never read the host clock."""
import pandas as pd


def utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError('An explicit timezone is required')
    return stamp.tz_convert('UTC')


def iso(value):
    return utc(value).isoformat().replace('+00:00', 'Z')


def block(data=None, *, status=None, reason=None, sources=(), asof=None, coverage=None):
    return {'status': status or ('ok' if data is not None else 'unavailable'),
            'reason': reason, 'source_ids': list(sources),
            'asof_utc': iso(asof) if asof is not None else None,
            'coverage': coverage or {}, 'data': data}


def regular(frame, minutes):
    return (frame is not None and not frame.empty and frame.index.tz is not None
            and frame.index.is_monotonic_increasing and not frame.index.has_duplicates
            and (frame.index.to_series().diff().dropna() == pd.Timedelta(minutes=minutes)).all())


def last_complete_frame(frame, minutes, reference):
    """The caller has already excluded bars open at their source receipt time."""
    if frame is None or frame.empty:
        return None
    cutoff = utc(reference).floor(f'{minutes}min')
    work = frame[frame.index + pd.Timedelta(minutes=minutes) <= cutoff]
    if work.empty or work.index[-1] + pd.Timedelta(minutes=minutes) != cutoff:
        return None
    return work
