#!/usr/bin/env python3
"""Frozen prompt/model replay harness. Fixture validation is NOT an LLM backtest.

Accepts explicitly exported point-in-time compact snapshots only. Each request
contains just its snapshot and the frozen prompt. Outputs live in an experiment
folder, never in the actual published forecast archive.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import requests
from oracle_common import canonical,digest
from oracle_forecasts import validate_forecast,create_only
from oracle_evaluator import evaluate_forecast
from oracle_context import outcome_frames
from oracle_scorecard import scorecard


def run(prompt_path,snapshots,output,model=None,endpoint='https://api.openai.com/v1/chat/completions'):
    prompt=prompt_path.read_text()
    eligible=[];skipped=[]
    for path in snapshots:
        snapshot=json.loads(path.read_text())
        dot=snapshot['markets']['DOTUSD']
        if not (dot.get('observations') or {}).get('config_sha256') or not (dot.get('oracle_context') or {}).get('current_features'):
            skipped.append({'snapshot_sha256':digest(snapshot),'reason':'original_measurement_config_or_oracle_inputs_unavailable'})
        else: eligible.append(path)
    manifest={'prompt_sha256':hashlib.sha256(prompt.encode()).hexdigest(),'model':model,
        'snapshot_sha256':[digest(json.loads(p.read_text())) for p in snapshots],
        'eligible_snapshot_count':len(eligible),'skipped_snapshots':skipped,
        'mode':'actual_model_invocation' if model else 'prepare_only', 'future_context_in_requests':False}
    output.mkdir(parents=True,exist_ok=True)
    create_only(output/'manifest.json',manifest)
    create_only(output/'frozen_prompt.json',{'text':prompt})
    for path in eligible:
        snapshot=json.loads(path.read_text())
        request={'model':model,'messages':[{'role':'system','content':prompt},
            {'role':'user','content':'Historical point-in-time experiment. Use the snapshot time as creation time. Return only the oracle_forecast JSON.\n'+canonical(snapshot)}],
            'temperature':0,'response_format':{'type':'json_object'}}
        request['messages'][0]['content'] += '\nReturn exactly schema/oracle_forecast.schema.json, provided below. Use the supplied deterministic snapshot hash; do not recompute it mentally.\n'+(Path(__file__).resolve().parents[1]/'schema/oracle_forecast.schema.json').read_text()
        request['messages'][1]['content'] += '\nDeterministic snapshot_sha256: '+digest(snapshot)
        ident=digest(snapshot)
        create_only(output/(ident+'-request.json'),request)
        create_only(output/(ident+'-snapshot.json'),snapshot)
        if model:
            key=os.environ.get('OPENAI_API_KEY')
            if not key: raise RuntimeError('OPENAI_API_KEY required; no generated model results are available')
            response=requests.post(endpoint,headers={'Authorization':'Bearer '+key},json=request,timeout=120)
            response.raise_for_status();body=response.json()
            create_only(output/(ident+'-response.json'),body)
            forecast=json.loads(body['choices'][0]['message']['content'])
            validate_forecast(forecast,snapshot)
            create_only(output/(ident+'-forecast.json'),forecast)
    return manifest


def evaluate_experiment(directory,data_dir):
    latest=json.loads((data_dir/'latest.json').read_text())
    frames=outcome_frames(latest,data_dir)
    forecasts=[json.loads(p.read_text()) for p in sorted(directory.glob('*-forecast.json'))]
    outcomes=[]
    for forecast in forecasts:
        snapshot=json.loads((directory/(forecast['snapshot_sha256']+'-snapshot.json')).read_text())
        validate_forecast(forecast,snapshot)
        out=evaluate_forecast(forecast,frames,latest['generated_at_utc'])
        create_only(directory/(forecast['forecast_id']+'-outcome.json'),out)
        outcomes.append(out)
    result=scorecard(forecasts,outcomes,latest['generated_at_utc'])
    create_only(directory/'scorecard.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prompt',type=Path,default=Path('docs/ORACLE_V3_PROMPT_ARCHIVE.md'))
    p.add_argument('--snapshots',type=Path);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--evaluate-with',type=Path)
    p.add_argument('--model');p.add_argument('--endpoint',default='https://api.openai.com/v1/chat/completions')
    a=p.parse_args()
    if a.evaluate_with:
        print(json.dumps(evaluate_experiment(a.output,a.evaluate_with),indent=2))
    elif a.snapshots:
        print(json.dumps(run(a.prompt,sorted(a.snapshots.glob('*.json')),a.output,a.model,a.endpoint),indent=2))
    else: p.error('--snapshots or --evaluate-with is required')
