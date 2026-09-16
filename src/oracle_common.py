"""Versioned Oracle contracts and canonical serialization; no market interpretation."""
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import jsonschema
from referencing import Registry, Resource
from observation_common import utc, iso

ROOT = Path(__file__).resolve().parents[1]
FEATURE_VERSION = '3.0.0'
STRATEGY_VERSION = 'oracle-v3.0.0'
EVALUATOR_VERSION = '1.0.0'


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@lru_cache(maxsize=12)
def validator(filename):
    schemas = [json.loads(p.read_text()) for p in (ROOT/'schema').glob('oracle*.schema.json')]
    registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    schema = json.loads((ROOT/'schema'/filename).read_text())
    return jsonschema.Draft202012Validator(schema, registry=registry,
        format_checker=jsonschema.FormatChecker())


def validate(value, filename):
    canonical(value)  # Reject NaN/Infinity, including inside extensible metadata.
    validator(filename).validate(value)
    return value


def configuration(path=None):
    cfg = json.loads(Path(path or ROOT/'config/oracle.json').read_text())
    validate(cfg, 'oracle.config.schema.json')
    if cfg['analog_min_samples'] > cfg['analog_k']:
        raise ValueError('analog_min_samples exceeds K')
    return cfg, digest(cfg)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def value(features, key):
    f = features.get(key, {})
    return f.get('value') if f.get('status') == 'ok' else None


def compatible(a, b):
    return all(a.get(k) == b.get(k) for k in
               ('schema_version', 'feature_version', 'oracle_config_sha256', 'measurement_config_sha256'))
