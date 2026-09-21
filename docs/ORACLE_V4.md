# Oracle v4 rollout

Phase 0 started on 2026-09-20. The implementation plan is the user-supplied
`oracle-v4-plan.md` in the parent workspace.

Configurable paper baseline selected under user delegation on 2026-09-20: paper equity USD 5000, Kraken base-tier fees enabled
(maker 0.02%, taker 0.05%, configurable), risk 1% per trade scaled by risk tier,
notional capped at 2x equity, maximum holding time 72 hours, pending entry
expiry 150 minutes. Live notional stages USD 500 / 2000 / 5000 require separate
explicit approval at each stage. No exchange execution is enabled by Phase 0.

## Phase 0 evidence

- Writer fix: PR #221. Local validation: 337 Python tests, 17 scheduler tests,
  immutable archive guard. GitHub test check succeeded.
- Closed the seven documented rejected drafts: #35, #52, #78, #89, #94, #96,
  #101, plus the four subsequently identified drafts #136, #192, #203, #214.
- Runner capture: PR #222, manual `Oracle v4 public fixtures` workflow.
  Captures unauthenticated public responses into a downloadable artifact;
  it does not publish collector data or change any account.
- Both merges explicitly approved by the user on 2026-09-20; #221 merged first.

## Fixture acceptance

After merging the workflow, dispatch `oracle-fixtures.yml` on main. Download
`oracle-v4-public-fixtures` and inspect every manifest entry and payload before
checking fixtures into `tests/fixtures/v4/`. Response body bytes are preserved
without JSON reserialization; manifest SHA-256 hashes cover those exact bytes.
HTTP 200 alone is not acceptance:
check exchange error codes, non-empty series, instrument, timestamp units,
funding sign/units/interval and candle ordering. Document unavailable candidate
feeds instead of inventing fixtures. One successful run does not prove stable
availability. Runtime ingestion and funding accounting remain disabled until
this inspection and the corresponding parser tests are complete.

Phase 1 was merged in #228 and the quarter-hour Worker deployed on 2026-09-21.
The dependent implementation stack was published together in release #265
(main `172c855`), with 613 Python tests passing on the exact release candidate.
The original stacked PRs were closed after checking their inclusion in that
release. The saved hourly v3 task now includes the authorized boundary guard.
Production paper genesis and model migration are tracked separately below;
demo/live sending remains disabled and acceptance windows remain open.

See [ORACLE_V4_ROLLOUT_STATUS.md](ORACLE_V4_ROLLOUT_STATUS.md) for the current
phase-by-phase audit, specific production gates and the isolated real-market
paper initialization/first-quarter rehearsal. The ledger contract is described
in [ORACLE_V4_LEDGER.md](ORACLE_V4_LEDGER.md).
