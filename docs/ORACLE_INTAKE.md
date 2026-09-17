# Oracle intake after the 17 September shadow audit

Prompt 3.3.0 sends one fresh envelope through a same-repository draft PR. Create
`oracle-submission/YYYYMMDDTHHMMSSZ` from the consumed full main SHA before the
final forecast is written. Add exactly one regular
`data/oracle/submissions/<forecast_id>.json`, then immediately open a draft PR
to main. No other commits, test files, edits, reopenings or reruns are supported.
The 120-second creation-to-validation limit is unchanged. The branch timestamp
does not set the forecast clock. A queued or rejected draft is not persisted.

`pull_request_target: opened` runs the writer from trusted main. It never checks
out or executes the PR head, merges its tree, or installs its dependencies. It
fetches the exact head object only to inspect one data addition. Forks, extra
files, multi-commit submissions, symlinks, reused IDs, malformed/duplicate-key
JSON, future/stale data and invalid forecast semantics are rejected. Runtime
checks still bind the snapshot/config/evidence and reserve snapshot + strategy
atomically. Only validated submission/forecast/input/key/receipt files are staged
by the trusted writer; the staged archive guard runs before commit. On successful
publication the draft PR is closed without merging or deleting the branch.

Manual `workflow_dispatch` remains available to authorised operators and uses
the same validation and receipts. All publication run attempts after the first
are rejected, including manual reruns. The retired push-to-main intake function
is retained for historical compatibility tests; it is no longer a workflow
trigger or an authorised consumer write path.

Ordinary code merges require PRs and the GitHub Actions `test` check. The only
automation exception is GitHub Actions (integration 15368) for the trusted
collector/writer. ChatGPT has no main bypass and only prepares draft branches.
The independent no-delete/no-force ruleset remains non-bypassable. The privileged
workflows execute exclusively on main; untrusted PR CI has contents:read.

## Readable bound-input receipts

New publications include `data/oracle/receipts/<forecast_id>.json`. Python
re-reads the forecast and decompresses its actual `.json.gz` input before emitting
the canonical snapshot hash, forecast hash, submission hash, original snapshot
commit, byte count and exact Git blob SHA1. SHA1 here identifies the Git blob;
SHA256 remains the canonical data integrity hash.

Consumers read the receipt and final forecast at the same main SHA and compare
the compressed input's GitHub blob metadata to the receipt. This is explicitly a
server-side verification with checked blob binding, not a claim that ChatGPT
itself decompressed the input. Independent decompression remains preferred when
available. No historical receipts are fabricated or backfilled.

## Append-only enforcement and audit history

The archive guard now checks every commit in the inspected range as well as the
net diff, and runs for PRs, direct main push CI and staged collector/writer data.
It rejects temporary add/delete sequences, updates, renames, symlinks and invalid
new submission paths/content. Existing immutable forecast/input/outcome bindings
and every new receipt are verified. All PRs get a test check, including docs-only
PRs, so the required status cannot remain pending due to path filters.

The two rejected 12:06/16:01 submissions remain historical evidence; they are not
repaired, re-dated, retried or treated as forecasts. The historical addition and
deletion of `submissions/test.json` remains visible in Git. A historical audit
including that deletion correctly fails; ordinary new change ranges do not
silently exempt future deletions. No strategy, feature, threshold or past outcome
changes are part of this operational fix.
