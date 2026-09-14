import json
from types import SimpleNamespace
import numpy as np
import pytest
from common import json_safe
from output import compact_snapshot, validate_snapshot, markdown_summary
from pipeline import Collector


class Offline:
    base_url='https://api.coingecko.com/api/v3'
    http=SimpleNamespace(records={},raw={})
    def __getattr__(self,name):
        def fail(*args,**kwargs): raise RuntimeError('offline fixture')
        return fail


@pytest.fixture(autouse=True)
def offline_extra_venue(monkeypatch):
    monkeypatch.setattr('pipeline.CoinbaseClient',Offline)


def test_all_source_failures_publish_explicit_nulls(tmp_path):
    # New v2 contract: even a total outage produces a fresh error envelope.
    data=Collector(tmp_path,Offline(),Offline()).collect()
    compact=compact_snapshot(data)
    validate_snapshot(compact)
    assert data['status']=='partial'
    assert data['markets']['DOTUSD']['spot']['verified_price'] is None
    assert data['markets']['DOTUSD']['timeframes']['1m']['live'] is None
    assert len(data['errors'])>=20
    assert all(not s['fresh'] for s in data['sources'].values())
    assert 'offline fixture' in markdown_summary(data)
    assert 'n/a' in markdown_summary(data)


def test_json_safe_removes_numpy_nonfinite_values():
    result=json_safe({'values':[np.float32('nan'),np.float64('inf'),np.int64(3)]})
    assert json.dumps(result,allow_nan=False)=='{"values": [null, null, 3]}'


def test_schema_rejects_wrong_version_and_missing_instrument(tmp_path):
    x=compact_snapshot(Collector(tmp_path,Offline(),Offline()).collect())
    x['meta']['schema_version']=999
    with pytest.raises(Exception): validate_snapshot(x)
    x['meta']['schema_version']=2
    del x['markets']['DOTBTC']
    with pytest.raises(Exception): validate_snapshot(x)


def test_single_failure_does_not_discard_other_sources(tmp_path):
    collector=Collector(tmp_path,Offline(),Offline())
    good=collector.source('good','https://example.test/good',lambda:{'price':12})
    bad=collector.source('bad','https://example.test/bad',lambda:(_ for _ in ()).throw(RuntimeError('one source failed')))
    assert good=={'price':12}
    assert bad is None
    assert collector.sources['good']['fresh']
    assert not collector.sources['bad']['fresh']


def test_offline_main_publishes_valid_files(tmp_path,monkeypatch):
    import main
    monkeypatch.setattr(main,'Collector',lambda target:Collector(target,Offline(),Offline()))
    data=main.main(tmp_path)
    saved=json.loads((tmp_path/'llm_snapshot.json').read_text())
    assert validate_snapshot(saved)
    assert saved['meta']['status']=='partial'
    assert (tmp_path/'raw'/'latest.json.gz').exists()
    assert (tmp_path/'latest.json').exists()
    assert 'offline fixture' in (tmp_path/'latest.md').read_text()


def test_schema_requires_all_dot_timeframes(tmp_path):
    x=compact_snapshot(Collector(tmp_path,Offline(),Offline()).collect())
    del x['markets']['DOTUSD']['timeframes']['3m']
    with pytest.raises(ValueError,match='inventory'):validate_snapshot(x)


def test_time_fibs_use_snapshot_reference_and_survive_compaction(tmp_path,monkeypatch):
    from datetime import datetime
    import pipeline
    monkeypatch.setattr(pipeline,'utcnow',lambda:datetime.fromisoformat('2026-09-15T14:00:00Z'))
    data=Collector(tmp_path,Offline(),Offline()).collect()
    compact=compact_snapshot(data)
    block=compact['markets']['DOTUSD']['time_fibs']
    assert validate_snapshot(compact)
    assert block['reference_at_utc']==compact['meta']['generated_at_utc']
    assert block['clusters'][1]['state']=='active'
    assert block['clusters'][1]['minutes_to_center']==0
    original=data['markets']['DOTUSD']['time_fibs']
    assert block['anchors']==original['anchors']
    # Existing numeric export rounding is retained; timestamps are unchanged.
    for exported,raw in zip(block['projections'],original['projections']):
        assert exported['projected_at_utc']==raw['projected_at_utc']
        assert exported['state']==raw['state']
        assert exported['minutes_to_center']==pytest.approx(raw['minutes_to_center'])
    # The new field is optional for older v2 consumers/archived snapshots.
    del compact['markets']['DOTUSD']['time_fibs']
    assert validate_snapshot(compact)


@pytest.fixture
def time_fib_reference(monkeypatch):
    from datetime import datetime
    import pipeline
    monkeypatch.setattr(pipeline,'utcnow',lambda:datetime.fromisoformat('2026-09-15T14:00:00Z'))


def test_regenerate_from_latest_is_additive_idempotent_and_preserves_history(tmp_path,monkeypatch,time_fib_reference):
    import main
    from common import write_json
    data=Collector(tmp_path,Offline(),Offline()).collect()
    del data['markets']['DOTUSD']['time_fibs']
    # Runtime perp and live data cannot select or replace configured time anchors.
    data['markets']['DOTUSD']['perp']['pivots']=[{'time_utc':'2026-09-14T12:00:00Z','price':99}]
    write_json(tmp_path/'latest.json',data)
    old_compact=compact_snapshot(data)
    history=(tmp_path/'history.json').read_bytes()
    raw=(tmp_path/'raw'/'latest.json.gz').read_bytes()
    monkeypatch.setattr(main,'Collector',lambda *a:pytest.fail('Offline regeneration must not collect'))
    regenerated=main.regenerate_snapshot(tmp_path)
    saved=json.loads((tmp_path/'llm_snapshot.json').read_text())
    assert validate_snapshot(saved)
    block=saved['markets']['DOTUSD'].pop('time_fibs')
    assert block['status']=='ok'
    assert [a['price'] for a in block['anchors']]==[1.2822,1.1642,0.9959]
    assert saved==old_compact
    assert regenerated['markets']['DOTUSD'].pop('time_fibs')
    assert regenerated==data
    assert (tmp_path/'history.json').read_bytes()==history
    assert (tmp_path/'raw'/'latest.json.gz').read_bytes()==raw
    once=(tmp_path/'llm_snapshot.json').read_bytes()
    main.regenerate_snapshot(tmp_path)
    assert (tmp_path/'llm_snapshot.json').read_bytes()==once


def test_missing_time_config_is_isolated_and_schema_checked(tmp_path,monkeypatch,time_fib_reference):
    import pipeline
    from time_fibs import load_time_fibs
    monkeypatch.setattr(pipeline,'load_time_fibs',lambda ref:load_time_fibs(ref,tmp_path/'absent.json'))
    data=compact_snapshot(Collector(tmp_path,Offline(),Offline()).collect())
    block=data['markets']['DOTUSD']['time_fibs']
    assert block['status']=='unavailable'
    assert validate_snapshot(data)
    block['status']='invented'
    with pytest.raises(Exception): validate_snapshot(data)


@pytest.mark.parametrize('field,value',[('state','bullish'),('projected_at_utc','invalid'),('minutes_to_start',-1)])
def test_time_projection_schema_rejects_invalid_values(tmp_path,field,value,time_fib_reference):
    data=compact_snapshot(Collector(tmp_path,Offline(),Offline()).collect())
    data['markets']['DOTUSD']['time_fibs']['projections'][0][field]=value
    with pytest.raises(Exception): validate_snapshot(data)
