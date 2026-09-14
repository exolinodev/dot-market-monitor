"""Explicit verified schedule facts. Date-only sources never acquire guessed times."""
from datetime import date
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from observation_common import block, utc, iso


def scheduled_events(config, reference):
    now = utc(reference)
    if config.get('version') != 1 or not isinstance(config.get('events'), list):
        raise ValueError('Invalid event registry')
    events = []
    seen = set()
    for event in config['events']:
        key = event['id']
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError('Event IDs must be nonempty and unique')
        seen.add(key)
        source = urlsplit(event['source_url'])
        if source.scheme != 'https' or not source.netloc:
            raise ValueError('Verified event source must be an HTTPS URL')
        verified = utc(event['verified_at_utc'])
        # Information verified later cannot appear in an earlier snapshot replay.
        if verified > now:
            continue
        row = {key: event[key] for key in ('id', 'name', 'event_type', 'precision', 'source_url')}
        row.update(verified_at_utc=iso(verified), verification_age_days=(now-verified).total_seconds()/86400,
                   event_time_utc=None, minutes_until_event=None, minutes_since_event=None)
        if event['precision'] == 'date':
            start, end = date.fromisoformat(event['start_date']), date.fromisoformat(event['end_date'])
            if start > end:
                raise ValueError('Invalid event date range')
            today = now.tz_convert(ZoneInfo(event['timezone'])).date()
            row.update(start_date=start.isoformat(), end_date=end.isoformat(), timezone=event['timezone'],
                       state='upcoming' if today < start else 'expired' if today > end else 'active')
            sort_key = start.isoformat()
        elif event['precision'] == 'instant':
            stamp = utc(event['event_time_utc'])
            row.update(event_time_utc=iso(stamp), start_date=None, end_date=None, timezone='UTC',
                       state='upcoming' if now < stamp else 'expired' if now > stamp else 'active',
                       minutes_until_event=(stamp-now).total_seconds()/60 if now <= stamp else None,
                       minutes_since_event=(now-stamp).total_seconds()/60 if now > stamp else None)
            sort_key = iso(stamp)
        else:
            raise ValueError('Event precision must be date or instant')
        events.append((sort_key, row['id'], row))
    return block([e[2] for e in sorted(events)] if events else None,
                 reason=None if events else 'no_events_verified_as_of_snapshot', asof=now,
                 coverage={'scope': config['scope'], 'registry_version': config['version'],
                           'automatic_schedule_refresh': False})
