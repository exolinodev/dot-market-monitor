# Oracle v3: prospective shadow rollout

## Synchronisation and publication identity

The PR branch merged main `90e27f2780ed8769e323e1979e9b997d8a6f1529` without
conflicts. The complete `data/` tree was byte-identical to that main revision
after synchronisation. Final head/base/CI and deployed evidence are recorded in
the PR and rollout result; later collector commits must be incorporated again
before the final merge if main advances.

The writer reserves a deterministic SHA256 key over the canonical object
`{snapshot_sha256, strategy_version}` at
`data/oracle/forecast_keys/<key>.json`. Atomic create-only hard-link publication
arbitrates concurrent processes. Different Git checkouts produce the same key
path with different forecast bindings; their add/add rebase conflict prevents
two winners from reaching main. No force push is used. A changed creation time
or NO_TRADE does not bypass deduplication. A deliberately different strategy
version can publish a distinct forecast ID for the same snapshot.

Existing forecasts without keys are scanned before reservation, without changing
them. The evaluator/archive guard rejects duplicate snapshot/strategy pairs.
Each key binds the exact final forecast hash/path; CI rejects orphaned or changed
keys. A local crash after reservation fails closed until inspected. A failed
Actions job does not commit its incomplete workspace. Bound inputs are preserved
and verified when reused. These controls do not replace GitHub access controls.

## Consumer/prompt coverage decision

All ten requested original observation blocks remain useful. Feature records are
selected derived measurements, not complete substitutes. Projection v2 exports
every block below, including its status, coverage and lineage. Prompt 3.2.0 loads
the observation parts and lists the same contract. Each part remains at most
10,000 UTF-8 bytes, with exact JSON Pointers, original values and the full
snapshot binding. Older immutable browser fixtures retain their original v1
projection and are not modified or republished.

| Original block | Feature coverage | Additional consumer need |
| --- | --- | --- |
| price_levels | Selected penetration/reclaim measurements | Actual levels and their roles/timeframes |
| anchored_vwap | ATR-normalised distances | Original anchors, VWAP prices, coverage and registration time |
| historical_context | Selected extremity/efficiency fields | Distinct historical volatility/range comparisons and sample coverage |
| market_relative | Selected DOT/BTC return/acceleration | Original market comparisons, reference windows and availability |
| flow_windows | Selected effort/result and signed-flow values | Window alignment, source coverage and exact executed-flow semantics |
| volume_profile | Not fully represented | Profile price zones, approximation method and coverage |
| spot_perp_history | Selected OI and price relation | Original comparison windows and funding history |
| cross_venue | Not fully represented | Venue basis, timestamp skew and source freshness |
| scheduled_events | Not represented | Genuine event precision, date/time and source |
| input_lineage | Selected feature source IDs | Shared input dependencies and contemporaneous anchor provenance |

## Repository protection

Main was initially unprotected, with no rulesets. Ruleset `23566182` is now
active for refs/heads/main: deletion and non-fast-forward updates are forbidden,
with no bypass actors (including the current administrator). Ordinary
fast-forward collector/writer commits remain possible.

The authenticated repository role has admin permission, but GitHub rejected app
installation enumeration with HTTP 403 (this credential is not an authorised
GitHub App user token). A scoped bypass identity for ChatGPT's create-file
connector therefore cannot be verified through the available credential. Do not
guess an app ID or grant broad administrator bypass just to make the writer work.

Remaining admin configuration: create a second active branch ruleset for
refs/heads/main requiring pull requests and the `test` check from GitHub Actions
(verified integration ID 15368). Require review-thread resolution; disallow
direct ordinary code pushes. Keep the no-delete/no-force rules in the separate
non-bypassable ruleset. In the PR/check ruleset, allow always-bypass only for
GitHub Actions and the explicitly verified automated submission identity. Review
the latter's ability to bypass code protections; prefer a dedicated automation
identity enforcing allowed data paths. Never add all write/admin roles as a
substitute. Confirm both manual dispatch and create-file submissions and a real
collector commit before enforcing that second ruleset. No credentials or access
grants are created by this rollout. Until the identity is configured, ordinary
code changes follow PR + final-head CI by operating policy, not a complete
server-side PR requirement.

Exact second-ruleset settings: target branch; enforcement active; include
refs/heads/main, exclude none; pull_request with required_approving_review_count=0,
dismiss_stale_reviews_on_push=true, require_code_owner_review=false,
require_last_push_approval=false, required_review_thread_resolution=true;
required_status_checks with context=test, integration_id=15368,
strict_required_status_checks_policy=false, do_not_enforce_on_create=false.
Bypass actors are type Integration, bypass_mode=always: 15368 and the separately
verified submission app ID. No unknown placeholder is to be submitted to the API.

## Finalisation verification

291 Python tests and 17 scheduler tests pass after synchronisation and hardening.
Both writer entrypoints execute against real temporary Git repositories with an
explicit isolated test clock; invalid timestamp/hash/config/evidence/gate/setup
inputs are rejected before publication. Concurrent distinct-ID and independent
Git-checkout races admit only one snapshot/strategy winner. Legacy archives and
NO_TRADE are covered. Real published market data is not generated by these tests.
Schema validation, archive integrity, Python compilation and workflow YAML parsing
pass. The two genuine archived snapshots yield 16 bounded v2 parts, with maxima
9,988 and 9,983 bytes respectively; all 64 features and ten observation blocks
are bound to their exact original values. The complete production data tree
remains unchanged by this patch. The hourly ChatGPT prompt was updated in place
to 3.2.0, preserving its private position block and hourly interval.

## Shadow-phase boundary

The first production forecast must come from the regular hourly ChatGPT Oracle.
No synthetic forecast fills the archive. Observe its submission, matching writer,
immutable forecast/key and decompressed bound input before calling persistence
verified. Later verify 1h/4h/12h Python outcomes, compatible score groups/context
and subsequent text_summary feedback identities. Pending or absent future data
remains pending; no strategy, feature or threshold is fitted to an individual
result. This rollout makes no profitability claim.
