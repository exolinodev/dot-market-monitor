"""Hourly entry point; collection failures are data, schema failures are bugs."""
from pathlib import Path
import argparse
import json
from common import json_safe, write_json
from pipeline import Collector
from output import compact_snapshot, validate_snapshot, markdown_summary
from time_fibs import load_time_fibs

ROOT=Path(__file__).resolve().parents[1]
DATA_DIR=ROOT/'data'


def main(data_dir=None):
    target=Path(data_dir) if data_dir is not None else DATA_DIR
    target.mkdir(parents=True,exist_ok=True)
    data=Collector(target).collect()
    compact=compact_snapshot(data)
    validate_snapshot(compact)
    write_json(target/'latest.json',data)
    write_json(target/'llm_snapshot.json',compact)
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
    compact=compact_snapshot(data)
    validate_snapshot(compact)
    write_json(target/'latest.json',data)
    write_json(target/'llm_snapshot.json',compact)
    print(f"Regenerated {target/'llm_snapshot.json'} from saved data at {data['generated_at_utc']}")
    return data


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--from-latest',action='store_true',help='Rebuild from latest.json without fetching data or advancing its timestamp/history')
    args=parser.parse_args()
    regenerate_snapshot() if args.from_latest else main()
