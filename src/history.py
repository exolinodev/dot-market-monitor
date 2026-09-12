"""Bounded hourly state. Never use stale history as a current observation."""
import pandas as pd
from common import finite, read_json, write_json

PERIODS={'1h':1,'4h':4,'24h':24,'7d':168}


def nearest(history, now, hours, tolerance_minutes=20):
    target=pd.Timestamp(now).timestamp()-hours*3600
    valid=[h for h in history if h.get('schema_version')==2 and h.get('unix',float('inf'))<pd.Timestamp(now).timestamp()]
    if not valid: return None
    closest=min(valid,key=lambda x:abs(x['unix']-target))
    return closest if abs(closest['unix']-target)<=tolerance_minutes*60 else None


def deltas(current, old):
    values={}
    for key,value in current['values'].items():
        previous=old.get('values',{}).get(key) if old else None
        good=finite(value) is not None and finite(previous) is not None
        values[key]={'absolute':float(value-previous) if good else None,
                     'relative_pct':float((value/previous-1)*100) if good and previous!=0 else None,
                     'status':'ok' if good else 'insufficient_history_or_current_data'}
    return {'reference_utc':old.get('generated_at_utc') if old else None,'values':values}


def history_changes(history, current):
    now=current['generated_at_utc']
    previous=[x for x in history if x.get('schema_version')==2 and x.get('unix',0)<current['unix']]
    last=max(previous,key=lambda x:x['unix']) if previous else None
    return {'since_previous_run':deltas(current,last),**{name:deltas(current,nearest(history,now,hours)) for name,hours in PERIODS.items()}}


def retain_hourly(history, current, days=30):
    cutoff=current['unix']-days*86400
    buckets={}
    for item in sorted([*history,current],key=lambda x:x.get('unix',0)):
        if item.get('schema_version')==2 and cutoff<=item.get('unix',0)<=current['unix']:
            buckets[int(item['unix']//3600)]=item
    return list(buckets.values())[-days*24:]


class HistoryStore:
    def __init__(self,path):
        self.path=path
        self.history=read_json(path,[])
        if not isinstance(self.history,list): raise ValueError('History must be an array')

    def update(self,current):
        changes=history_changes(self.history,current)
        self.history=retain_hourly(self.history,current)
        write_json(self.path,self.history)
        return changes
