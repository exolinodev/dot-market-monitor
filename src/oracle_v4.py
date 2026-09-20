"""Strict executable forecast contract; model numbers never determine quantity.

Publication is not enabled by this module. The writer must supply the actual
snapshot, state and genesis configuration from the bound trusted commit, then
persist the returned plan through the replay-verifying ledger store.
"""
from copy import deepcopy
from datetime import timedelta
from ledger import digest, number, plan_instruction, state_hash, validate_config
from oracle_common import validate
from cycles import utc, iso


def forecast_id(created, snapshot_hash):
    return utc(created).strftime('%Y%m%dT%H%M%SZ') + '-' + snapshot_hash[:12] + '-oracle-v4'


def validate_contract(f, snapshot=None):
    validate(f, 'oracle_forecast_v4.schema.json')
    created, reference = utc(f['created_at_utc']), utc(f['snapshot_generated_at_utc'])
    if not reference <= created <= reference + timedelta(minutes=90):
        raise ValueError('Forecast must use a snapshot at most 90 minutes old')
    if f['forecast_id'] != forecast_id(created, f['snapshot_sha256']):
        raise ValueError('Forecast ID must bind creation time and snapshot hash')
    stance = f['decision']['stance']
    if (stance == 'FLAT') != (len(f['orders']) == 0):
        raise ValueError('FLAT requires no entry; directional stance requires exactly one order')
    if f['orders'] and f['orders'][0]['side'] != stance:
        raise ValueError('Order side conflicts with decision')
    if f['orders'] and f['orders'][0]['client_id'] != f['forecast_id'] + '-1':
        raise ValueError('Order identity must bind forecast')
    if snapshot is None:
        return f
    meta = snapshot['meta']
    if digest(snapshot) != f['snapshot_sha256'] or utc(meta['generated_at_utc']) != reference:
        raise ValueError('Forecast snapshot hash/time mismatch')
    if meta.get('run_kind') != 'full' or utc(meta['cycle_boundary_utc']) != created.replace(minute=0, second=0, microsecond=0):
        raise ValueError('v4 requires the current full-hour boundary snapshot')
    if meta.get('fresh') is not True or meta.get('status') not in ('ok', 'partial'):
        raise ValueError('Fresh usable snapshot required')
    dot = snapshot['markets']['DOTUSD']
    context = dot.get('oracle_context') or {}
    if dot['observations']['config_sha256'] != f['measurement_config_sha256']:
        raise ValueError('Measurement config mismatch')
    if context.get('oracle_config_sha256') != f['oracle_config_sha256'] or context.get('feature_version') != f['oracle_feature_version']:
        raise ValueError('Oracle method/config mismatch')
    features = (context.get('current_features') or {}).get('features', {})
    cited = f['evidence']['supporting_feature_ids'] + f['evidence']['opposing_feature_ids']
    if any(key not in features or features[key]['status'] != 'ok' for key in cited):
        raise ValueError('Evidence must reference available actual features')
    regime = f['decision']['regime']
    if regime in ('REVERSAL_ARMED', 'REVERSAL_TRIGGERED'):
        if stance == 'FLAT':
            raise ValueError('Executable reversal requires a direction')
        gates = context['current_features']['evidence']['reversal_gates']['downside' if stance == 'LONG' else 'upside']
        if not gates['abc_ready'] or (regime == 'REVERSAL_TRIGGERED' and not gates['trigger_candidate']):
            raise ValueError('Reversal requires A+B+C and structure confirmation')
    execution = dot.get('execution_context') or {}
    if execution.get('status') != 'ok' or execution.get('instrument') != 'PF_DOTUSD':
        raise ValueError('Verified execution context required')
    if execution.get('ledger_state_sha256') != f['ledger_state_sha256']:
        raise ValueError('Snapshot execution context ledger binding mismatch')
    return f


def _decimal_instructions(items):
    """Preserve submitted forecast; normalize a separate engine instruction copy."""
    result = deepcopy(items)
    def convert(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in ('price_usd', 'stop_usd', 'stop_after_t1_usd', 'fraction') and value is not None:
                    obj[key] = number(value)
                else:
                    convert(value)
        elif isinstance(obj, list):
            for value in obj:
                convert(value)
    convert(result)
    return result


def prepare_plan(f, snapshot, bound_state, config):
    """Validate all bindings and derive the only admissible executable plan."""
    validate_contract(f, snapshot)
    validate_config(config)
    if not config['enabled']:
        raise ValueError('Paper ledger is disabled')
    if bound_state.get('state_sha256') != state_hash(bound_state) or digest(bound_state) != f['ledger_state_sha256']:
        raise ValueError('Forecast ledger state hash mismatch')
    execution = snapshot['markets']['DOTUSD']['execution_context']
    if execution.get('ledger_config_sha256') != digest(config):
        raise ValueError('Execution context configuration mismatch')
    quote = execution['quote']
    # Prices must originate at or before this snapshot, never from writer time.
    if quote.get('status') != 'ok' or not max(utc(bound_state['epoch_utc']), utc(snapshot['meta']['cycle_boundary_utc'])) <= utc(quote['asof_utc']) <= utc(snapshot['meta']['generated_at_utc']):
        raise ValueError('Execution quote time outside bound evidence')
    return plan_instruction(f['forecast_id'], f['created_at_utc'],
        _decimal_instructions(f['orders']), _decimal_instructions(f['management']),
        bound_state, config, {'bid': number(quote['bid']), 'ask': number(quote['ask'])}, f['strategy_version'])


def verify_forecast_plan(f, snapshot, directory):
    """Bind a published forecast to its immutable, independently sized plan."""
    import json
    from pathlib import Path
    root = Path(directory) / 'ledger'
    genesis = json.loads((root / 'genesis.json').read_text())
    bound = json.loads((root / 'states' / (f['ledger_state_sha256'] + '.json')).read_text())
    plan = json.loads((root / 'plans' / (f['forecast_id'] + '.json')).read_text())
    if prepare_plan(f, snapshot, bound, genesis['config']) != plan:
        raise ValueError('Published v4 forecast differs from its executable ledger plan')
    return plan
