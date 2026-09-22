// Cloudflare starts/checks GitHub Actions; all market calculations stay in Python.
export const REPOSITORY = "exolinodev/dot-market-monitor";
export const WORKFLOW = "market-data.yml";
export const CRONS = ["58,13,28,43 * * * *", "*/5 * * * *"];
const ACCEPTED_CRONS = new Set(CRONS);
const API = `https://api.github.com/repos/${REPOSITORY}`;
const MINUTE = 60_000;
const QUARTER = 15 * MINUTE;
const PREWARM = 2 * MINUTE;
const ACTIVE = new Set(["queued", "in_progress", "waiting", "pending", "requested"]);
const WAITING = new Set(["queued", "waiting", "pending", "requested"]);
const WAIT_LIMIT = 30 * MINUTE;
const iso = (time) => new Date(time).toISOString();

export function cycleStart(time) {
  return Math.floor((time + PREWARM) / QUARTER) * QUARTER - PREWARM;
}

export function scheduleWindow(controller, now) {
  if (!ACCEPTED_CRONS.has(controller.cron)) return null;
  // Always reconcile the current round. A late event must help restore data,
  // including after midnight, rather than discard the whole recovery opportunity.
  const start = cycleStart(now);
  return { start, now, phase: now < start + 5 * MINUTE ? "initial" : "verify" };
}

export function decide({ runs, snapshot, start, now, phase }) {
  if (!["initial", "verify"].includes(phase)) throw new Error("invalid_phase");
  // Fail closed on unknown API states, instead of creating duplicate runs.
  if (!Array.isArray(runs) || runs.some((r) => !Number.isFinite(Date.parse(r.created_at)) ||
      !(ACTIVE.has(r.status) || r.status === "completed"))) throw new Error("invalid_run_schema");
  const generated = Date.parse(snapshot?.generated_at_utc);
  const boundary = start + PREWARM;
  const run_kind = new Date(boundary).getUTCMinutes() === 0 ? "full" : "light";
  const fresh = Date.parse(snapshot?.cycle_boundary_utc) === boundary && snapshot?.run_kind === run_kind &&
    Number.isFinite(generated) && generated >= boundary && generated <= now + MINUTE &&
    snapshot?.fresh === true && ["ok", "partial"].includes(snapshot?.status);
  // GitHub can retain phantom queued runs whose cancellation endpoint reports
  // already completed. A stale waiting record must not disable every new cycle.
  // Running jobs always block; workflow concurrency still serializes collectors.
  const staleWaiting = (r) => WAITING.has(r.status) &&
    now - Math.max(Date.parse(r.created_at), Date.parse(r.updated_at) || 0) > WAIT_LIMIT;
  const active = runs.find((r) => ACTIVE.has(r.status) && !staleWaiting(r));
  const attempts = runs.filter((r) => Date.parse(r.created_at) >= start).length;
  const detail = {
    run_kind, cycle_boundary_utc: iso(boundary), cycle_started_at_utc: iso(start), checked_at_utc: iso(now), phase, attempts,
    snapshot_generated_at_utc: Number.isFinite(generated) ? iso(generated) : null,
    snapshot_status: snapshot?.status ?? null, fresh,
    stale_waiting_run_ids: runs.filter(staleWaiting).map((r) => r.id),
  };
  if (fresh) return { ...detail, action: "fresh", run_id: active?.id ?? null };
  if (active) return { ...detail, action: "already_running", run_id: active.id };
  // At most one initial dispatch, and one recovery attempt per quarter-hour round.
  if (attempts >= (phase === "initial" ? 1 : 2)) return { ...detail, action: "attempt_limit" };
  return { ...detail, action: "dispatch" };
}

