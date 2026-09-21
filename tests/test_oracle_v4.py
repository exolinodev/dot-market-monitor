from copy import deepcopy
import json
import pytest
from oracle_v4 import forecast_id, validate_contract, prepare_plan
from ledger import digest, replay
from test_ledger import config, seed, order, candle, EPOCH
from test_oracle import fixture_forecast


def fixture():
    cfg = config()
    state, _ = seed(cfg)
    snapshot = {'meta': {'generated_at_utc': EPOCH, 'cycle_boundary_utc': EPOCH,
                        'run_kind': 'full', 'fresh': True, 'status': 'ok'},
        'markets': {'DOTUSD': {'observations': {'config_sha256': 'b' * 64},
            'oracle_context': {'oracle_config_sha256': 'c' * 64, 'feature_version': '3.0.0',
                              'current_features': {'features': {'flow': {'status': 'ok'}}}},
            'execution_context': {'status': 'ok', 'instrument': 'PF_DOTUSD',
                'ledger_state_sha256': digest(state), 'ledger_config_sha256': digest(cfg),
                'quote': {'bid': '.9998', 'ask': '1.0002', 'asof_utc': EPOCH, 'status': 'ok'}}}}}
    old = fixture_forecast()
    keys = ['market', 'measurement_config_sha256', 'oracle_feature_version', 'forecast_horizons',
            'evidence', 'calibration_context', 'text_summary']
    f = {k: old[k] for k in keys}
    f.update(schema_version=2, strategy_version='oracle-v4.0.0', created_at_utc=EPOCH,
             snapshot_generated_at_utc=EPOCH, snapshot_sha256=digest(snapshot),
             oracle_config_sha256='c' * 64, ledger_state_sha256=digest(state), primary_market='PF_DOTUSD',
             decision={'stance': 'LONG', 'regime': 'EXHAUSTION_WATCH', 'summary': 'Synthetic order'},
             management=[])
    f['forecast_id'] = forecast_id(EPOCH, f['snapshot_sha256'])
    entry = order(forecast=f['forecast_id'])
    entry['entry']['price_usd'] = 1.0
    entry['stop_usd'] = .99
    for t in entry['targets']:
        t['price_usd'] = float(t['price_usd']); t['fraction'] = float(t['fraction'])
    f['orders'] = [entry]
    return f, snapshot, state, cfg


def rebind(f, snapshot):
    f['snapshot_sha256'] = digest(snapshot)
    f['forecast_id'] = forecast_id(f['created_at_utc'], f['snapshot_sha256'])
    if f['orders']:
        f['orders'][0]['client_id'] = f['forecast_id'] + '-1'


def test_v4_derives_quantity_and_preserves_exact_numeric_forecast():
    f, snapshot, state, cfg = fixture()
    before = json.dumps(f, sort_keys=True)
    plan = prepare_plan(f, snapshot, state, cfg)
    assert plan['orders'][0]['size']['quantity']
    assert plan['orders'][0]['order']['entry']['price_usd'] == '1'
    assert json.dumps(f, sort_keys=True) == before
    assert plan['ledger_state_sha256'] == digest(state)
    assert plan['effective_at_utc'] == '2026-09-20T20:01:00Z'


def test_v4_requires_order_or_explicit_flat_and_rejects_model_quantity():
    f, snapshot, state, cfg = fixture()
    f['decision']['stance'] = 'FLAT'
    with pytest.raises(ValueError, match='FLAT'): prepare_plan(f, snapshot, state, cfg)
    f['orders'] = []
    assert prepare_plan(f, snapshot, state, cfg)['orders'] == []
    f, snapshot, state, cfg = fixture()
    f['orders'][0]['quantity'] = 10000
    with pytest.raises(Exception, match='quantity'): prepare_plan(f, snapshot, state, cfg)


def test_v4_refuses_stale_boundary_and_bad_quote_even_if_hashes_are_consistent():
    f, snapshot, state, cfg = fixture()
    snapshot['meta']['cycle_boundary_utc'] = '2026-09-20T19:00:00Z'
    rebind(f, snapshot)
    with pytest.raises(ValueError, match='current full-hour'): prepare_plan(f, snapshot, state, cfg)
    f, snapshot, state, cfg = fixture()
    snapshot['markets']['DOTUSD']['execution_context']['quote']['asof_utc'] = '2026-09-20T20:00:01Z'
    rebind(f, snapshot)
    with pytest.raises(ValueError, match='quote time'): prepare_plan(f, snapshot, state, cfg)


def test_v4_rejects_ledger_or_config_substitution_and_disabled_account():
    f, snapshot, state, cfg = fixture()
    fake = deepcopy(state); fake['cash_usd'] = '50000'
    with pytest.raises(ValueError, match='state hash'): prepare_plan(f, snapshot, fake, cfg)
    wrong = deepcopy(cfg); wrong['risk_fraction_per_trade'] = '.02'
    with pytest.raises(ValueError, match='configuration mismatch'): prepare_plan(f, snapshot, state, wrong)
    wrong['enabled'] = False
    with pytest.raises(ValueError, match='disabled'): prepare_plan(f, snapshot, state, wrong)


