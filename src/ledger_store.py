"""Replay-verified, create-only ledger plans/trades plus an append-only journal.

There is no exchange access. Initialization is explicit; no collector imports this
module yet. A crash may leave derived views behind the journal; verification then
fails closed and the explicit rebuild operation restores views without changing
inputs, effects, plans or trades.
"""
from collections import defaultdict
import json
from pathlib import Path
import re

from common import write_json
from cycles import utc
from ledger import canonical, digest, initial_state, performance, plan_instruction, replay, state_hash
from oracle_forecasts import create_only
from ledger_contracts import validate


def _create_or_verify(path, value):
    if path.exists():
        if path.is_symlink() or json.loads(path.read_text()) != value:
            raise ValueError('Immutable ledger artifact differs: ' + str(path))
    else:
        create_only(path, value)


def initialize(directory, config, epoch):
    root = Path(directory) / 'ledger'
    if not config.get('enabled'):
        raise ValueError('Paper ledger must be explicitly enabled before initialization')
    state = initial_state(config, epoch)
    genesis = {'config': config, 'epoch_utc': state['epoch_utc'], 'initial_state_sha256': state['state_sha256']}
    if (root / 'genesis.json').exists():
        _create_or_verify(root / 'genesis.json', genesis)
        return verify(directory)['state']
    create_only(root / 'genesis.json', genesis)
    _create_or_verify(root / 'states' / (digest(state) + '.json'), state)
    write_json(root / 'state.json', state)
    write_json(root / 'performance.json', performance(state, config))
    return state


def persist_plan(directory, plan, bound_state):
    validate(plan, 'plan')
    root = Path(directory) / 'ledger'
    current = verify(directory)
    config = current['genesis']['config']
    known = {digest(initial_state(config, current['genesis']['epoch_utc']))} | {r['state_document_sha256'] for r in current['records']}
    if digest(bound_state) not in known:
        raise ValueError('Plan binds an unreachable ledger state')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', plan['forecast_id']):
        raise ValueError('Invalid plan identity')
    expected = plan_instruction(plan['forecast_id'], plan['created_at_utc'],
                                [p['order'] for p in plan['orders']], plan['management'],
                                bound_state, config, plan['quote'], plan['strategy_version'])
    if plan != expected:
        raise ValueError('Plan size or bound state differs from deterministic calculation')
    _create_or_verify(root / 'states' / (digest(bound_state) + '.json'), bound_state)
    _create_or_verify(root / 'plans' / (plan['forecast_id'] + '.json'), plan)


def _inputs_and_records(root):
    records = []
    for path in sorted((root / 'events').glob('*/*/*.jsonl')):
        if path.is_symlink():
            raise ValueError('Journal must be regular files')
        for line in path.read_text().splitlines():
            record = json.loads(line)
            validate(record, 'event')
            expected = utc(record['input']['at_utc']).strftime('%Y/%m/%d.jsonl')
            if path.relative_to(root / 'events').as_posix() != expected:
                raise ValueError('Event in wrong archive day')
            records.append(record)
    return [record['input'] for record in records], records


def _check_plans(root, inputs, config):
    for event in inputs:
        if event['type'] != 'instruction':
            continue
        plan = event['plan']
        validate(plan, 'plan')
        if not re.fullmatch(r'[A-Za-z0-9_-]+', plan['forecast_id']):
            raise ValueError('Invalid plan identity')
        stored = json.loads((root / 'plans' / (plan['forecast_id'] + '.json')).read_text())
        state_hash = plan['ledger_state_sha256']
        if not re.fullmatch(r'[a-f0-9]{64}', state_hash):
            raise ValueError('Invalid ledger-state binding')
        bound = json.loads((root / 'states' / (state_hash + '.json')).read_text())
        expected = plan_instruction(plan['forecast_id'], plan['created_at_utc'],
                                    [p['order'] for p in plan['orders']], plan['management'],
                                    bound, config, plan['quote'], plan['strategy_version'])
        if plan != stored or plan != expected:
            raise ValueError('Journal instruction differs from verified plan')


def _check_reachable_bindings(genesis, records):
    known = {digest(initial_state(genesis['config'], genesis['epoch_utc']))}
    for record in records:
        if record['input']['type'] == 'instruction' and record['input']['plan']['ledger_state_sha256'] not in known:
            raise ValueError('Plan binds an unreachable or future ledger state')
        known.add(record['state_document_sha256'])


