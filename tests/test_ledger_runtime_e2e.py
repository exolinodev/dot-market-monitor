"""Synthetic economic scenarios through the real writer, Git merge and runtime.

These prove integration and accounting, not profitability or exchange behavior.
"""
from copy import deepcopy
from decimal import Decimal as D
import json
from pathlib import Path
import sys

import pytest

from ledger import digest
from ledger_market import market_events
from ledger_runtime import advance, execution_context
from ledger_store import initialize, append_events, verify
from oracle_submission import publish_submission
from test_ledger import EPOCH
from test_ledger_market import archive
from test_ledger_publication import git
from test_oracle_v4 import fixture, rebind

sys.path.insert(0, str(Path(__file__).parents[1] / 'scripts'))
from validate_ledger import check


@pytest.mark.parametrize('kind', ['LIMIT', 'MARKET'])
def test_published_forecast_fills_exits_and_reports_replayable_net_result(tmp_path, kind):
    data = tmp_path / 'data'
    _, rows = archive(data)
    rows[0]['sources']['perp_book']['received_at_utc'] = EPOCH
    rows[0]['perp_book']['spread_bps'] = 2
    # Before publication there are executable prices too: the account must wait.
    # An intrabar limit entry must defer targets until the next bar.
    if kind == 'LIMIT':
        rows[1]['candles']['trade'][5][2] = 1.061
    for series in rows[1]['candles'].values():
        if kind == 'LIMIT':
            series[6][1:5] = [1, 1.061, .9998, 1.05]
        else:
            # Both stop and targets touched: mark stop wins at the adverse gap.
            series[6][1:5] = [.98, 1.061, .97, .98]
    archive_path = data / 'intraday/2026/09/20.jsonl'
    archive_path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    forecast, snapshot, _, cfg = fixture()
    initialize(data, cfg, EPOCH)
    seed = [e for e in market_events(data, EPOCH, '2026-09-20T20:15:00Z')
            if e['at_utc'] == EPOCH and e['type'] != 'candle']
    state = append_events(data, seed)
    snapshot['markets']['DOTUSD']['execution_context']['ledger_state_sha256'] = digest(state)
    forecast['ledger_state_sha256'] = digest(state)
    forecast['orders'][0]['entry']['type'] = kind
    rebind(forecast, snapshot)
    (data / 'llm_snapshot.json').write_text(json.dumps(snapshot))
    git(tmp_path, 'init', '-q', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Test')
    git(tmp_path, 'config', 'user.email', 'test@example.invalid')
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'synthetic bound snapshot', date=EPOCH)
    base = git(tmp_path, 'rev-parse', 'HEAD')
    git(tmp_path, 'update-ref', 'refs/remotes/origin/main', base)
    git(tmp_path, 'switch', '-c', 'writer')
    publish_submission({'schema_version': 1, 'snapshot_commit': base, 'forecast': forecast},
                       EPOCH, tmp_path, archive_submission=True)
    git(tmp_path, 'add', '.')
    git(tmp_path, 'commit', '-m', 'validated forecast', date='2026-09-20T20:00:20Z')
    git(tmp_path, 'switch', 'main')
    git(tmp_path, 'merge', '--no-ff', 'writer', '-m', 'publish', date='2026-09-20T20:04:30Z')
    head = git(tmp_path, 'rev-parse', 'HEAD')
    summary = advance(data, tmp_path, head, '2026-09-20T20:15:00Z')
    result = verify(data)
    effects = [e for r in result['records'] for e in r['effects']]
    entries = [e for e in effects if e['type'] == 'ENTRY_FILL']
    exits = [e for e in effects if e['type'] == 'EXIT_FILL']
    assert len(entries) == 1 and entries[0]['at_utc'] == '2026-09-20T20:05:00Z'
    assert all(e['at_utc'] == '2026-09-20T20:06:00Z' for e in exits)
    instruction = next(e for e in result['inputs'] if e['type'] == 'instruction')
    assert instruction['publication']['commit'] == head
    assert instruction['publication']['at_utc'] == '2026-09-20T20:04:30Z'
    assert len(result['trades']) == 1
    trade = next(iter(result['trades'].values()))
    qty = D(instruction['plan']['orders'][0]['size']['quantity'])
    assert D(entries[0]['quantity']) == qty
    assert sum(D(e['quantity']) for e in exits) == qty
    if kind == 'LIMIT':
        assert [e['reason'] for e in exits] == ['T1', 'T2', 'T3']
        q1, q2 = (qty * D('.4')) // 1, (qty * D('.3')) // 1
        proceeds = q1 * D('1.025') + q2 * D('1.04') + (qty-q1-q2) * D('1.06')
        gross, spread, fees = proceeds - qty, D(0), (qty + proceeds) * D('.0002')
    else:
        assert [e['reason'] for e in exits] == ['STOP_LOSS']
        assert D(entries[0]['fill_price_usd']) == D('1.0001')
        assert D(exits[0]['fill_price_usd']) == D('.979902')
        gross = qty * D('-.02')
        spread = qty * D('.000198')
        fees = qty * (D('1.0001') + D('.979902')) * D('.0005')
    funding = -qty * D('.000001')  # Published .00006 USD/DOT/hour, one retained minute.
    net = gross - spread - fees + funding
    assert D(trade['gross_pnl_usd']) == gross
    assert D(trade['spread_cost_usd']) == spread
    assert D(trade['fees_usd']) == fees
    assert D(trade['funding_usd']) == funding
    assert D(trade['net_pnl_usd']) == net
    assert D(summary['equity_usd']) == D(5000) + net
    assert summary['position'] is None and summary['open_order'] is None
    assert D(result['performance']['net_pnl_usd']) == net
    current_quote = deepcopy(rows[1])
    current_quote['perp_book'].update(bid=1, ask=1.001)
    context = execution_context(data, current_quote, '2026-09-20T20:15:00Z', '2026-09-20T20:15:10Z')
    assert context['recent_closed_trades'][0]['net_pnl_usd'] == trade['net_pnl_usd']
    # The same evidence survives both rerun and the real protected Git check.
    before = {p: p.read_bytes() for p in (data / 'ledger').rglob('*') if p.is_file()}
    assert advance(data, tmp_path, head, '2026-09-20T20:15:00Z') == summary
    assert before == {p: p.read_bytes() for p in (data / 'ledger').rglob('*') if p.is_file()}
    git(tmp_path, 'add', '.')
    check(staged=True, root=tmp_path)
    # An internally replayable journal is insufficient if external prices change.
    rows[1]['candles']['trade'][5][3] = .9997
    archive_path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    git(tmp_path, 'add', '.')
    with pytest.raises(ValueError, match='differs from archived'):
        check(staged=True, root=tmp_path)
