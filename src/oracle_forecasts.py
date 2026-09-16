"""Strict forecast validation and atomic, create-only publication."""
import os
from pathlib import Path
import tempfile
import pandas as pd
from oracle_common import validate, canonical, digest, FEATURE_VERSION
from observation_common import utc, iso


def forecast_id(created, snapshot_hash):
    return utc(created).strftime('%Y%m%dT%H%M%SZ')+'-'+snapshot_hash[:12]+'-oracle-v3'


def validate_forecast(f, snapshot=None):
    validate(f, 'oracle_forecast.schema.json')
    created, ref = utc(f['created_at_utc']), utc(f['snapshot_generated_at_utc'])
    if not ref <= created <= ref+pd.Timedelta(minutes=90):
        raise ValueError('Forecast must use a snapshot at most 90 minutes old')
    if f['forecast_id'] != forecast_id(created, f['snapshot_sha256']):
        raise ValueError('Forecast ID must bind creation time and snapshot hash')
    setup = f['trade_setup']
    if setup['direction'] == 'NONE':
        if setup['status'] != 'NO_TRADE' or setup['trigger'] is not None or setup['failure'] is not None or setup['targets']:
            raise ValueError('NONE must abstain without executable barriers')
    else:
        trigger, failure = setup['trigger'], setup['failure']
        if setup['status'] == 'NO_TRADE' or trigger is None or failure is None or len(setup['targets']) != 3:
            raise ValueError('Executable forecast requires trigger, failure and T1/T2/T3')
        sign = 1 if setup['direction'] == 'LONG' else -1
        if not trigger['kind'].endswith('above' if sign == 1 else 'below'):
            raise ValueError('Trigger direction conflicts with setup')
        if failure['kind'] != ('touch_below' if sign == 1 else 'touch_above'):
            raise ValueError('Failure must be an opposing touch barrier')
        if sign*(trigger['price_usd']-failure['price_usd']) <= 0:
            raise ValueError('Failure must define positive initial risk')
        prior = trigger['price_usd']
        for idx, target in enumerate(setup['targets'], 1):
            if target['id'] != f'T{idx}' or sign*(target['price_usd']-prior) <= 0:
                raise ValueError('Targets must be unique, ordered and beyond entry')
            prior = target['price_usd']
        rr = abs(setup['targets'][0]['price_usd']-trigger['price_usd'])/abs(trigger['price_usd']-failure['price_usd'])
        provided = setup['asymmetry']['reward_risk_t1']
        if provided is None or abs(provided-rr)>1e-6:
            raise ValueError('T1 reward/risk must match the declared barriers')
    if snapshot is not None:
        ref_snapshot = snapshot['meta']['generated_at_utc']
        context = snapshot['markets']['DOTUSD'].get('oracle_context') or {}
        features = (context.get('current_features') or {}).get('features', {})
        if digest(snapshot) != f['snapshot_sha256'] or utc(ref_snapshot) != ref:
            raise ValueError('Forecast snapshot hash/time mismatch')
        if snapshot['markets']['DOTUSD']['observations']['config_sha256'] != f['measurement_config_sha256']:
            raise ValueError('Measurement config mismatch')
        if context.get('oracle_config_sha256') != f['oracle_config_sha256'] or context.get('feature_version') != f['oracle_feature_version']:
            raise ValueError('Oracle method/config mismatch')
        for key in f['evidence']['supporting_feature_ids']+f['evidence']['opposing_feature_ids']:
            if key not in features or features[key]['status'] != 'ok':
                raise ValueError('Evidence must reference available actual features')
        if f['regime'] in ('REVERSAL_ARMED','REVERSAL_TRIGGERED'):
            direction = 'downside' if setup['direction']=='LONG' else 'upside'
            gates = context['current_features']['evidence']['reversal_gates'][direction]
            if not gates['abc_ready'] or (f['regime']=='REVERSAL_TRIGGERED' and not gates['trigger_candidate']):
                raise ValueError('Reversal requires A+B+C and triggered requires new structure confirmation')
    return f


def create_only(path, value):
    """Hard-link an fsynced temporary file; atomic and fails if destination exists."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.oracle-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(canonical(value)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temp, path)
    finally:
        os.unlink(temp)
    return path


def persist_forecast(f, snapshot, directory, now):
    validate_forecast(f, snapshot)
    if abs((utc(now)-utc(f['created_at_utc'])).total_seconds()) > 120:
        raise ValueError('Publication timestamp differs from actual creation; historical write-back forbidden')
    day = utc(f['created_at_utc']).strftime('%Y/%m/%d')
    root = Path(directory)
    path = root/'forecasts'/day/(f['forecast_id']+'.json')
    # Bound input snapshot is also create-only; repeated same snapshot is verified.
    evidence = root/'inputs'/(f['snapshot_sha256']+'.json.gz')
    import gzip
    evidence.parent.mkdir(parents=True, exist_ok=True)
    payload = gzip.compress(canonical(snapshot).encode(), mtime=0)
    try:
        with evidence.open('xb') as stream: stream.write(payload)
    except FileExistsError:
        if gzip.decompress(evidence.read_bytes()) != canonical(snapshot).encode():
            raise ValueError('Existing snapshot evidence mismatch')
    return create_only(path, f)
