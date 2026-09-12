"""Shared serialization, freshness and bounded public HTTP transport."""
from __future__ import annotations
import gzip
import hashlib
import json
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlencode, urlsplit
import numpy as np
import pandas as pd
import requests


def utcnow():
    return datetime.now(timezone.utc)


def finite(value):
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_safe(v) for v in value]
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def dumps(value, pretty=False):
    return json.dumps(json_safe(value), sort_keys=True, allow_nan=False,
                      indent=2 if pretty else None, separators=None if pretty else (',', ':')) + '\n'


def write_json(path, value, compressed=False, pretty=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = dumps(value, pretty).encode()
    if compressed:
        blob = gzip.compress(blob, mtime=0)
    temp = path.with_name(path.name + '.tmp')
    temp.write_bytes(blob)
    temp.replace(path)


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    blob = path.read_bytes()
    if path.suffix == '.gz':
        blob = gzip.decompress(blob)
    return json.loads(blob)


def freshness(timestamp, now, ttl_seconds):
    if timestamp is None:
        return {'source_timestamp_utc': None, 'age_seconds': None, 'fresh': False, 'status': 'unavailable'}
    try:
        ts = pd.Timestamp(timestamp)
        if ts.tzinfo is None:
            raise ValueError('Timestamp must include timezone')
        signed_age = (pd.Timestamp(now) - ts).total_seconds()
        valid = -5 <= signed_age <= ttl_seconds
        return {'source_timestamp_utc': ts.tz_convert('UTC').isoformat(),
                'age_seconds': max(0.0, signed_age), 'fresh': bool(valid),
                'status': 'ok' if valid else ('invalid' if signed_age < -5 else 'stale')}
    except (TypeError, ValueError, OverflowError):
        return {'source_timestamp_utc': None, 'age_seconds': None, 'fresh': False, 'status': 'invalid'}


class PublicHTTP:
    def __init__(self, session=None, timeout=12, retries=2, delay=0.15):
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': 'dot-market-monitor/2.0', 'Accept': 'application/json'})
        self.timeout, self.retries, self.delay = timeout, retries, delay
        self.records, self.raw = {}, {}
        self.host_failures = {}

    def get(self, url, params=None):
        full_url = url + ('?' + urlencode(sorted((params or {}).items())) if params else '')
        source_id = hashlib.sha256(full_url.encode()).hexdigest()[:16]
        host=urlsplit(url).netloc
        failures,blocked_until=self.host_failures.get(host,(0,0))
        if time.monotonic()<blocked_until:
            self.records[source_id]={'source_id':source_id,'url':full_url,'received_at_utc':utcnow().isoformat(),'status':'unavailable','http_status':None,'attempts':0,'error':'Host circuit open after three transient failures'}
            raise RuntimeError(f'{host}: circuit open after three transient failures')
        error = None
        status = None
        for attempt in range(self.retries + 1):
            retry_after = None
            try:
                if self.delay:
                    time.sleep(self.delay)
                response = self.session.get(url, params=params, timeout=self.timeout)
                if response.status_code == 429:
                    header = response.headers.get('Retry-After')
                    try:
                        retry_after = float(header)
                    except (TypeError, ValueError):
                        if header:
                            try:
                                retry_after = (parsedate_to_datetime(header) - utcnow()).total_seconds()
                            except (TypeError, ValueError):
                                pass
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, (dict, list)):
                    raise ValueError('Expected JSON object or array')
                if isinstance(payload, dict):
                    if payload.get('error') or payload.get('errors'):
                        raise ValueError(str(payload.get('error') or payload.get('errors')))
                    if isinstance(payload.get('result'), str) and payload['result'] != 'success':
                        raise ValueError('Unsuccessful API response')
                received = utcnow().isoformat()
                self.records[source_id] = {'source_id': source_id, 'url': full_url, 'received_at_utc': received,
                                           'status': 'ok', 'http_status': response.status_code, 'attempts': attempt + 1, 'error': None}
                self.host_failures[host]=(0,0)
                self.raw[source_id] = payload
                return payload
            except (requests.RequestException, ValueError, TypeError) as exc:
                # No request headers or credentials are logged.
                error = str(exc)
                status = getattr(getattr(exc, 'response', None), 'status_code', None)
                if status is not None and 400 <= status < 500 and status not in (408, 429):
                    break
                if attempt < self.retries:
                    time.sleep(min(15, max(0, retry_after) if retry_after is not None else 1.5 * 2 ** attempt))
        if status is None or status>=500 or status in (408,429):
            failures+=1
            self.host_failures[host]=(failures,time.monotonic()+300 if failures>=3 else 0)
        self.records[source_id] = {'source_id': source_id, 'url': full_url, 'received_at_utc': utcnow().isoformat(),
                                   'status': 'unavailable', 'http_status': None, 'attempts': attempt + 1, 'error': error}
        raise RuntimeError(f'{full_url}: {error}')
