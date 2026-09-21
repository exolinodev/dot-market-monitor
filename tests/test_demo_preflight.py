"""Real Git publication + acquired journal + entry preflight, never sends."""
import json
from pathlib import Path
import pytest

from demo_evidence import capture_bundle
from demo_journal import initialize, locked
from demo_observation import import_observation
from demo_preflight import entry_preflight
from kraken_execution import ExecutionError
from order_executor import materialize
from test_demo_position import Client
from test_demo_evidence import KEY
from test_exchange_history import ACCOUNT
from test_order_executor import published, git

START = '2026-09-20T20:00:00Z'
AT = '2026-09-20T20:05:00Z'


class Funded(Client):
    def __init__(self, at, capital='5000', available='5000'):
        super().__init__(at)
        self.capital, self.available = capital, available
    def request(self, endpoint, params=None):
        status, raw = super().request(endpoint, params)
        data = json.loads(raw)
        if endpoint == 'accounts':
            data['accounts'] = {'flex': {'type': 'multiCollateralMarginAccount', 'portfolioValue': self.capital,
                'unrealizedFunding': '0', 'marginEquity': self.capital, 'availableMargin': self.available}}
        return status, json.dumps(data).encode()


def observe(store, path, at=AT, capital='5000'):
    result = capture_bundle(path, Funded(at, capital), ACCOUNT, KEY, START)
    return import_observation(store, path, result['bundle_sha256'])


def setup(tmp_path, capital='5000', at=AT):
    repo = tmp_path/'repo'; head, forecast = published(repo)
    (repo/'config').mkdir()
    settings = json.loads((Path(__file__).resolve().parents[1]/'config/demo_executor.json').read_text())
    (repo/'config/demo_executor.json').write_text(json.dumps(settings))
    git(repo, 'add', 'config'); git(repo, 'commit', '-m', 'demo policy', date='2026-09-20T20:04:35Z')
    head = git(repo, 'rev-parse', 'HEAD'); git(repo, 'update-ref', 'refs/remotes/origin/main', head)
    data = materialize(repo, head, tmp_path/'view')
    journal = tmp_path/'journal'; initialize(journal, ACCOUNT, KEY)
    with locked(journal, ACCOUNT, KEY) as store:
        observe(store, tmp_path/'baseline', '2026-09-20T20:04:45Z')
        store.establish_flat_baseline()
        observe(store, tmp_path/'current', at, capital)
        store.reconcile_position()
    return repo, head, forecast, data, journal


def run(args, reference=AT):
    repo, head, forecast, data, journal = args
    with locked(journal, ACCOUNT, KEY) as store:
        return entry_preflight(data, repo, head, forecast, store, reference)


def test_current_flat_account_and_published_entry_pass_without_authorizing(tmp_path):
    args = setup(tmp_path)
    result = run(args)
    assert result['entry_checks_passed'] and not result['reasons']
    assert not result['demo_enabled'] and not result['authorizes_execution']
    assert not result['account_flows_verified'] and result['live_quote_verified']
    assert result['entry_request']['size'] == result['quantity_ceiling']
    # A local policy edit must not relax the committed limits.
    (args[0]/'config/demo_executor.json').write_text('{}')
    assert run(args) == result


def test_stale_and_future_observations_are_refused(tmp_path):
    args = setup(tmp_path)
    assert 'readback_stale' in run(args, '2026-09-20T20:05:31Z')['reasons']
    assert 'readback_from_future' in run(args, '2026-09-20T20:04:59Z')['reasons']
    assert 'entry_not_yet_effective' in run(args, '2026-09-20T20:04:59Z')['reasons']


def test_expired_order_and_stale_quote_are_visible_even_with_fresh_account(tmp_path):
    args = setup(tmp_path, at='2026-09-20T23:00:00Z')
    reasons = run(args, '2026-09-20T23:00:00Z')['reasons']
    assert 'entry_expired' in reasons and 'published_quote_stale_or_future' in reasons
    assert 'readback_stale' not in reasons


def test_lower_equity_rejects_original_quantity_without_resizing(tmp_path):
    args = setup(tmp_path, capital='4500')
    result = run(args)
    assert 'published_quantity_exceeds_current_risk_budget' in result['reasons']
    assert int(result['entry_request']['size']) > int(result['quantity_ceiling'])
    assert result['budget_equity_usd'] == '4500'


