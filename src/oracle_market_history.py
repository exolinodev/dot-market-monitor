"""Retain matured market-state labels after their minute candles leave the cache.

This archive is independent of model forecasts. Labels are filled once per state
and horizon, never inferred from missing candles or overwritten by later prices.
"""
import pandas as pd
from common import read_json, write_json
from oracle_common import digest, validate
from oracle_evaluator import forward_outcome
from observation_common import utc, iso

IDENTITY_FIELDS=('schema_version','feature_version','oracle_config_sha256',
                 'measurement_config_sha256','reference_at_utc','input_sha256')


def state_identity(record):
    return {k:record[k] for k in IDENTITY_FIELDS}


def state_id(record):
    return digest(state_identity(record))


def market_outcome_history(path, records, frames, reference, days=365, persist=False, previous=None):
    empty={'schema_version':1,'methodology':'retained-market-labels-v1','records':[]}
    archive=read_json(path,empty) if previous is None else {**empty,'records':list(previous.values())}
    validate(archive,'oracle_market_history.schema.json')
    ref=utc(reference)
    retained={}
    for row in archive['records']:
        key=digest(row['identity'])
        if key!=row['state_id'] or key in retained: raise ValueError('Invalid or duplicate market-state identity')
        stamp=utc(row['identity']['reference_at_utc'])
        if stamp>ref: raise ValueError('Future market-state labels')
        for h,label in row['labels'].items():
            out=label['outcome']
            expected_start=stamp.ceil('min')
            expected_end=expected_start+pd.Timedelta(hours=int(h[:-1]))
            if (utc(label['evaluated_at_utc'])>ref or utc(out['end_at_utc'])>utc(label['evaluated_at_utc'])
                    or utc(out['anchor_at_utc'])!=expected_start or utc(out['end_at_utc'])!=expected_end):
                raise ValueError('Future or misaligned market outcome label')
        if stamp>=ref-pd.Timedelta(days=days): retained[key]=row
    for record in records:
        stamp=utc(record['reference_at_utc'])
        if stamp>ref: raise ValueError('Future state cannot be labelled')
        if stamp<ref-pd.Timedelta(days=days): continue
        key=state_id(record)
        row=retained.get(key,{'state_id':key,'identity':state_identity(record),'labels':{}})
        for h in (1,4,12):
            if f'{h}h' in row['labels']: continue
            out=forward_outcome(stamp,h,frames,ref)
            if out['status']=='ok':
                row['labels'][f'{h}h']={'evaluated_at_utc':iso(ref),'outcome':out}
        if row['labels']: retained[key]=row
    archive['records']=sorted(retained.values(),key=lambda r:(r['identity']['reference_at_utc'],r['state_id']))
    validate(archive,'oracle_market_history.schema.json')
    if persist: write_json(path,archive,compressed=True)
    return retained
