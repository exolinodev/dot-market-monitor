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