def test_actual_demo_drawdown_trips_equity_floor(tmp_path):
    result = run(setup(tmp_path, capital='3900'))
    assert 'demo_equity_floor_breached' in result['reasons']
    assert not result['entry_checks_passed']


def test_new_observation_invalidates_preflight_until_reconciled(tmp_path):
    args = setup(tmp_path)
    repo, head, forecast, data, journal = args
    with locked(journal, ACCOUNT, KEY) as store:
        observe(store, tmp_path/'new')
        with pytest.raises(ExecutionError, match='Current durable'):
            entry_preflight(data, repo, head, forecast, store, AT)


def test_old_fetched_head_cannot_omit_new_main_information(tmp_path):
    args = setup(tmp_path)
    repo = args[0]
    git(repo, 'commit', '--allow-empty', '-m', 'new main', date='2026-09-20T20:04:40Z')
    git(repo, 'update-ref', 'refs/remotes/origin/main', git(repo, 'rev-parse', 'HEAD'))
    with pytest.raises(ExecutionError, match='current fetched main'):
        run(args)


def test_wide_readback_span_fails_even_when_start_is_recent(tmp_path):
    args = setup(tmp_path)
    class Slow(Funded):
        def request(self, endpoint, params=None):
            status, raw = super().request(endpoint, params)
            value = json.loads(raw)
            if endpoint == 'accounts': value['serverTime'] = '2026-09-20T20:05:20Z'
            return status, json.dumps(value).encode()
    with locked(args[4], ACCOUNT, KEY) as store:
        result = capture_bundle(tmp_path/'slow', Slow(AT), ACCOUNT, KEY, START)
        import_observation(store, tmp_path/'slow', result['bundle_sha256'])
        store.reconcile_position()
    result = run(args, '2026-09-20T20:05:20Z')
    assert 'readback_span_exceeded' in result['reasons'] and 'readback_stale' not in result['reasons']


def test_executor_cli_uses_journal_preflight_without_modifying_it(tmp_path, monkeypatch, capsys):
    import importlib.util
    from cycles import utc
    args = setup(tmp_path)
    repo, head, forecast, _, journal = args
    path = Path(__file__).resolve().parents[1]/'scripts/execute_orders.py'
    spec = importlib.util.spec_from_file_location('executor_preflight_cli_test', path)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    class Clock:
        @staticmethod
        def now(zone): return utc(AT)
    monkeypatch.setattr(cli, 'datetime', Clock)
    with locked(journal, ACCOUNT, KEY) as store: before = store.state
    capsys.readouterr()
    assert cli.main(['--mode', 'demo', '--preview', '--repo', str(repo), '--trusted-head', head,
                     '--forecast-id', forecast, '--journal-dir', str(journal),
                     '--account-uid', ACCOUNT, '--key-fingerprint', KEY]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['entry_checks_passed'] and not result['authorizes_execution']
    with locked(journal, ACCOUNT, KEY) as store: assert store.state == before


@pytest.mark.parametrize('change,reason', [('spread', 'published_quantity_exceeds_current_risk_budget'),
    ('suspended', 'demo_market_not_tradable'), ('tick', 'demo_instrument_config_mismatch'),
    ('stale', 'demo_market_stale_or_future')])
def test_current_market_controls_entry_checks(tmp_path, change, reason):
    args = setup(tmp_path)
    class Market(Funded):
        def market(self, endpoint):
            status, raw, headers = super().market(endpoint)
            value = json.loads(raw)
            if change == 'spread' and endpoint == 'tickers':
                value['tickers'][0].update(bid='.998', ask='1.002')
            if change == 'suspended' and endpoint == 'tickers': value['tickers'][0]['suspended'] = True
            if change == 'tick' and endpoint == 'instruments': value['instruments'][0]['tickSize'] = '.01'
            if change == 'stale': value['serverTime'] = START
            return status, json.dumps(value).encode(), headers
    with locked(args[4], ACCOUNT, KEY) as store:
        result = capture_bundle(tmp_path/'market-changed', Market(AT), ACCOUNT, KEY, START)
        import_observation(store, tmp_path/'market-changed', result['bundle_sha256'])
        store.reconcile_position()
    result = run(args)
    assert reason in result['reasons'] and not result['entry_checks_passed']