async function github(env, path, fetcher, { raw = false, method = "GET", body, allowMissing = false } = {}) {
  const response = await fetcher(`${API}${path}`, {
    // Workers supports only follow/manual. Manual + !ok rejects redirects without forwarding credentials.
    method, redirect: "manual", signal: AbortSignal.timeout(15_000),
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: raw ? "application/vnd.github.raw+json" : "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "dot-market-scheduler",
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  // Never retry a POST blindly: the start may have succeeded before a timeout.
  if (allowMissing && response.status === 404) return null;
  if (!response.ok) throw new Error(`github_http_${response.status}`);
  if (method === "POST") {
    if (response.status !== 204) throw new Error("unexpected_dispatch_response");
    return null;
  }
  return response.json();
}

export async function readState(env, fetcher = fetch, start = cycleStart(Date.now())) {
  if (!env.GITHUB_TOKEN) throw new Error("missing_github_token");
  const [runResponse, ref] = await Promise.all([
    github(env, `/actions/workflows/${WORKFLOW}/runs?branch=main&per_page=20`, fetcher),
    github(env, "/git/ref/heads/main", fetcher),
  ]);
  if (!Array.isArray(runResponse.workflow_runs) || !/^[a-f0-9]{40}$/.test(ref.object?.sha ?? "")) {
    throw new Error("invalid_github_schema");
  }
  // Pin the file to the current commit: avoid stale raw.githubusercontent.com/main responses.
  const light = new Date(start + PREWARM).getUTCMinutes() !== 0;
  const path = light ? "data/intraday/latest.json" : "data/llm_snapshot.json";
  const document = await github(env, `/contents/${path}?ref=${ref.object.sha}`, fetcher, { raw: true, allowMissing: light });
  if (light && document === null) return { runs: runResponse.workflow_runs, snapshot: {}, commit: ref.object.sha };
  if (!document?.meta || typeof document.meta !== "object") throw new Error("invalid_snapshot_schema");
  return { runs: runResponse.workflow_runs, snapshot: document.meta, commit: ref.object.sha };
}

export async function reconcile(env, { start, now, phase }, fetcher = fetch) {
  if (env.ENABLED !== "true") return { action: "disabled" };
  if (!env.GITHUB_TOKEN) throw new Error("missing_github_token");
  const state = await readState(env, fetcher, start);
  const result = { ...decide({ ...state, start, now, phase }), commit: state.commit };
  if (result.action === "dispatch") {
    await github(env, `/actions/workflows/${WORKFLOW}/dispatches`, fetcher, {
      method: "POST", body: { ref: "main", inputs: {run_kind: result.run_kind, boundary_utc: result.cycle_boundary_utc} },
    });
    result.action = "dispatched";
  }
  console.log(JSON.stringify({ service: "dot-market-scheduler", ...result }));
  if (result.action === "attempt_limit") console.error("Snapshot missing; quarter-hour dispatch budget exhausted");
  return result;
}

const json = (value, status = 200) => Response.json(value, {
  status, headers: { "Cache-Control": "no-store" },
});

export default {
  async scheduled(controller, env) {
    const window = scheduleWindow(controller, Date.now());
    // Removed triggers can still arrive while Cloudflare propagates a deployment.
    if (!window) {
      console.log(JSON.stringify({ service: "dot-market-scheduler", action: "ignored_retired_cron" }));
      return { action: "ignored_retired_cron" };
    }
    return reconcile(env, window);
  },
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (request.method === "GET" && path === "/health") {
      return json({ service: "dot-market-scheduler", enabled: env.ENABLED === "true",
        configured: Boolean(env.GITHUB_TOKEN && env.CONTROL_TOKEN), crons_utc: CRONS, cycle_boundary_utc: iso(cycleStart(Date.now()) + PREWARM), repository: REPOSITORY });
    }
    if (!env.CONTROL_TOKEN || request.headers.get("Authorization") !== `Bearer ${env.CONTROL_TOKEN}`) {
      return json({ error: "unauthorized" }, 401);
    }
    try {
      if (request.method === "GET" && path === "/status") {
        const now = Date.now();
        const state = await readState(env, fetch, cycleStart(now));
        return json({ ...decide({ ...state, start: cycleStart(now), now, phase: "verify" }), commit: state.commit });
      }
      if (request.method === "POST" && ["/run", "/check"].includes(path)) {
        const now = Date.now();
        return json(await reconcile(env, { now, start: cycleStart(now),
          phase: path === "/run" ? "initial" : "verify" }));
      }
      return json({ error: "not_found" }, 404);
    } catch (error) {
      // Only our fixed error codes may leave the worker, never response bodies or secrets.
      const code = /^(github_http_\d{3}|invalid_[a-z_]+|missing_github_token|unexpected_dispatch_response)$/.test(error.message)
        ? error.message : "upstream_request_failed";
      console.error(JSON.stringify({ service: "dot-market-scheduler", error: code }));
      return json({ error: code }, 502);
    }
  },
};
