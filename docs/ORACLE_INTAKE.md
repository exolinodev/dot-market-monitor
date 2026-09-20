# Oracle intake after the 17 September shadow audit

Prompt 3.3.x sends one fresh envelope through a same-repository draft PR. Create
`oracle-submission/YYYYMMDDTHHMMSSZ` from the consumed full main SHA before the
final forecast is written. Add exactly one regular
`data/oracle/submissions/<forecast_id>.json`, then immediately open a draft PR
to main. No other commits, test files, edits, reopenings or reruns are supported.
The 120-second creation-to-acceptance limit is unchanged. Since the 19 September
stabilisation it is measured against the draft's GitHub-recorded opening time
(`pull_request.created_at`), an independent timestamp the consumer cannot edit,
instead of the moment the Actions runner happened to start. Runner queueing no
longer rejects an honest submission; the consumer's own delay between
`created_at_utc` and opening the PR still does. A draft opened more than 30
minutes before the writer runs, or after the writer clock, is rejected as a stale
or implausible queue. The branch timestamp does not set the forecast clock. A
queued or rejected draft is not persisted.

`pull_request_target: opened` runs the writer from trusted main. It never checks
out or executes the PR head, merges its tree, or installs its dependencies. It
fetches the exact head object only to inspect one data addition. Forks, extra
files, multi-commit submissions, symlinks, reused IDs, malformed/duplicate-key
JSON, future/stale data and invalid forecast semantics are rejected. Runtime
checks still bind the snapshot/config/evidence and reserve snapshot + strategy
atomically. Only validated submission/forecast/input/key/receipt files are staged
by the trusted writer; the staged archive guard runs before commit. On successful
publication the draft PR is closed without merging or deleting the branch. A
rejected draft is closed as well, with the writer's one-line verdict as the closing
comment (for example `Submission JSON is truncated (7118 bytes, brace depth +1,
ends with '...')`, `Publication timestamp differs from actual creation by +151s`
or `Evidence must reference available actual features; not ok at this snapshot:
structure.1h.new_low`). Closing never reopens, edits or re-runs anything; the
branch and its file stay as audit evidence.

Manual `workflow_dispatch` remains available to authorised operators and uses
the same validation and receipts. All publication run attempts after the first
are rejected, including manual reruns. The retired push-to-main intake function
is retained for historical compatibility tests; it is no longer a workflow
trigger or an authorised consumer write path.

The target main policy requires PRs and an up-to-date GitHub Actions `test` check,
with no bypass actors. Activate that policy only after the data-PR workflows are
merged; the earlier direct-push producers cannot operate under it. The repository
Actions setting must allow PR creation before the merge. GitHub couples creation
with review approval in one setting; these workflows never approve reviews.

Rollout checkpoint, 17 September: Actions PR creation is enabled with default
workflow permissions still `read`. Ruleset `23616485` (`main: require validated
PRs`) is prepared but disabled pending the reviewed merge of PR #5. It requires
`test` from GitHub Actions integration 15368 and an up-to-date branch, with no
bypass actors. Activate it immediately after merging #5 and verify a real
collector and genuine Oracle publication under the policy. Until then only the
independent no-delete/no-force ruleset `23566182` is enforced; do not describe the
required test policy as already deployed.

The trusted collector/writer stages only allowlisted data and uses
`scripts/promote_data.py` to create a unique automation branch. It runs archive
integrity, snapshot schema, all Python tests and scheduler tests on the exact
commit before publishing a `test` check and normally merging its data PR with
SHA binding. GITHUB_TOKEN-created PRs do not start ordinary PR CI; the check links
to the actual producer test logs. Failed checks never create a success result or
merge. A main advance aborts publication instead of replaying stale data. After
an abort, the producer revokes its success check, closes its data PR and removes
its own unchanged branch. A PR-creation failure also cleans the pushed branch;
an ambiguous API response is reconciled against the unique run's PR. After a
confirmed merge, the automation branch is removed. Ref deletion uses a Git lease
on the exact tested SHA, so newer branch work is preserved. Cleanup failures are
explicit workflow warnings and do not turn an already confirmed publication into
a failed forecast. Original Oracle submission branches remain audit evidence and
are never removed by this cleanup.

Input drafts close only after successful data promotion. No code from the
input draft runs and its tree is never merged. The repository permits Actions to
create PRs; no review approval or bypass is used. The independent no-delete/no-force
ruleset stays active. Privileged workflows execute exclusively on main; untrusted
PR CI has contents:read. ChatGPT only prepares the isolated input draft branch.

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
and every new receipt are verified. Every code, docs or submission PR gets a
test check, so the required status cannot remain pending due to path filters;
pure `data/**` producer PRs are covered by the producer run's own `test` check.

Rejected submissions (the 17 September 12:06/16:01 drafts as well as the 18/19
September drafts #35, #52, #78, #89, #94, #96 and #101: two late openings, three
unbalanced JSON envelopes, two citations of `unavailable` features) remain
historical evidence; they are not repaired, re-dated, retried or treated as
forecasts. Ordinary PR CI no longer runs on `data/**` changes except
`data/oracle/submissions/**`, because trusted producer data PRs carry their own
`test` check-run and a GITHUB_TOKEN-created PR only produced an empty failed
run. The historical addition and
deletion of `submissions/test.json` remains visible in Git. A historical audit
including that deletion correctly fails; ordinary new change ranges do not
silently exempt future deletions. No strategy, feature, threshold or past outcome
changes are part of this operational fix.
