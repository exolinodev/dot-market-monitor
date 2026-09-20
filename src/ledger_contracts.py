"""Structural contracts at the disk boundary; economics are checked by replay."""
from functools import lru_cache
import json
from pathlib import Path
import jsonschema
from referencing import Registry, Resource


@lru_cache(maxsize=3)
def validator(kind):
    if kind not in ('plan', 'input', 'event'):
        raise ValueError('Unknown ledger contract')
    path = Path(__file__).resolve().parents[1] / 'schema' / ('ledger_' + kind + '.schema.json')
    schema = json.loads(path.read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    schemas = [json.loads(p.read_text()) for p in path.parent.glob('ledger_*.schema.json')]
    registry = Registry().with_resources((s['$id'], Resource.from_contents(s)) for s in schemas)
    return jsonschema.Draft202012Validator(schema, registry=registry, format_checker=jsonschema.FormatChecker())


def validate(value, kind):
    validator(kind).validate(value)
