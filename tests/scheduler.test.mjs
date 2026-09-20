import test from "node:test";
import assert from "node:assert/strict";
import worker, { cycleStart, decide, reconcile, readState, scheduleWindow } from "../src/scheduler/worker.mjs";

const start = Date.parse("2026-09-12T20:59:00Z");
const now = start + 5 * 60_000;
const at = (offset) => new Date(start + offset * 60_000).toISOString();
const run = (status, offset = 0, id = 1) => ({ id, status, created_at: at(offset) });
const snapshot = (offset, status = "ok") => ({ generated_at_utc: at(offset), status, fresh: true, run_kind: "full", cycle_boundary_utc: at(1) });
const choice = (values = {}) => decide({ start, now, phase: "verify", runs: [], snapshot: snapshot(-60), ...values });
const env = { ENABLED: "true", GITHUB_TOKEN: "test-only-token", CONTROL_TOKEN: "test-control" };
const sha = "a".repeat(40);

test("UTC quarter cycle includes previous hour and date rollover", () => {
  assert.equal(cycleStart(start), start);
  assert.equal(cycleStart(now), start);
  assert.equal(cycleStart(start - 1), start - 900000);
  assert.equal(new Date(cycleStart(Date.parse("2026-09-13T00:00:00Z"))).toISOString(), "2026-09-12T23:59:00.000Z");
});
test("fresh successful and partial snapshots suppress duplicate collection", () => {
  for (const status of ["ok", "partial"]) assert.equal(choice({ snapshot: snapshot(1, status) }).action, "fresh");
});
test("old, future, false freshness, error and invalid timestamps need collection", () => {
  for (const value of [snapshot(-1), snapshot(7), snapshot(1, "error"),
    { ...snapshot(1), fresh: false }, { generated_at_utc: "bad" }, null]) {
    assert.equal(choice({ snapshot: value }).action, "dispatch");
  }
});
test("all GitHub active states prevent another dispatch, including old runs", () => {
  for (const status of ["queued", "in_progress", "waiting", "pending", "requested"]) {
    assert.equal(choice({ runs: [run(status, -60)] }).action, "already_running");
  }
});
test("initial delivery is idempotent once an attempt exists; verifier permits one recovery", () => {
  const runs = [run("completed")];
  assert.equal(choice({ runs, phase: "initial" }).action, "attempt_limit");
  assert.equal(choice({ runs }).action, "dispatch");
  assert.equal(choice({ runs: [...runs, run("completed", 4, 2)] }).action, "attempt_limit");
  assert.equal(choice({ runs: [run("completed", -1)], phase: "initial" }).action, "dispatch");
});
test("unknown upstream run state fails closed", () => {
  assert.throws(() => choice({ runs: [run("mystery")] }), /invalid_run_schema/);
  assert.throws(() => choice({ runs: [{ status: "completed", created_at: "bad" }] }), /invalid_run_schema/);
  assert.throws(() => choice({ phase: "unknown" }), /invalid_phase/);
});

