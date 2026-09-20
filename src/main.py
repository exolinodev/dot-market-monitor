"""Hourly entry point; collection failures are data, schema failures are bugs."""
from pathlib import Path
import argparse
import json
import time
from common import json_safe, write_json
from pipeline import Collector
from output import compact_snapshot, validate_snapshot, markdown_summary
from time_fibs import load_time_fibs
from observations import build_observations, replay_inputs
from observation_history import ObservationArchive
from observation_common import block

ROOT=Path(__file__).resolve().parents[1]
DATA_DIR=ROOT/'data'


def main(data_dir=None, run_kind=None, boundary=None):
    timer = time.monotonic()
    target=Path(data_dir) if data_dir is not None else DATA_DIR
    target.mkdir(parents=True,exist_ok=True)
    ledger_active = (target / 'ledger/genesis.json').exists()
    trusted_head = None
    if ledger_active:
        if run_kind is None:
            raise ValueError('Active ledger collection requires an explicit cycle')
        import subprocess
        trusted_head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    if run_kind is not None:
        from cycles import resolve, metadata
        from common import utcnow
        from intraday import collect as collect_intraday
        run_kind, point = resolve(utcnow(), run_kind, boundary, 'workflow_dispatch')
        started = utcnow()
        quarter_error = None
        try:
            quarter = collect_intraday(target, run_kind, point, persist_result=False) if ledger_active else collect_intraday(target, run_kind, point)
        except Exception as exc:
            if run_kind == 'light' or ledger_active: raise
            quarter_error = str(exc)
        if run_kind == 'light':
            if ledger_active:
                from ledger_runtime import finish_quarter
                finish_quarter(target, quarter, point, ROOT, trusted_head)
            print(f"Light quarter {quarter['meta']['cycle_boundary_utc']}: {quarter['meta']['status']}")
            return quarter
    data=Collector(target).collect()
    if run_kind is not None:
        from perp_data import enrich_hourly
        data.update(metadata(run_kind, point, started))
        if ledger_active:
            enrich_hourly(data, target, point, pending_quarter=quarter)
            from ledger_runtime import finish_quarter, execution_context
            finish_quarter(target, quarter, point, ROOT, trusted_head)
            data['markets']['DOTUSD']['intraday']['quarters'][-1]['ledger'] = quarter['ledger']
            data['markets']['DOTUSD']['execution_context'] = execution_context(target, quarter, point, data['generated_at_utc'])
        else:
            enrich_hourly(data, target, point)
        if quarter_error:
            data['errors'].append({'source_id': 'intraday', 'error': quarter_error})
            data['status'] = 'partial'
        data['run_duration_seconds'] = round(time.monotonic() - timer, 3)
    compact=compact_snapshot(data)
    validate_snapshot(compact)
    write_json(target/'latest.json',data)
    write_json(target/'llm_snapshot.json',compact)
    from oracle_consumer import write_consumer_bundle
    write_consumer_bundle(compact,target)
    (target/'latest.md').write_text(markdown_summary(data),encoding='utf-8')
    print(f"Wrote {target/'llm_snapshot.json'} ({(target/'llm_snapshot.json').stat().st_size:,} bytes)")
    print(f"Status: {data['status']}; {len(data['sources'])} sources; {len(data['errors'])} errors")
    for error in data['errors']: print(f"- {error['source_id']}: {error['error']}")
    return data


def regenerate_snapshot(data_dir=None):
    """Rebuild from saved measurements, preserving their reference time/history."""
    target=Path(data_dir) if data_dir is not None else DATA_DIR
    data=json.loads((target/'latest.json').read_text(encoding='utf-8'))
    data['markets']['DOTUSD']['time_fibs']=load_time_fibs(data['generated_at_utc'])
    frames,spot_trades,perp_trades,quote=replay_inputs(data,target)
    archive_error=None
    try:
        archive=ObservationArchive(target/'raw'/'observation_history.json.gz')
    except Exception as exc:
        archive=None
        archive_error=str(exc)
    observation=build_observations(data,frames,spot_trades,perp_trades,quote,archive)[0]
    if archive_error:
        observation['components']['spot_perp_history']=block(status='error',reason=archive_error)
        observation['status']='partial'
    data['markets']['DOTUSD']['observations']=observation
    from oracle_context import attach_oracle
    attach_oracle(data, target, persist=False)
    compact=compact_snapshot(data)
    validate_snapshot(compact)
    write_json(target/'latest.json',data)
    write_json(target/'llm_snapshot.json',compact)
    from oracle_consumer import write_consumer_bundle
    write_consumer_bundle(compact,target)
    print(f"Regenerated {target/'llm_snapshot.json'} from saved data at {data['generated_at_utc']}")
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',type=Path,default=DATA_DIR,
        help='Output/history directory; use an isolated directory for live smoke tests')
    parser.add_argument('--from-latest',action='store_true',help='Rebuild from latest.json without fetching data or advancing its timestamp/history')
    parser.add_argument('--run-kind', choices=['full', 'light'], default='full')
    parser.add_argument('--boundary')
    args=parser.parse_args()
    regenerate_snapshot(args.data_dir) if args.from_latest else main(args.data_dir, args.run_kind, args.boundary)
