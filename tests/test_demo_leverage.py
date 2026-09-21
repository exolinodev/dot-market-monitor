"""Leverage settings are read-only evidence, not an inferred margin formula."""
import json

import pytest
import requests

from demo_evidence import capture_bundle, verify_bundle, digest, inventory
from demo_journal import initialize, locked
from demo_leverage import preference
from demo_observation import import_observation, observation
from kraken_execution import DemoClient, ExecutionError, OutcomeUnknown, authent, _json_bytes
from test_demo_evidence import Client, KEY
from test_demo_preflight import setup, run, Funded, START as ENTRY_START, AT
from test_exchange_history import ACCOUNT, START, END
from test_kraken_execution import SECRET


class Configured(Client):
    def request(self, endpoint, params=None):
        if endpoint == 'leveragepreferences':
            return 200, ('{ "result":"success", "serverTime":"' + END + '", '
                         '"leveragePreferences":[{"symbol":"PF_DOTUSD","maxLeverage":3.125}] }\n ').encode()
        return super().request(endpoint, params)


def test_settings_raw_bytes_import_and_restart_remain_exact(tmp_path):
    root = tmp_path/'bundle'
    result = capture_bundle(root, Configured(), ACCOUNT, KEY, START)
    raw = (root/'readback/leveragepreferences.raw').read_bytes()
    assert raw.endswith(b'\n ')
    assert verify_bundle(root, ACCOUNT, KEY, result['bundle_sha256'])['version'] == 4
    journal = tmp_path/'journal'; initialize(journal, ACCOUNT, KEY)
    with locked(journal, ACCOUNT, KEY) as store:
        ref = import_observation(store, root, result['bundle_sha256'])
        value = store._read_artifact(ref)['leverage_preference']
        assert value == {'symbol': 'PF_DOTUSD', 'asof_utc': END, 'configured': True,
                         'max_leverage_present': True, 'max_leverage': '3.125'}
    with locked(journal, ACCOUNT, KEY) as store:
        assert store._read_artifact(ref)['leverage_preference'] == value
    (journal/'evidence'/result['bundle_sha256']/'readback/leveragepreferences.raw').write_bytes(raw+b' ')
    with pytest.raises(ExecutionError, match='files changed'):
        with locked(journal, ACCOUNT, KEY): pass


@pytest.mark.parametrize('rows,configured,present', [([], False, False),
    ([{'symbol':'PF_DOTUSD'}], True, False),
    ([{'symbol':'PF_DOTUSD', 'maxLeverage':None}], True, True),
    ([{'symbol':'PF_XBTUSD', 'maxLeverage':10}], False, False)])
def test_missing_null_and_other_instrument_settings_do_not_become_defaults(rows, configured, present):
    value = preference({'result':'success', 'serverTime':END, 'leveragePreferences':rows})
    assert value['configured'] is configured and value['max_leverage_present'] is present
    assert value['max_leverage'] is None and 'margin_mode' not in value


@pytest.mark.parametrize('rows', [[{'symbol':'PF_DOTUSD', 'maxLeverage':x}] for x in (True, 0, -1, 'NaN')] +
    [[{'symbol':'PF_DOTUSD'}, {'symbol':'pf_dotusd'}], [{'maxLeverage':10}], [None]])
def test_invalid_or_duplicate_preferences_fail(rows):
    with pytest.raises(ExecutionError):
        preference({'result':'success', 'serverTime':END, 'leveragePreferences':rows})


def test_settings_are_signed_get_only_on_fixed_demo_host(monkeypatch):
    client = DemoClient('synthetic-public', SECRET)
    seen = []
    def request(method, url, **options):
        seen.append((method, url, options)); raise requests.Timeout('secret-not-for-output')
    monkeypatch.setattr(client._session, 'request', request)
    with pytest.raises(OutcomeUnknown): client.request('leveragepreferences')
    method, url, options = seen[0]
    assert method == 'GET' and url == 'https://demo-futures.kraken.com/derivatives/api/v3/leveragepreferences'
    assert options['data'] is None and options['allow_redirects'] is False
    assert options['headers']['Authent'] == authent(SECRET, '', '/api/v3/leveragepreferences')


def test_preference_timestamp_participates_in_history_window(tmp_path):
    class Later(Client):
        def request(self, endpoint, params=None):
            status, raw = super().request(endpoint, params)
            if endpoint == 'leveragepreferences':
                value = json.loads(raw); value['serverTime'] = '2026-09-21T01:00:01Z'
                raw = json.dumps(value).encode()
            return status, raw
    root = tmp_path/'bundle'
    result = capture_bundle(root, Later(), ACCOUNT, KEY, START, END)
    with pytest.raises(ExecutionError, match='cover all readback'):
        observation(root, ACCOUNT, KEY, result['bundle_sha256'])


def test_legacy_preference_absence_blocks_current_entry_without_invalidating_replay(tmp_path):
    args = setup(tmp_path)
    root = tmp_path/'legacy'
    capture_bundle(root, Funded(AT), ACCOUNT, KEY, ENTRY_START)
    for ext in ('.raw', '.json'): (root/'readback'/('leveragepreferences'+ext)).unlink()
    meta = json.loads((root/'readback/manifest.json').read_bytes()); meta['version'] = 1
    del meta['responses']['leveragepreferences']
    (root/'readback/manifest.json').write_bytes(_json_bytes(meta))
    manifest = json.loads((root/'bundle.json').read_bytes())
    manifest.update(version=3, files=inventory(root))
    raw = _json_bytes(manifest); (root/'bundle.json').write_bytes(raw)
    with locked(args[-1], ACCOUNT, KEY) as store:
        import_observation(store, root, digest(raw)); store.reconcile_position()
    result = run(args)
    assert not result['entry_checks_passed'] and not result['leverage_preferences_verified']
    assert 'demo_leverage_preferences_missing' in result['reasons']
    assert not result['exchange_margin_requirement_verified']
