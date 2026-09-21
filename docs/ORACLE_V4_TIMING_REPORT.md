# Measuring the Phase 1 observation window

`scripts/rollout_timing.py` reads a full local Git history at an explicit trusted
main SHA. It does not fetch, deploy, dispatch, modify data or activate an account.
After fetching main, pass its full immutable SHA and the actual observation
window, using quarter-aligned UTC boundaries:

```sh
python scripts/rollout_timing.py --head <full-main-sha> --start <inclusive-UTC-boundary> --end <exclusive-UTC-boundary>
```

Save the JSON outside the immutable market-data archive. A window cannot meet
its scoped criteria until at least 48 hours have elapsed through its end. Select
the start after verified deployment/cron propagation, not a retrospectively chosen
quiet period. A missing quarter counts against the expected 192 observations;
only healthy, fresh records count as on time. The threshold is at least 95%
with recorded acquisition lag <=30 seconds. Longer windows keep their full
expected-quarter denominator. The operational `late` flag uses a
different threshold and is deliberately not used for this acceptance calculation.

For every quarter, the report separates:

- Recorded acquisition lag relative to its boundary.
- Recorded collection duration (not GitHub runner/setup duration).
- Boundary-to-main-publication duration.
- Acquisition-to-main-publication duration.

Publication is the committer time of first appearance in the supplied main
first-parent history, including merge commits. A producer branch's earlier commit
time is never used. All expected light quarters must have recorded collection
and boundary-to-publication durations strictly below 60 seconds for the scoped
criterion to pass. This is a conservative publication deadline. Publication
commits are also inspected for `.gz` changes. A combined/full merge publishing a
light row with gzip changes cannot prove an isolated light publication and fails
that criterion. Archive rewrites, duplicate quarters, wrong archive days, shallow
history and inconsistent times fail rather than silently weakening the evidence.

`timing_and_light_gzip_criteria_met` covers only those checks.
`phase1_acceptance_proven` remains false: the report does not independently prove
Cloudflare cron provenance versus manual dispatch, the four-quarter hourly
snapshot contract, writer latency, or production Git storage growth. Collect
those separate deployment/runner/commit measurements before concluding Phase 1.
A clean unit test or isolated smoke run is not a production observation window.
