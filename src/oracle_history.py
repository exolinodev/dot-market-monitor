"""Actual hourly feature observations with archived inputs/config, never synthetic backfill."""
import copy
import pandas as pd
from common import read_json, write_json
from oracle_common import digest, validate, configuration, compatible
from observation_common import utc
from oracle_features import build_features


class FeatureArchive:
    def __init__(self, path, reference):
        self.path = path
        self.content = read_json(path, {'schema_version':1,'records':[],'configurations':{}})
        if self.content.get('schema_version') != 1:
            raise ValueError('Unknown feature archive version')
        previous = None
        for key, cfg in self.content['configurations'].items():
            validate(cfg, 'oracle.config.schema.json')
            if digest(cfg) != key: raise ValueError('Oracle config fingerprint mismatch')
        for row in self.records:
            record = row['record']
            validate(record, 'oracle_features.schema.json')
            stamp = utc(record['reference_at_utc'])
            if stamp > utc(reference): raise ValueError('Future feature observation')
            if previous is not None and stamp.floor('h') <= previous.floor('h'):
                raise ValueError('Feature archive requires unique ordered hours')
            previous = stamp
            if record['oracle_config_sha256'] not in self.content['configurations']:
                raise ValueError('Missing archived config')
            if utc(row['inputs']['reference_at_utc']) != stamp:
                raise ValueError('Archived input/reference mismatch')
            if row['inputs']['observations'].get('config_sha256') != record['measurement_config_sha256']:
                raise ValueError('Archived measurement config mismatch')
            if digest(row['inputs']) != record['input_sha256']:
                raise ValueError('Feature input fingerprint mismatch')

    @property
    def records(self):
        return self.content['records']

    def update(self, record, inputs, cfg):
        validate(record, 'oracle_features.schema.json')
        if record['oracle_config_sha256'] != digest(cfg) or record['input_sha256'] != digest(inputs):
            raise ValueError('Archive fingerprint mismatch')
        now = utc(record['reference_at_utc'])
        if any(utc(r['record']['reference_at_utc']) > now for r in self.records):
            raise ValueError('Cannot replace a future observation')
        rows = {utc(r['record']['reference_at_utc']).floor('h'):r for r in self.records
                if utc(r['record']['reference_at_utc']) >= now-pd.Timedelta(days=cfg['history_days'])}
        rows[now.floor('h')] = {'record':copy.deepcopy(record), 'inputs':copy.deepcopy(inputs)}
        self.content['records'] = [rows[k] for k in sorted(rows)][-cfg['history_days']*24:]
        self.content['configurations'][digest(cfg)] = copy.deepcopy(cfg)
        # Keep configs that remain referenced; orphan versions do not affect features.
        used = {r['record']['oracle_config_sha256'] for r in self.records}
        self.content['configurations'] = {k:v for k,v in self.content['configurations'].items() if k in used}
        write_json(self.path, self.content, compressed=True)

    def replay(self):
        result = []
        for row in self.records:
            cfg = self.content['configurations'][row['record']['oracle_config_sha256']]
            actual = build_features(row['inputs'], result, cfg)
            result.append(actual)
        return result