def derive(directory):
    root = Path(directory) / 'ledger'
    genesis = json.loads((root / 'genesis.json').read_text())
    config = genesis['config']
    if initial_state(config, genesis['epoch_utc'])['state_sha256'] != genesis['initial_state_sha256']:
        raise ValueError('Genesis hash mismatch')
    inputs, saved_records = _inputs_and_records(root)
    _check_plans(root, inputs, config)
    state, records = replay(config, genesis['epoch_utc'], inputs)
    _check_reachable_bindings(genesis, records)
    if records != saved_records:
        raise ValueError('Journal effects, input hash or state chain differ from replay')
    trades = {e['trade_id']: e['trade'] for r in records for e in r['effects'] if e['type'] == 'TRADE_CLOSED'}
    return {'state': state, 'performance': performance(state, config), 'trades': trades,
            'inputs': inputs, 'records': records, 'genesis': genesis}


def verify(directory):
    root = Path(directory) / 'ledger'
    result = derive(directory)
    for name in ('state', 'performance'):
        if json.loads((root / (name + '.json')).read_text()) != result[name]:
            raise ValueError('Ledger ' + name + ' differs from replay')
    known = {digest(initial_state(result['genesis']['config'], result['genesis']['epoch_utc']))} | {r['state_document_sha256'] for r in result['records']}
    for path in (root / 'states').glob('*.json'):
        bound = json.loads(path.read_text())
        if path.is_symlink() or path.stem != digest(bound) or path.stem not in known or bound.get('state_sha256') != state_hash(bound):
            raise ValueError('Invalid or unreachable archived ledger state')
    # Pending plans are evidence too, even before their instruction is consumed.
    for path in (root / 'plans').glob('*.json'):
        plan = json.loads(path.read_text())
        if path.is_symlink() or path.stem != plan['forecast_id'] or plan['ledger_state_sha256'] not in known:
            raise ValueError('Invalid or unreachable archived plan')
        _check_plans(root, [{'type': 'instruction', 'plan': plan}], result['genesis']['config'])
    files = {p.stem: json.loads(p.read_text()) for p in (root / 'trades').glob('*.json')}
    if files != result['trades']:
        raise ValueError('Trade archive differs from replay')
    return result


def rebuild_views(directory):
    root = Path(directory) / 'ledger'
    result = derive(directory)
    for ident, trade in result['trades'].items():
        _create_or_verify(root / 'trades' / (ident + '.json'), trade)
    write_json(root / 'state.json', result['state'])
    write_json(root / 'performance.json', result['performance'])
    verify(directory)
    return result['state']


def append_events(directory, new_inputs):
    """Validate entire proposed transition before touching any immutable evidence."""
    root = Path(directory) / 'ledger'
    before = verify(directory)
    config, epoch = before['genesis']['config'], before['genesis']['epoch_utc']
    # Replayed, exact duplicates are no-ops; conflicting IDs fail in replay.
    new_inputs = list(new_inputs)
    for event in new_inputs:
        validate(event, 'input')
    inputs = before['inputs'] + new_inputs
    _check_plans(root, inputs, config)
    state, records = replay(config, epoch, inputs)
    _check_reachable_bindings(before['genesis'], records)
    if records[:len(before['records'])] != before['records']:
        raise ValueError('Existing journal prefix cannot change')
    additions = records[len(before['records']):]
    grouped = defaultdict(list)
    for record in additions:
        grouped[utc(record['input']['at_utc']).strftime('%Y/%m/%d.jsonl')].append(record)
    for name, rows in grouped.items():
        path = root / 'events' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        previous = path.read_bytes() if path.exists() else b''
        raw = previous + ''.join(canonical(row) + '\n' for row in rows).encode()
        temporary = path.with_suffix('.jsonl.tmp')
        temporary.write_bytes(raw)
        temporary.replace(path)  # Exact old prefix, complete new lines, atomic per day.
    for record in additions:
        for effect in record['effects']:
            if effect['type'] == 'TRADE_CLOSED':
                _create_or_verify(root / 'trades' / (effect['trade_id'] + '.json'), effect['trade'])
    write_json(root / 'state.json', state)
    write_json(root / 'performance.json', performance(state, config))
    verify(directory)
    return state
