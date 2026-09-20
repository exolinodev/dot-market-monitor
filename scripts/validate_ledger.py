"""Enforce ledger archive immutability and replay the exact Git candidate tree."""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from ledger_store import verify


def git(root, *args):
    return subprocess.check_output(['git', *args], cwd=root)


def blob(root, ref, name):
    return git(root, 'show', ref + ':' + name)


def paths(root, ref):
    args = ('ls-files', '-z', '--', 'data/ledger/') if ref == '' else ('ls-tree', '-r', '--name-only', '-z', ref, '--', 'data/ledger/')
    return set(git(root, *args).decode().strip('\0').split('\0')) - {''}


def check_transition(root, before, after):
    old, new = paths(root, before), paths(root, after)
    if old - new:
        raise ValueError('Ledger artifacts cannot be removed')
    for name in new:
        relative = name.removeprefix('data/ledger/')
        current = blob(root, after, name)
        if relative in ('state.json', 'performance.json'):
            continue  # Derived views must still match replay below.
        if re.fullmatch(r'events/\d{4}/\d{2}/\d{2}\.jsonl', relative):
            previous = blob(root, before, name) if name in old else b''
            if not current.endswith(b'\n') or (previous and not previous.endswith(b'\n')) or not current.startswith(previous):
                raise ValueError('Ledger journal prefix changed: ' + name)
        elif relative == 'genesis.json' or re.fullmatch(r'(plans|states|trades)/[A-Za-z0-9_-]+\.json', relative):
            if name in old and current != blob(root, before, name):
                raise ValueError('Immutable ledger artifact changed: ' + name)
        else:
            raise ValueError('Unknown ledger artifact: ' + name)


def check(base=None, head='HEAD', staged=False, root=Path('.')):
    root = Path(root)
    candidate = '' if staged else head
    if staged:
        check_transition(root, 'HEAD', '')
    else:
        if not base:
            raise ValueError('Trusted ledger base required')
        # Reject rewriting and subsequently restoring evidence within a PR too.
        for commit in git(root, 'rev-list', base + '..' + head).decode().split():
            check_transition(root, commit + '^', commit)
        check_transition(root, base, head)
    names = paths(root, candidate)
    if not names:
        return
    modes = git(root, 'ls-files', '--stage', '--', 'data/ledger/') if staged else git(root, 'ls-tree', '-r', head, '--', 'data/ledger/')
    if any(line.split()[0] != b'100644' for line in modes.splitlines()):
        raise ValueError('Ledger artifacts must be regular non-executable files')
    with tempfile.TemporaryDirectory(prefix='oracle-ledger-verify-') as folder:
        directory = Path(folder)
        for name in names:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob(root, candidate, name))
        result = verify(directory / 'data')
        from ledger_publication import verify_instruction
        from ledger_market import SOURCE_PATH, verify_market_input
        source_cache, copied_sources = {}, set()
        production = any(r['input']['type'] == 'instruction' and r['input']['plan']['forecast_id'].endswith('-oracle-v4') for r in result['records'])
        for record in result['records']:
            event = record['input']
            if event['type'] != 'instruction' and (production or 'source' in event):
                source = (event.get('source') or {}).get('path', '')
                if not re.fullmatch(SOURCE_PATH, source):
                    raise ValueError('Production market input requires source evidence')
                if source not in copied_sources:
                    name = 'data/' + source
                    target = directory / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(blob(root, candidate, name))
                    copied_sources.add(source)
                verify_market_input(directory / 'data', result['genesis']['epoch_utc'], event, source_cache)
            if event['type'] == 'instruction' and event['plan']['forecast_id'].endswith('-oracle-v4'):
                if 'publication' not in event:
                    raise ValueError('Production instruction requires main publication evidence')
                verify_instruction(root, git(root, 'rev-parse', 'HEAD' if staged else head).decode().strip(), event)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--head', default='HEAD')
    parser.add_argument('--staged', action='store_true')
    args = parser.parse_args()
    check(args.base, args.head, args.staged)
    print('Ledger archive and deterministic replay OK')
