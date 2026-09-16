"""Consumer files must remain bounded and bind exactly to unmodified measurements."""
import copy
import gzip
import json
from pathlib import Path
import pytest
from oracle_common import canonical, digest
from oracle_consumer import PART_BYTES, OBSERVATION_COMPONENTS, build_consumer_bundle, write_consumer_bundle


@pytest.fixture
def snapshot():
    path = Path(__file__).parents[1] / 'docs/evaluation/oracle-v3/example_llm_snapshot.json.gz'
    return json.loads(gzip.decompress(path.read_bytes()))


def resolve(document, path):
    for token in path[1:].split('/'):
        key = token.replace('~1', '/').replace('~0', '~')
        document = document[int(key)] if isinstance(document, list) else document[key]
    return document


def test_projection_preserves_every_value_and_snapshot_binding(snapshot):
    before = copy.deepcopy(snapshot)
    manifest, parts = build_consumer_bundle(snapshot)
    assert snapshot == before
    assert build_consumer_bundle(snapshot) == (manifest, parts)
    # Dictionary insertion order must not alter filenames or projected hashes.
    reordered = json.loads(json.dumps(snapshot, sort_keys=True))
    assert build_consumer_bundle(reordered) == (manifest, parts)
    assert manifest['snapshot_sha256'] == digest(snapshot)
    paths = []
    for entry in manifest['parts']:
        part = parts[Path(entry['path']).name]
        assert entry['sha256'] == digest(part)
        assert entry['bytes'] == len((canonical(part) + '\n').encode()) <= PART_BYTES
        assert part['snapshot_sha256'] == digest(snapshot)
        for record in part['records']:
            assert resolve(snapshot, record['path']) == record['value']
            paths.append(record['path'])
    assert len(paths) == len(set(paths))
    features = snapshot['markets']['DOTUSD']['oracle_context']['current_features']['features']
    assert all('/markets/DOTUSD/oracle_context/current_features/features/' + k in paths for k in features)
    assert all('/markets/DOTUSD/timeframes/' + tf + '/last_closed/close' in paths
               for tf in snapshot['markets']['DOTUSD']['timeframes'])


def test_no_oracle_is_an_explicit_v2_projection(snapshot, tmp_path):
    del snapshot['markets']['DOTUSD']['oracle_context']
    manifest = write_consumer_bundle(snapshot, tmp_path)
    assert not any(p['section'] in ('features', 'oracle') for p in manifest['parts'])
    assert json.loads((tmp_path / 'oracle/consumer/index.json').read_text()) == manifest


def test_changed_input_changes_binding_and_removes_only_rolling_parts(snapshot, tmp_path):
    first = write_consumer_bundle(snapshot, tmp_path)
    immutable = tmp_path / 'oracle/forecasts/example.json'
    immutable.parent.mkdir(parents=True)
    immutable.write_text('original')
    snapshot['markets']['DOTUSD']['spot']['current_price'] = None
    snapshot['markets']['DOTUSD']['timeframes'] = {}
    second = write_consumer_bundle(snapshot, tmp_path)
    assert first['snapshot_sha256'] != second['snapshot_sha256']
    root = tmp_path / 'oracle/consumer'
    assert {p.name for p in root.glob('*.json')} == {'index.json'} | {
        Path(p['path']).name for p in second['parts']}
    assert immutable.read_text() == 'original'


def test_large_records_split_without_losing_json_pointer_semantics(snapshot):
    snapshot['markets']['DOTUSD']['oracle_context']['recent_forecasts'] = [
        {'a/b~c': 'x' * 6000} for _ in range(3)]
    _, parts = build_consumer_bundle(snapshot)
    records = [r for p in parts.values() for r in p['records']]
    selected = [r for r in records if '/recent_forecasts/' in r['path']]
    assert len(selected) == 3
    assert all(resolve(snapshot, r['path']) == r['value'] for r in selected)


def test_all_required_observations_are_fully_exported(snapshot):
    manifest, parts = build_consumer_bundle(snapshot)
    assert manifest['projection_version'] == 'oracle-consumer-v2'
    assert set(manifest['observation_components']) == set(OBSERVATION_COMPONENTS)
    records = [r for p in parts.values() for r in p['records']]
    for key in OBSERVATION_COMPONENTS:
        path = '/markets/DOTUSD/observations/components/' + key
        assert {'path': path, 'value': resolve(snapshot, path)} in records


def test_split_observation_arrays_retain_every_leaf_and_escaped_pointer(snapshot):
    original = [{'a/b~c': 'x' * 6000, 'value': i} for i in range(4)]
    snapshot['markets']['DOTUSD']['observations']['components']['volume_profile'] = original
    _, parts = build_consumer_bundle(snapshot)
    prefix = '/markets/DOTUSD/observations/components/volume_profile/'
    records = [r for p in parts.values() for r in p['records'] if r['path'].startswith(prefix)]
    assert len(records) == 4
    assert [r['value'] for r in records] == original
    for p in parts.values():
        assert len((canonical(p)+'\n').encode()) <= PART_BYTES
        assert all(resolve(snapshot,r['path']) == r['value'] for r in p['records'])
