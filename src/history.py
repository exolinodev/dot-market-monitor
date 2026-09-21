"""Bounded hourly state. Never use stale history as a current observation."""
import pandas as pd
from common import finite, read_json, write_json

PERIODS={'1h':1,'4h':4,'24h':24,'7d':168}


def encode_history(history):
    """Losslessly share repeated field paths; retain null versus absent fields."""
    def leaves(value, path=()):
        if isinstance(value, dict) and value:
            for key, item in sorted(value.items()):
                yield from leaves(item, (*path, key))
        else:
            yield path, value

    flattened = [dict(leaves(row)) if row else {} for row in history]
    columns = sorted({path for row in flattened for path in row})
    return {'storage_format': 'hourly-columnar-v1', 'columns': [list(path) for path in columns],
            'rows': [{'values': [row.get(path) for path in columns],
                      'missing': [i for i, path in enumerate(columns) if path not in row]}
                     for row in flattened]}


def decode_history(value):
    if isinstance(value, list):
        if not all(isinstance(row, dict) for row in value):
            raise ValueError('History rows must be objects')
        return value  # Legacy array; next successful update migrates without truncation.
    if not isinstance(value, dict) or set(value) != {'storage_format', 'columns', 'rows'} or value['storage_format'] != 'hourly-columnar-v1':
        raise ValueError('Unsupported history storage format')
    columns, rows = value['columns'], value['rows']
    if not isinstance(columns, list) or not isinstance(rows, list) or any(
            not isinstance(path, list) or not path or any(not isinstance(key, str) for key in path) for path in columns):
        raise ValueError('Invalid history column paths')
    if len({tuple(path) for path in columns}) != len(columns):
        raise ValueError('Duplicate history column')
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'values', 'missing'} or not isinstance(row['values'], list) or len(row['values']) != len(columns):
            raise ValueError('Invalid history row width')
        absent = row['missing']
        if not isinstance(absent, list) or any(type(i) is not int or not 0 <= i < len(columns) for i in absent) or len(set(absent)) != len(absent):
            raise ValueError('Invalid absent history fields')
        missing, out, assigned = set(absent), {}, set()
        for i, path in enumerate(columns):
            if i in missing:
                if row['values'][i] is not None:
                    raise ValueError('Missing history field must use null placeholder')
                continue
            node = out
            for end, key in enumerate(path[:-1], start=1):
                if tuple(path[:end]) in assigned:
                    raise ValueError('Conflicting history column paths')
                node = node.setdefault(key, {})
            if path[-1] in node:
                raise ValueError('Conflicting history column paths')
            node[path[-1]] = row['values'][i]
            assigned.add(tuple(path))
        result.append(out)
    return result


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
        self.history=decode_history(read_json(path,[]))

    def update(self,current):
        changes=history_changes(self.history,current)
        self.history=retain_hourly(self.history,current)
        write_json(self.path,encode_history(self.history))
        return changes
