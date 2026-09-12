"""Hourly entry point; collection failures are data, schema failures are bugs."""
from pathlib import Path
from common import json_safe, write_json
from pipeline import Collector
from output import compact_snapshot, validate_snapshot, markdown_summary

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


if __name__=='__main__':
    main()
