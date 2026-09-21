"""Dependency-free UTC cycle identity shared by dispatch, wait and collectors."""
from datetime import datetime, timedelta, timezone


def utc(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00')) if isinstance(value, str) else value
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def iso(value):
    return utc(value).isoformat().replace('+00:00', 'Z')


def resolve(now, kind=None, boundary=None, event='schedule'):
    now = utc(now)
    if boundary:
        point = utc(boundary)
        if point.second or point.microsecond or point.minute % 15:
            raise ValueError('Boundary must align to a UTC quarter hour')
        if (point - now).total_seconds() > 150:
            raise ValueError('Boundary more than 150 seconds in future')
    else:
        point = now.replace(minute=(now.minute // 15) * 15, second=0, microsecond=0)
    kind = kind or ('full' if event in ('push', 'workflow_dispatch') and not boundary
                    else 'full' if point.minute == 0 else 'light')
    if kind not in ('full', 'light'):
        raise ValueError('Unknown run kind')
    if kind == 'full' and point.minute:
        if boundary:
            raise ValueError('Full run requires an hourly boundary')
        point = point.replace(minute=0)
    if kind == 'light' and point.minute == 0:
        raise ValueError('Hourly boundary requires full run')
    return kind, point


def metadata(kind, boundary, started):
    lag = (utc(started) - utc(boundary)).total_seconds()
    return {'run_kind': kind, 'cycle_boundary_utc': iso(boundary),
            'boundary_lag_seconds': round(lag, 3),
            'late': lag > (360 if kind == 'full' else 240)}
