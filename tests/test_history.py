from datetime import datetime,timezone,timedelta
from history import history_changes,retain_hourly

NOW=datetime(2026,9,12,12,tzinfo=timezone.utc)

def entry(hours,value=58):
    now=NOW-timedelta(hours=hours)
    return {'schema_version':2,'unix':now.timestamp(),'generated_at_utc':now.isoformat(),'values':{'dominance':value}}


def test_history_does_not_mislabel_short_history_as_7d():
    x=history_changes([entry(1)],entry(0,59))
    assert x['1h']['values']['dominance']['absolute']==1
    assert x['24h']['values']['dominance']['absolute'] is None
    assert x['7d']['values']['dominance']['absolute'] is None


def test_real_deltas_and_timestamp_tolerance():
    x=history_changes([entry(1),entry(4,56),entry(24,57),entry(168,50)],entry(0,59))
    assert x['24h']['values']['dominance']['absolute']==2
    assert x['7d']['values']['dominance']['absolute']==9
    assert x['4h']['values']['dominance']['relative_pct']==(59/56-1)*100
    y=history_changes([entry(1.6)],entry(0,59))
    assert y['1h']['values']['dominance']['absolute'] is None


def test_null_current_never_uses_a_previous_value():
    x=history_changes([entry(1,58)],entry(0,None))
    assert x['since_previous_run']['values']['dominance']['absolute'] is None


def test_hourly_dedup_and_30_day_retention():
    history=[entry(i) for i in range(800,0,-1)]
    history.append(entry(.2))
    result=retain_hourly(history,entry(0,60))
    assert len(result)<=720
    assert result[-1]['values']['dominance']==60
    assert min(x['unix'] for x in result)>=NOW.timestamp()-30*86400


def test_columnar_storage_is_lossless_for_missing_null_and_nested_fields():
    from copy import deepcopy
    from history import encode_history, decode_history
    rows = [
        {'schema_version': 2, 'values': {'a': None, 'b': 1.2345678901234567}, 'walls': [None, {'price': 1.0}]},
        {'schema_version': 2, 'values': {'b': 2, 'c': 0}, 'walls': [], 'empty': {}},
        {'schema_version': 2, 'values': {}, 'empty': {'nested': 1}},
        {}]
    original = deepcopy(rows)
    assert decode_history(encode_history(rows)) == rows == original
    assert decode_history(encode_history([])) == []


def test_history_migrates_legacy_and_preserves_deltas(tmp_path):
    import json
    from history import HistoryStore, decode_history
    path = tmp_path / 'history.json'
    old = [entry(1), entry(4, 56)]
    path.write_text(json.dumps(old))
    store = HistoryStore(path)
    expected = history_changes(old, entry(0, 60))
    assert store.update(entry(0, 60)) == expected
    stored = json.loads(path.read_text())
    assert stored['storage_format'] == 'hourly-columnar-v1'
    assert decode_history(stored) == retain_hourly(old, entry(0, 60))
    assert HistoryStore(path).history == store.history


def test_columnar_storage_rejects_ambiguous_or_truncated_records():
    import pytest
    from history import decode_history
    payload = {'storage_format': 'hourly-columnar-v1', 'columns': [['a'], ['a', 'b']],
               'rows': [{'values': [{}, 1], 'missing': []}]}
    with pytest.raises(ValueError, match='Conflicting'):
        decode_history(payload)
    payload['rows'][0]['values'] = [1]
    with pytest.raises(ValueError, match='width'):
        decode_history(payload)


def test_columnar_history_reduces_repeated_field_names_without_rounding():
    from common import dumps
    from history import encode_history, decode_history
    rows = [entry(i, 1.2345678901234567) for i in range(100)]
    encoded = encode_history(rows)
    assert len(dumps(encoded)) < len(dumps(rows))
    assert dumps(decode_history(encoded)) == dumps(rows)