def test_v4_requires_management_for_every_bound_object():
    f, snapshot, state, cfg = fixture()
    plan = prepare_plan(f, snapshot, state, cfg)
    _, events = seed(cfg)
    events += [{'type': 'instruction', 'event_id': 'entry', 'at_utc': plan['effective_at_utc'], 'plan': plan}, candle(1)]
    state, _ = replay(cfg, EPOCH, events)
    f['created_at_utc'] = f['snapshot_generated_at_utc'] = '2026-09-20T20:02:00Z'
    f['orders'] = []; f['decision']['stance'] = 'FLAT'; f['ledger_state_sha256'] = digest(state)
    snapshot['meta']['generated_at_utc'] = f['created_at_utc']
    snapshot['markets']['DOTUSD']['execution_context']['ledger_state_sha256'] = digest(state)
    rebind(f, snapshot)
    with pytest.raises(ValueError, match='Exactly one'): prepare_plan(f, snapshot, state, cfg)
    f['management'] = [{'position_id': state['position']['position_id'], 'action': 'CLOSE', 'type': 'MARKET'}]
    assert prepare_plan(f, snapshot, state, cfg)['management'] == f['management']


def test_v4_cited_feature_must_be_available():
    f, snapshot, state, cfg = fixture()
    f['evidence']['supporting_feature_ids'] = ['invented']
    with pytest.raises(ValueError, match='actual features'): prepare_plan(f, snapshot, state, cfg)


def test_v4_publication_is_bound_to_reachable_account_and_loadable(tmp_path):
    from ledger_store import initialize, append_events
    from oracle_forecasts import persist_forecast
    from oracle_context import load_forecasts
    from oracle_v4 import verify_forecast_plan
    f, snapshot, state, cfg = fixture()
    initialize(tmp_path, cfg, EPOCH)
    append_events(tmp_path, seed(cfg)[1])
    path = persist_forecast(f, snapshot, tmp_path / 'oracle', EPOCH,
                           ledger_state=state, ledger_config=cfg)
    assert json.loads(path.read_text()) == f
    assert load_forecasts(tmp_path, EPOCH) == [f]
    assert verify_forecast_plan(f, snapshot, tmp_path)['orders'][0]['size']['quantity']
    with pytest.raises(FileExistsError):
        persist_forecast(f, snapshot, tmp_path / 'oracle', EPOCH, ledger_state=state, ledger_config=cfg)


def test_v4_spot_evaluation_preserves_direction_without_inventing_fills():
    from oracle_evaluator import evaluate_forecast
    from oracle_common import validate
    from oracle_scorecard import scorecard
    from test_oracle import bars
    f, snapshot, state, cfg = fixture()
    out = evaluate_forecast(f, {1: bars(start=EPOCH)}, '2026-09-21T08:00:00Z')
    validate(out, 'oracle_outcome.schema.json')
    assert out['evaluator_version'] == '2.0.0'
    assert out['horizons']['1h']['status'] == 'direction_only'
    assert not out['horizons']['1h']['triggered']
    assert out['horizons']['1h']['r_multiple'] is None
    scores = scorecard([f], [out], '2026-09-21T08:00:00Z')
    validate(scores, 'oracle_scorecard.schema.json')
    assert scores['groups'][0]['direction'] == 'LONG'
    assert scores['groups'][0]['resolved_triggered_count'] == 0


def test_v4_writer_reads_committed_bindings_and_passes_archive_guard(tmp_path):
    import sys
    from pathlib import Path
    from ledger_store import initialize, append_events
    from oracle_submission import publish_submission
    from oracle_receipts import verify_receipt
    from test_oracle_submission import git, commit
    f, snapshot, state, cfg = fixture()
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    initialize(tmp_path / 'data', cfg, EPOCH)
    append_events(tmp_path / 'data', seed(cfg)[1])
    path = tmp_path / 'data/llm_snapshot.json'
    path.write_text(json.dumps(snapshot))
    before = commit(tmp_path)
    envelope = {'schema_version': 1, 'snapshot_commit': before, 'forecast': f}
    path.write_text('{}')  # Writer uses immutable snapshot_commit, not checkout.
    published = publish_submission(envelope, EPOCH, tmp_path, archive_submission=True)
    assert json.loads(published.read_text()) == f
    receipt = json.loads((tmp_path / 'data/oracle/receipts' / (f['forecast_id'] + '.json')).read_text())
    assert verify_receipt(receipt, tmp_path / 'data')
    path.write_text(json.dumps(snapshot))
    after = commit(tmp_path)
    sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
    from check_oracle_archive import check
    assert check(before, after, repo=tmp_path)
    # A validly sized extra plan still cannot authorize an unpublished order.
    from ledger_store import persist_plan
    extra = deepcopy(f)
    extra['created_at_utc'] = '2026-09-20T20:00:01Z'
    rebind(extra, snapshot)
    persist_plan(tmp_path / 'data', prepare_plan(extra, snapshot, state, cfg), state)
    orphan = commit(tmp_path)
    with pytest.raises(ValueError, match='no published v4 forecast'):
        check(after, orphan, repo=tmp_path)


def test_oracle_producer_may_only_add_plans_and_bound_states():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
    from promote_data import allowed
    assert allowed('oracle', 'data/ledger/plans/forecast-a.json')
    assert allowed('oracle', 'data/ledger/states/' + 'a' * 64 + '.json')
    for name in ('genesis.json', 'state.json', 'performance.json', 'events/2026/09/20.jsonl', 'trades/a.json'):
        assert not allowed('oracle', 'data/ledger/' + name)
    assert not allowed('collector', 'data/ledger/plans/a.json')
    assert not allowed('light', 'data/ledger/plans/a.json')


def test_writer_rechecks_quote_age_at_forecast_creation():
    f, snapshot, state, cfg = fixture()
    f['created_at_utc'] = '2026-09-20T20:16:00Z'
    rebind(f, snapshot)
    with pytest.raises(ValueError, match='configured age'):
        prepare_plan(f, snapshot, state, cfg)
