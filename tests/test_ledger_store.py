from copy import deepcopy
import json
from pathlib import Path
import pytest
from ledger import digest, plan_instruction, state_hash
from ledger_store import initialize, persist_plan, append_events, verify, rebuild_views
from test_ledger import config, seed, prepare, candle, EPOCH


def setup_account(tmp_path):
    cfg, plan, events = prepare()
    initial = initialize(tmp_path, cfg, EPOCH)
    state = append_events(tmp_path, events[:2])
    persist_plan(tmp_path, plan, state)
    return cfg, plan, events, initial


def test_disk_replay_matches_every_view_and_is_idempotent(tmp_path):
    cfg, plan, events, _ = setup_account(tmp_path)
    state = append_events(tmp_path, events[2:] + [candle(1), candle(2, high='1.061')])
    result = verify(tmp_path)
    assert state == result['state'] and len(result['trades']) == 1
    before = {p: p.read_bytes() for p in (tmp_path / 'ledger').rglob('*') if p.is_file()}
    assert append_events(tmp_path, events[2:]) == state
    assert all(p.read_bytes() == raw for p, raw in before.items())
    assert len(json.dumps(state)) < 6000  # Rolling state excludes growing event/trade arrays.


def test_state_corruption_is_detected_and_rebuild_uses_journal(tmp_path):
    _, _, events, _ = setup_account(tmp_path)
    expected = append_events(tmp_path, events[2:] + [candle(1)])
    path = tmp_path / 'ledger/state.json'
    wrong = deepcopy(expected); wrong['cash_usd'] = '9000'; path.write_text(json.dumps(wrong))
    with pytest.raises(ValueError, match='state differs'): verify(tmp_path)
    assert rebuild_views(tmp_path) == expected
    verify(tmp_path)


def test_effect_tampering_or_forged_size_is_rejected(tmp_path):
    _, plan, events, _ = setup_account(tmp_path)
    state = verify(tmp_path)['state']
    wrong = deepcopy(plan); wrong['orders'][0]['size']['quantity'] = '999999'
    with pytest.raises(ValueError, match='size'): persist_plan(tmp_path, wrong, state)
    append_events(tmp_path, events[2:] + [candle(1)])
    journal = tmp_path / 'ledger/events/2026/09/20.jsonl'
    records = [json.loads(line) for line in journal.read_text().splitlines()]
    records[-1]['effects'][-1]['equity_usd'] = '9000'
    journal.write_text(''.join(json.dumps(r) + '\n' for r in records))
    with pytest.raises(ValueError, match='differ from replay'): verify(tmp_path)


def test_plan_cannot_bind_a_fabricated_account_balance(tmp_path):
    cfg, _, events, _ = setup_account(tmp_path)
    state = verify(tmp_path)['state']; fake = deepcopy(state)
    fake['cash_usd'] = fake['equity_usd'] = '50000'; fake['state_sha256'] = state_hash(fake)
    from test_ledger import order
    plan = plan_instruction('forecast-fake', EPOCH, [order(forecast='forecast-fake')], [], fake, cfg, {'bid': '.9998', 'ask': '1.0002'})
    with pytest.raises(ValueError, match='unreachable'): persist_plan(tmp_path, plan, fake)


def test_pending_plan_tampering_is_checked_before_execution(tmp_path):
    _, _, _, _ = setup_account(tmp_path)
    path = tmp_path / 'ledger/plans/forecast-a.json'
    plan = json.loads(path.read_text())
    plan['orders'][0]['size']['quantity'] = '999999'
    path.write_text(json.dumps(plan))
    with pytest.raises(ValueError, match='verified plan'):
        verify(tmp_path)


def test_replay_cli_verifies_and_detects_corruption(tmp_path):
    import subprocess
    import sys
    _, _, events, _ = setup_account(tmp_path)
    append_events(tmp_path, events[2:] + [candle(1)])
    command = [sys.executable, str(Path(__file__).parents[1] / 'scripts/ledger_replay.py'), '--data-dir', str(tmp_path)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['replay'] == 'identical'
    (tmp_path / 'ledger/state.json').write_text('{}')
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode != 0 and 'state differs from replay' in result.stderr
