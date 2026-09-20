from copy import deepcopy
from datetime import timedelta
import json
import pytest

from intraday import collect
from ledger_activation import prepare, activate
from ledger_market import verify_market_input
from ledger_store import verify
from test_intraday import NOW, BOUNDARY, fixture_fetch
from test_ledger import config


def evidence(tmp_path):
    collect(tmp_path, 'light', BOUNDARY, fixture_fetch, lambda: NOW)
    path = tmp_path / 'funding/2026/09.jsonl'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'timestamp':'2026-09-20T20:00:00Z', 'fundingRate':'0.00006', 'relativeFundingRate':'0.00006'}) + '\n')
    return config(enabled=False)


def test_preview_is_read_only_and_write_replays_with_bound_sources(tmp_path):
    cfg = evidence(tmp_path)
    original = deepcopy(cfg)
    preview = prepare(tmp_path, cfg, NOW + timedelta(seconds=10))
    assert not (tmp_path / 'ledger').exists() and cfg == original
    result = activate(tmp_path, cfg, NOW + timedelta(seconds=20), preview['plan_sha256'])
    ledger = verify(tmp_path)
    assert result['equity_usd'] == '5000' and result['events'] == 2
    assert ledger['state']['position'] is None and ledger['state']['order'] is None
    assert ledger['genesis']['config']['enabled'] is True
    assert ledger['genesis']['epoch_utc'] == '2026-09-20T20:30:08Z'
    for record in ledger['records']:
        verify_market_input(tmp_path, ledger['genesis']['epoch_utc'], record['input'])
    before = {p: p.read_bytes() for p in (tmp_path / 'ledger').rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='never resets'):
        activate(tmp_path, cfg, NOW, preview['plan_sha256'])
    assert all(p.read_bytes() == raw for p, raw in before.items())


def test_changed_config_or_source_requires_new_preview_and_writes_nothing(tmp_path):
    cfg = evidence(tmp_path)
    preview = prepare(tmp_path, cfg, NOW)
    cfg['risk_fraction_per_trade'] = '0.005'
    with pytest.raises(ValueError, match='plan changed'):
        activate(tmp_path, cfg, NOW, preview['plan_sha256'])
    assert not (tmp_path / 'ledger').exists()
    cfg['risk_fraction_per_trade'] = '0.01'
    path = tmp_path / 'funding/2026/09.jsonl'
    rate = json.loads(path.read_text()); rate['fundingRate'] = '0.00007'
    path.write_text(json.dumps(rate) + '\n')
    with pytest.raises(ValueError, match='plan changed'):
        activate(tmp_path, cfg, NOW, preview['plan_sha256'])
    assert not (tmp_path / 'ledger').exists()


def test_missing_hour_and_stale_or_future_quote_reject_activation(tmp_path):
    cfg = evidence(tmp_path)
    with pytest.raises(ValueError, match='current quarter'):
        prepare(tmp_path, cfg, NOW + timedelta(minutes=15))
    cfg['execution_quote_max_age_seconds'] = 1
    with pytest.raises(ValueError, match='quote exceeds'):
        prepare(tmp_path, cfg, NOW + timedelta(seconds=2))
    with pytest.raises(ValueError, match='outside current'):
        prepare(tmp_path, cfg, NOW - timedelta(seconds=1))
    (tmp_path / 'funding/2026/09.jsonl').write_text('')
    with pytest.raises(ValueError, match='historical funding'):
        prepare(tmp_path, cfg, NOW)
    assert not (tmp_path / 'ledger').exists()


def test_invalid_seed_fails_before_any_ledger_publication(tmp_path):
    cfg = evidence(tmp_path)
    path = tmp_path / 'funding/2026/09.jsonl'
    rate = json.loads(path.read_text()); rate['relativeFundingRate'] = '-0.00006'
    path.write_text(json.dumps(rate) + '\n')
    with pytest.raises(ValueError, match='signs disagree'):
        prepare(tmp_path, cfg, NOW)
    assert not (tmp_path / 'ledger').exists()
    assert not list(tmp_path.glob('.paper-init-*'))


def test_staging_failure_never_publishes_partial_genesis(tmp_path, monkeypatch):
    import ledger_activation
    cfg = evidence(tmp_path)
    preview = prepare(tmp_path, cfg, NOW)
    def fail(*args):
        raise ValueError('simulated staging failure')
    monkeypatch.setattr(ledger_activation, 'append_events', fail)
    with pytest.raises(ValueError, match='staging failure'):
        activate(tmp_path, cfg, NOW, preview['plan_sha256'])
    assert not (tmp_path / 'ledger').exists()
    assert not list(tmp_path.glob('.paper-init-*'))


def test_initialized_account_advances_on_next_quarter_without_backdating(tmp_path):
    from ledger_runtime import advance
    from test_ledger_market import quarter
    cfg = evidence(tmp_path)
    preview = prepare(tmp_path, cfg, NOW)
    activate(tmp_path, cfg, NOW, preview['plan_sha256'])
    following = quarter(BOUNDARY, '2026-09-20T20:45:00Z', '2026-09-20T20:45:08Z', 3)
    with (tmp_path / 'intraday/2026/09/20.jsonl').open('a') as handle:
        handle.write(json.dumps(following) + '\n')
    advance(tmp_path, tmp_path, 'a' * 40, '2026-09-20T20:45:00Z')
    result = verify(tmp_path)
    candles = [r['input']['at_utc'] for r in result['records'] if r['input']['type'] == 'candle']
    assert candles[0] == '2026-09-20T20:31:00Z'
    assert candles[-1] == '2026-09-20T20:44:00Z'
    assert len(candles) == 14
    assert result['state']['equity_usd'] == '5000'
    assert result['state']['position'] is None
