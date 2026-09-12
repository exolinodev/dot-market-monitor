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
