import copy
import hashlib
import json
import pandas as pd
import pytest
from common import write_json
from observation_history import METRICS, ObservationArchive, history_observations, observation_record

NOW = pd.Timestamp('2026-09-14T20:00:00Z')
DIGEST = hashlib.sha256(b'{"version":1}').hexdigest()


def record(hours=0, value=10):
    stamp = (NOW-pd.Timedelta(hours=hours)).isoformat()
    return {'schema_version': 1, 'observed_at_utc': stamp, 'config_sha256': DIGEST,
        'values': {k: {'value': value, 'unit': unit, 'source_timestamp_utc': stamp,
                      'source_ids': ['fixture'], 'method': k} for k, unit in METRICS.items()}}


def test_actual_history_changes_keep_units_timestamps_and_missing_references():
    old, current = record(1, 8), record(0, 10)
    result = history_observations([old], current)
    one = result['data']['changes']['1h']
    assert one['data']['open_interest_dot']['absolute_change'] == 2
    assert one['data']['open_interest_dot']['elapsed_minutes'] == 60
    assert one['data']['funding_rate_absolute_api']['unit'] == 'absolute_API_rate'
    assert result['data']['changes']['24h']['data']['open_interest_dot']['reference_value'] is None
    assert result['data']['funding_history']['historical_percentile'] is None
    assert 'spot_signed_volume_1h_dot' not in one['data']


def test_zero_and_missing_values_are_distinct_and_future_records_excluded():
    past, current = record(1, 0), record(0, 2)
    current['values']['spot_midprice_usd']['value'] = None
    result = history_observations([past, record(-1, 99)], current)
    one = result['data']['changes']['1h']['data']
    assert one['funding_rate_absolute_api']['absolute_change'] == 2
    assert one['spot_midprice_usd']['absolute_change'] is None
    assert result['coverage']['available_prior_records'] == 1


def test_reference_time_tolerance_and_quantity_method_must_match():
    current = record()
    assert history_observations([record(1.5)], current)['data']['changes']['1h']['data']['open_interest_dot']['absolute_change'] is None
    past = record(1)
    past['values']['open_interest_dot']['method'] = 'contracts_with_unknown_size'
    result = history_observations([past], current)['data']['changes']['1h']['data']
    assert result['open_interest_dot']['absolute_change'] is None
    assert result['perp_mark_usd']['absolute_change'] == 0
    past['values']['perp_mark_usd']['source_ids'] = ['different_exchange']
    assert history_observations([past], current)['data']['changes']['1h']['data']['perp_mark_usd']['absolute_change'] is None


def test_funding_percentile_excludes_current_and_requires_samples():
    past = [record(i, i) for i in range(1, 5)]
    result = history_observations(past, record(0, 2), minimum=4)['data']['funding_history']
    assert result['historical_percentile'] == 37.5
    assert result['sample_count'] == 4


def test_archive_retains_latest_real_hour_and_its_configuration(tmp_path):
    archive = ObservationArchive(tmp_path/'observations.json.gz')
    archive.content['records'] = [record(i) for i in range(80, 0, -1)]
    archive.content['configurations'] = {DIGEST: {'recorded_at_utc': record(80)['observed_at_utc'], 'configuration': {'version': 1}}}
    archive.update(record(.2, 11), {'version': 1}, days=1)
    archive.update(record(0, 12), {'version': 1}, days=1)
    assert len(archive.records) == 24
    assert archive.records[-1]['values']['spot_midprice_usd']['value'] == 12
    assert archive.content['configurations'][DIGEST]['configuration'] == {'version': 1}
    assert ObservationArchive(archive.path).records == archive.records


def test_corrupt_archive_is_rejected_without_overwriting_it(tmp_path):
    path = tmp_path/'bad.json.gz'
    write_json(path, {'schema_version': 7, 'records': []}, compressed=True)
    before = path.read_bytes()
    with pytest.raises(ValueError): ObservationArchive(path)
    assert path.read_bytes() == before


def test_archive_rejects_config_tampering_and_out_of_order_writes(tmp_path):
    archive = ObservationArchive(tmp_path/'observations.json.gz')
    archive.update(record(), {'version': 1})
    before = archive.path.read_bytes()
    with pytest.raises(ValueError, match='later archived'):
        archive.update(record(1), {'version': 1})
    assert archive.path.read_bytes() == before
    content = copy.deepcopy(archive.content)
    content['configurations'][DIGEST]['configuration']['version'] = 2
    write_json(archive.path, content, compressed=True)
    with pytest.raises(ValueError, match='hash mismatch'): ObservationArchive(archive.path)


@pytest.mark.parametrize('hours', [[.2], list(range(24, 0, -1))])
def test_archive_replay_equal_after_same_hour_replacement_or_retention_trim(tmp_path, hours):
    archive = ObservationArchive(tmp_path/'observations.json.gz')
    archive.content['records'] = [record(h, 8) for h in hours]
    archive.content['configurations'] = {DIGEST: {'recorded_at_utc': record(24)['observed_at_utc'], 'configuration': {'version': 1}}}
    current = record(0, 10)
    before = history_observations(archive.records, current, days=1, minimum=1)
    archive.update(current, {'version': 1}, days=1)
    after = history_observations(archive.records, current, days=1, minimum=1)
    assert before == after


def test_archived_current_measurements_require_freshness_and_valid_oi_units():
    stamp = NOW.isoformat()
    data = {'generated_at_utc': stamp, 'markets': {'DOTUSD': {
        'spot': {'quote_midprice': 10, 'quote_timestamp_utc': stamp},
        'perp': {'ticker': {'mark_price': 11, 'open_interest': 123, 'funding_rate': .001, 'server_time_utc': stamp},
                 'instrument': None}}}}
    first = observation_record(data, {}, DIGEST)
    assert first['values']['open_interest_dot']['value'] is None
    assert first['values']['quote_mark_basis_bps']['value'] == pytest.approx(1000)
    data['markets']['DOTUSD']['perp']['instrument'] = {'base': 'DOT', 'quote': 'USD', 'contractSize': 1}
    assert observation_record(data, {}, DIGEST)['values']['open_interest_dot']['value'] == 123
    data['markets']['DOTUSD']['spot']['quote_timestamp_utc'] = (NOW-pd.Timedelta(minutes=5)).isoformat()
    stale = observation_record(data, {}, DIGEST)
    assert stale['values']['spot_midprice_usd']['value'] is None
    assert stale['values']['quote_mark_basis_bps']['value'] is None
