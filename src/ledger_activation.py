"""Explicit, offline paper initialization from a reviewed current-quarter plan."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile

import jsonschema
from cycles import iso, utc
from ledger import digest, validate_config, PRIORITY, replay
from ledger_market import row_events
from ledger_contracts import validate
from ledger_store import initialize, append_events, verify


def _rows(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError('Regular archived market evidence required: ' + str(path))
    return [json.loads(line) for line in path.read_text().splitlines()]


def prepare(directory, baseline, now):
    """Read-only preview; never infer a new epoch for an existing account."""
    root, now = Path(directory), utc(now)
    if (root / 'ledger').exists() or (root / 'ledger').is_symlink():
        raise ValueError('Ledger directory already exists; initialization never resets it')
    validate_config(baseline)
    if not baseline['funding_convention_verified']:
        raise ValueError('Documented funding convention required for paper activation')
    boundary = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    name = 'intraday/' + boundary.strftime('%Y/%m/%d.jsonl')
    matches = [r for r in _rows(root / name) if utc(r['meta']['cycle_boundary_utc']) == boundary]
    if len(matches) != 1:
        raise ValueError('Exactly one archived current quarter required')
    quarter = matches[0]
    schema = json.loads((Path(__file__).parents[1] / 'schema/intraday.schema.json').read_text())
    jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker()).validate(quarter)
    meta, quote = quarter['meta'], quarter['sources']['perp_book']
    epoch = utc(quote['received_at_utc'])
    if meta['fresh'] is not True or meta['status'] not in ('ok', 'partial') or quote['status'] != 'ok':
        raise ValueError('Fresh usable current-quarter quote required')
    if not boundary <= epoch <= utc(meta['generated_at_utc']) <= now:
        raise ValueError('Activation quote outside current archived evidence')
    if (now - epoch).total_seconds() > baseline['execution_quote_max_age_seconds']:
        raise ValueError('Activation quote exceeds configured age')
    if baseline.get('ledger_epoch_utc') and utc(baseline['ledger_epoch_utc']) != epoch:
        raise ValueError('Configured epoch differs from current quote')
    config = deepcopy(baseline)
    config.update(enabled=True, ledger_epoch_utc=iso(epoch))
    validate_config(config)
    # Epoch is the quote reception time. Earlier candles cannot enter the account.
    seed = [e for e in row_events(name, quarter, epoch) if e['type'] == 'spread' and utc(e['at_utc']) == epoch]
    funding_name = 'funding/' + epoch.strftime('%Y/%m.jsonl')
    hour = epoch.replace(minute=0, second=0, microsecond=0)
    rates = [r for r in _rows(root / funding_name) if utc(r['timestamp']) == hour]
    if len(rates) != 1:
        raise ValueError('Exactly one historical funding rate for activation hour required')
    seed += row_events(funding_name, rates[0], epoch)
    if len(seed) != 2:
        raise ValueError('Activation requires one funding rate and one recorded spread')
    seed.sort(key=lambda e: (utc(e['at_utc']), PRIORITY[e['type']], e['event_id']))
    for event in seed:
        validate(event, 'input')
    replay(config, iso(epoch), seed)  # Preview itself must be executable.
    plan = {'mode': 'paper', 'baseline_config_sha256': digest(baseline),
            'config': config, 'epoch_utc': iso(epoch), 'seed_inputs': seed}
    return {'plan_sha256': digest(plan), 'plan': plan}


def activate(directory, baseline, now, expected_plan_sha256):
    """Recheck evidence, stage and replay fully, then publish a new local directory.

    The caller must serialize this operation with any collector/writer. No Git,
    workflow, account API or exchange operation is performed here.
    """
    preview = prepare(directory, baseline, now)
    if preview['plan_sha256'] != expected_plan_sha256:
        raise ValueError('Activation plan changed; review a new preview')
    plan, root = preview['plan'], Path(directory)
    with tempfile.TemporaryDirectory(prefix='.paper-init-', dir=root) as staging:
        initialize(staging, plan['config'], plan['epoch_utc'])
        append_events(staging, plan['seed_inputs'])
        staged = verify(staging)
        # A concurrently created nonempty ledger cannot be replaced by rename.
        # Refuse even an empty/pre-existing/symlink target explicitly.
        if (root / 'ledger').exists() or (root / 'ledger').is_symlink():
            raise ValueError('Ledger appeared during initialization; refusing replacement')
        (Path(staging) / 'ledger').rename(root / 'ledger')
    return {'mode': 'paper', 'plan_sha256': preview['plan_sha256'],
            'epoch_utc': plan['epoch_utc'], 'state_sha256': staged['state']['state_sha256'],
            'equity_usd': staged['state']['equity_usd'], 'events': len(staged['records'])}