function mockGitHub({ runs = [], meta = snapshot(-60), dispatchStatus = 204 } = {}) {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    assert.equal(options.redirect, "manual");
    assert.equal(options.headers.Authorization, "Bearer test-only-token");
    if (url.includes("/runs?")) return Response.json({ workflow_runs: runs });
    if (url.endsWith("/git/ref/heads/main")) return Response.json({ object: { sha } });
    if (url.includes("/contents/")) {
      assert.ok(url.endsWith(`?ref=${sha}`));
      assert.equal(options.headers.Accept, "application/vnd.github.raw+json");
      return Response.json({ meta });
    }
    assert.ok(url.endsWith("/dispatches"));
    assert.equal(options.method, "POST");
    assert.deepEqual(JSON.parse(options.body), { ref: "main", inputs: {run_kind: "full", boundary_utc: at(1)} });
    return new Response(null, { status: dispatchStatus });
  };
  return { fetcher, calls };
}
test("one dispatch to the fixed repository and workflow on stale data", async () => {
  const m = mockGitHub();
  assert.equal((await reconcile(env, { start, now, phase: "initial" }, m.fetcher)).action, "dispatched");
  assert.equal(m.calls.filter((x) => x.options.method === "POST").length, 1);
});
test("fresh snapshot uses immutable commit and makes no POST", async () => {
  const m = mockGitHub({ meta: snapshot(1) });
  assert.equal((await reconcile(env, { start, now, phase: "verify" }, m.fetcher)).action, "fresh");
  assert.equal(m.calls.length, 3);
});
test("disabled deployment never contacts GitHub", async () => {
  const result = await reconcile({ ENABLED: "false" }, {}, () => assert.fail("unexpected HTTP"));
  assert.equal(result.action, "disabled");
});
test("429/5xx and dispatch ambiguity never cause a blind retry", async () => {
  for (const code of [429, 500, 503]) {
    const m = mockGitHub({ dispatchStatus: code });
    await assert.rejects(reconcile(env, { start, now, phase: "initial" }, m.fetcher), new RegExp(`github_http_${code}`));
    assert.equal(m.calls.filter((x) => x.options.method === "POST").length, 1);
  }
});
test("redirects are rejected without following them or starting a collector", async () => {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    assert.equal(options.redirect, "manual");
    return new Response(null, { status: 302, headers: { Location: "https://unexpected.example/" } });
  };
  await assert.rejects(reconcile(env, { start, now, phase: "initial" }, fetcher), /github_http_302/);
  assert.ok(calls.every((c) => c.url.startsWith("https://api.github.com/") && c.options.method === "GET"));
});
test("malformed refs and documents cannot trigger a workflow", async () => {
  await assert.rejects(readState(env, async () => Response.json({})), /invalid_github_schema/);
  const m = mockGitHub({ meta: null });
  await assert.rejects(reconcile(env, { start, now, phase: "initial" }, m.fetcher), /invalid_snapshot_schema/);
});
test("administrative endpoints require a separate secret; public health exposes no credential", async () => {
  for (const path of ["/run", "/check", "/status"]) {
    const response = await worker.fetch(new Request(`https://example.test${path}`, { method: "POST" }), env);
    assert.equal(response.status, 401);
  }
  const response = await worker.fetch(new Request("https://example.test/health"), env);
  const text = await response.text();
  assert.ok(text.includes('"configured":true'));
  assert.ok(!text.includes(env.GITHUB_TOKEN) && !text.includes(env.CONTROL_TOKEN));
});
test("retired triggers are ignored while Cloudflare propagates changes", async () => {
  const result = await worker.scheduled({ cron: "* * * * *", scheduledTime: start }, env);
  assert.equal(result.action, "ignored_retired_cron");
});
test("late cron delivery recovers the current round instead of discarding it", () => {
  const late = start + 10 * 60_000;
  assert.deepEqual(scheduleWindow({ cron: "*/5 * * * *", scheduledTime: start }, late),
    { start, now: late, phase: "verify" });
  assert.equal(choice({ now: late }).action, "dispatch");
  assert.equal(choice({ now: late, snapshot: snapshot(9) }).action, "fresh");
  assert.equal(choice({ now: late, runs: [run("completed"), run("completed", 10)] }).action, "attempt_limit");
});
test("a many-hours-old delivery checks the newest round and keeps the quarter-hour budget", () => {
  const current = start + 4 * 3600000;
  assert.deepEqual(scheduleWindow({ cron: "59,14,29,44 * * * *", scheduledTime: start }, current),
    { start: current, now: current, phase: "initial" });
  assert.equal(scheduleWindow({ cron: "* * * * *", scheduledTime: start }, current), null);
});
test("missing GitHub secret fails before any upstream request", async () => {
  await assert.rejects(readState({}, () => assert.fail("unexpected HTTP")), /missing_github_token/);
});

test("light quarters dispatch explicit cycle inputs and read their own immutable ref", async () => {
  const quarter = Date.parse("2026-09-20T21:14:00Z");
  const requests = [];
  const fetcher = async (url, options) => {
    requests.push(url);
    if (url.includes('/runs?')) return Response.json({workflow_runs: []});
    if (url.endsWith('/git/ref/heads/main')) return Response.json({object: {sha}});
    if (url.includes('/contents/')) {
      assert.ok(url.includes('/data/intraday/latest.json?ref='));
      return new Response(null, {status: 404});
    }
    assert.deepEqual(JSON.parse(options.body), {ref: 'main', inputs: {run_kind: 'light', boundary_utc: '2026-09-20T21:15:00.000Z'}});
    return new Response(null, {status: 204});
  };
  assert.equal((await reconcile(env, {start: quarter, now: quarter, phase: 'initial'}, fetcher)).action, 'dispatched');
  assert.equal(requests.length, 4);
});
