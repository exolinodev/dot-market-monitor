import copy
import json
import os
from pathlib import Path
import time
from datetime import datetime
import numpy as np
import pandas as pd
import pytest
from common import write_json
from observation_history import ObservationArchive
from observations import build_observations, configuration, replay_inputs, COMPONENTS
from output import compact_snapshot, validate_snapshot
from pipeline import Collector
from test_main import Offline
from test_observation_candles import candles
from test_observation_flow import tape

ROOT = Path(__file__).parents[1]
REFERENCE = '2026-09-14T20:00:00Z'


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    monkeypatch.setattr('pipeline.utcnow', lambda: datetime.fromisoformat(REFERENCE))
    data = Collector(tmp_path, Offline(), Offline(), Offline()).collect()
    index = pd.date_range(end=pd.Timestamp(REFERENCE)-pd.Timedelta(hours=1), periods=1000, freq='h')
    values = 10*np.exp(np.sin(np.arange(1000)/3)*.01)
    hourly = candles(values, start=index[0])
    four_hour = candles(values[-250:], start=pd.Timestamp(REFERENCE)-pd.Timedelta(hours=1000), minutes=240)
    frames = {('DOTUSD', 60): hourly, ('BTCUSD', 60): hourly.copy(), ('DOTUSD', 240): four_hour}
    return data, frames


def test_all_components_contract_schema_and_existing_snapshot_fields(inputs):
    data, frames = inputs
    before = copy.deepcopy(data)
    observations, _, _ = build_observations(data, frames)
    assert data == before
    assert set(observations['components']) == set(COMPONENTS)
    assert observations['components']['price_levels']['status'] == 'ok'
    assert observations['components']['anchored_vwap']['status'] == 'ok'
    assert observations['components']['historical_context']['status'] == 'ok'
    assert observations['components']['market_relative']['status'] == 'ok'
    assert observations['components']['scheduled_events']['status'] == 'ok'
    assert observations['components']['flow_windows']['status'] == 'partial'
    assert observations['components']['input_lineage']['data']['selection_record_available_asof_snapshot']
    data['markets']['DOTUSD']['observations'] = observations
    assert validate_snapshot(compact_snapshot(data))
    del data['markets']['DOTUSD']['observations']
    assert validate_snapshot(compact_snapshot(data))


def test_interpretations_are_absent_and_rejected_by_payload_schema(inputs):
    data, frames = inputs
    observations = build_observations(data, frames)[0]
    forbidden = {'bullish', 'bearish', 'probability', 'confidence', 'forecast', 'signal', 'regime', 'wave_count', 'price_target'}
    def check(value):
        if isinstance(value, dict):
            assert not forbidden.intersection(value)
            for child in value.values(): check(child)
        elif isinstance(value, list):
            for child in value: check(child)
    check(observations)
    observations['components']['price_levels']['data']['1h']['data'][0]['bullish_score'] = 80
    data['markets']['DOTUSD']['observations'] = observations
    with pytest.raises(Exception): validate_snapshot(compact_snapshot(data))


def test_one_invalid_measurement_source_does_not_discard_other_components(inputs):
    data, frames = inputs
    broken = {**frames, ('DOTUSD', 60): frames[('DOTUSD', 60)].drop(columns=['vwap'])}
    observations = build_observations(data, broken)[0]
    assert observations['components']['anchored_vwap']['status'] == 'error'
    assert observations['components']['price_levels']['status'] == 'ok'
    assert observations['components']['market_relative']['status'] == 'ok'
    data['markets']['DOTUSD']['observations'] = observations
    assert validate_snapshot(compact_snapshot(data))


def test_invalid_configuration_yields_explicit_error_without_fallback(inputs, tmp_path):
    data, frames = inputs
    missing = build_observations(data, frames, config_dir=tmp_path/'missing')[0]
    assert missing['status'] == 'error'
    assert all(value['data'] is None for value in missing['components'].values())
    for filename in ('observations.json', 'scheduled_events.json', 'time_fibs.json'):
        (tmp_path/filename).write_bytes((ROOT/'config'/filename).read_bytes())
    config = json.loads((tmp_path/'observations.json').read_text())
    config['bullish_score_threshold'] = 5
    write_json(tmp_path/'observations.json', config)
    bad = build_observations(data, frames, config_dir=tmp_path)[0]
    assert bad['status'] == 'error'
    data['markets']['DOTUSD']['observations'] = bad
    assert validate_snapshot(compact_snapshot(data))


def test_utc_determinism_and_config_fingerprint(inputs):
    data, frames = inputs
    expected = build_observations(data, frames)[0]
    previous = os.environ.get('TZ')
    try:
        for zone in ('UTC', 'Europe/Madrid', 'Pacific/Auckland', 'America/Los_Angeles'):
            os.environ['TZ'] = zone
            time.tzset()
            assert build_observations(data, frames)[0] == expected
    finally:
        if previous is None: os.environ.pop('TZ', None)
        else: os.environ['TZ'] = previous
        time.tzset()
    assert len(expected['config_sha256']) == 64
    assert expected['config_sha256'] == configuration()[1]


def test_replay_cannot_promote_a_candle_open_at_source_receipt(inputs, tmp_path):
    data, frames = inputs
    from timeframes import encode_candles
    key = 'DOTUSD.ohlc.60'
    data['generated_at_utc'] = '2026-09-14T20:00:01Z'
    data['sources'][key].update(fresh=True, received_at_utc='2026-09-14T19:59:59Z')
    write_json(tmp_path/'raw/latest.json.gz', {'generated_at_utc': data['generated_at_utc'], 'http_requests': {}, 'responses': {}}, compressed=True)
    write_json(tmp_path/'raw/ohlc_cache.json.gz', {key: encode_candles(frames[('DOTUSD', 60)])}, compressed=True)
    restored, *_ = replay_inputs(data, tmp_path)
    assert restored[('DOTUSD', 60)].index[-1] == pd.Timestamp('2026-09-14T18:00:00Z')
    assert build_observations(data, restored)[0]['components']['historical_context']['status'] == 'unavailable'


def test_corrupt_archive_is_isolated_during_normal_collection(tmp_path, monkeypatch):
    monkeypatch.setattr('pipeline.utcnow', lambda: datetime.fromisoformat(REFERENCE))
    path = tmp_path/'raw/observation_history.json.gz'
    write_json(path, {'schema_version': 999, 'records': []}, compressed=True)
    before = path.read_bytes()
    data = Collector(tmp_path, Offline(), Offline(), Offline()).collect()
    assert data['markets']['DOTUSD']['observations']['components']['spot_perp_history']['status'] == 'error'
    assert path.read_bytes() == before
    assert validate_snapshot(compact_snapshot(data))


@pytest.mark.parametrize('target', ['reference', 'asof', 'source'])
def test_observation_reference_and_provenance_checks(inputs, target):
    data, frames = inputs
    observation = build_observations(data, frames)[0]
    if target == 'reference': observation['reference_at_utc'] = '2026-09-14T19:00:00Z'
    if target == 'asof': observation['components']['historical_context']['asof_utc'] = '2026-09-15T00:00:00Z'
    if target == 'source': observation['components']['historical_context']['source_ids'] = ['invented_source']
    data['markets']['DOTUSD']['observations'] = observation
    with pytest.raises(ValueError): validate_snapshot(compact_snapshot(data))
