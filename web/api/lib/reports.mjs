/** Server-issued report sessions bind one person/scenario without changing hub tables. */
import { createHmac, randomUUID, timingSafeEqual } from "node:crypto";
import { hubClient } from "./hub.mjs";
const identifier = (value) =>
  typeof value === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(value);
const clockNow = (c, wall) =>
  Math.round(
    Math.min(
      c.max_sim_ms,
      Math.max(
        c.min_sim_ms,
        c.anchor_sim_ms + (c.playing ? (wall - c.anchor_wall_ms) * c.speed : 0),
      ),
    ),
  );
function signature(payload, secret) {
  return createHmac("sha256", secret).update(payload).digest("base64url");
}
export function issueSession(person, scenario, now, secret) {
  const payload = Buffer.from(
    JSON.stringify({ person, scenario, expires: now + 7200000 }),
  ).toString("base64url");
  return `${payload}.${signature(payload, secret)}`;
}
export function verifySession(token, now, secret) {
  if (typeof token !== "string" || token.length > 2048) return null;
  const [payload, sig, extra] = token.split(".");
  if (!payload || !sig || extra) return null;
  const actual = Buffer.from(sig),
    expected = Buffer.from(signature(payload, secret));
  if (actual.length !== expected.length || !timingSafeEqual(actual, expected))
    return null;
  try {
    const data = JSON.parse(Buffer.from(payload, "base64url").toString());
    return identifier(data.person) &&
      identifier(data.scenario) &&
      Number.isSafeInteger(data.expires) &&
      data.expires > now
      ? data
      : null;
  } catch {
    return null;
  }
}

export async function reportService(
  action,
  req,
  {
    env = process.env,
    fetcher = fetch,
    now = Date.now,
    uuid = randomUUID,
    channel = "tap",
    transcript = "",
  } = {},
) {
  if (req.method !== "POST")
    return { status: 405, body: { error: "POST required" } };
  const secret = env.REPORT_SESSION_SECRET;
  if (!secret || secret.length < 32 || !identifier(env.STDB_SCENARIO_ID))
    return { status: 503, body: { error: "Report service unavailable" } };
  const body = req.body,
    wall = now();
  if (
    !body ||
    typeof body !== "object" ||
    Array.isArray(body) ||
    Buffer.byteLength(JSON.stringify(body)) > 4096
  ) {
    return { status: 400, body: { error: "Invalid request" } };
  }
  try {
    const hub = hubClient(env, fetcher);
    if (action === "join") {
      if (
        !identifier(body.join_code) ||
        typeof body.label !== "string" ||
        body.label.length > 40
      ) {
        return { status: 400, body: { error: "Invalid join request" } };
      }
      const person = `per_${uuid().replaceAll("-", "").slice(0, 8)}`;
      await hub.call("join_as_person", [
        env.STDB_SCENARIO_ID,
        person,
        body.join_code,
        body.label,
      ]);
      return {
        status: 200,
        body: {
          person_id: person,
          scenario_id: env.STDB_SCENARIO_ID,
          report_session: issueSession(
            person,
            env.STDB_SCENARIO_ID,
            wall,
            secret,
          ),
          expires_wall_ms: wall + 7200000,
          voice_enabled:
            env.VOICE_REPORT_ENABLED === "true" &&
            Boolean(env.XAI_API_KEY && env.ANTHROPIC_API_KEY),
        },
      };
    }
    if (action !== "tap")
      return { status: 400, body: { error: "Unknown report action" } };
    const auth = req.headers?.authorization;
    const session = verifySession(
      typeof auth === "string" && auth.startsWith("Bearer ")
        ? auth.slice(7)
        : null,
      wall,
      secret,
    );
    if (!session || session.scenario !== env.STDB_SCENARIO_ID)
      return { status: 401, body: { error: "Report session required" } };
    if (
      !identifier(body.request_id) ||
      !["fire", "smoke", "embers", "all_clear"].includes(body.kind) ||
      !Number.isFinite(body.lon) ||
      Math.abs(body.lon) > 180 ||
      !Number.isFinite(body.lat) ||
      Math.abs(body.lat) > 90 ||
      !Number.isSafeInteger(body.observed_sim_ms) ||
      Object.keys(body).some(
        (k) =>
          !["request_id", "kind", "lon", "lat", "observed_sim_ms"].includes(k),
      )
    ) {
      return { status: 400, body: { error: "Invalid report" } };
    }
    const people = await hub.sql(
      `SELECT * FROM person WHERE person_id = '${session.person}'`,
    );
    const clocks = await hub.sql(
      `SELECT * FROM clock WHERE scenario_id = '${session.scenario}'`,
    );
    if (
      people.length !== 1 ||
      people[0].scenario_id !== session.scenario ||
      clocks.length !== 1
    ) {
      return { status: 409, body: { error: "Session no longer active" } };
    }
    const current = clockNow(clocks[0], wall);
    if (
      body.observed_sim_ms > current ||
      body.observed_sim_ms < clocks[0].min_sim_ms
    ) {
      return {
        status: 400,
        body: { error: "Report time outside replay knowledge" },
      };
    }
    const reportId = `rep_${createHmac("sha256", secret).update(`${session.person}:${body.request_id}`).digest("hex").slice(0, 8)}`;
    // person_id is an Option<String> on the pinned hub SQL wire. Comparing it
    // with a bare string is rejected; query the scalar scenario key, then scope.
    const reports = (
      await hub.sql(
        `SELECT * FROM report WHERE scenario_id = '${session.scenario}'`,
      )
    ).filter((r) => r.person_id === session.person);
    const existing = reports.find((r) => r.report_id === reportId);
    if (existing) {
      const same = ["kind", "lon", "lat", "observed_sim_ms"].every(
        (k) => existing[k] === body[k],
      );
      return same
        ? {
            status: 200,
            body: { ok: true, report_id: reportId, duplicate: true },
          }
        : {
            status: 409,
            body: { error: "Request ID already has different evidence" },
          };
    }
    if (reports.some((r) => wall - r.created_wall_ms < 20000))
      return {
        status: 429,
        body: { error: "Please wait before reporting again" },
      };
    const report = {
      report_id: reportId,
      scenario_id: session.scenario,
      branch_id: clocks[0].active_branch_id,
      person_id: session.person,
      channel,
      kind: body.kind,
      summary: `Tap report: ${body.kind}`,
      lon: body.lon,
      lat: body.lat,
      radius_m: 250,
      trust: 0.5,
      observed_sim_ms: body.observed_sim_ms,
      created_wall_ms: wall,
      provenance: "injected",
    };
    await hub.call("submit_report", [JSON.stringify(report), transcript]);
    return {
      status: 200,
      body: {
        ok: true,
        report_id: reportId,
        readback: `Received your ${body.kind} report.`,
      },
    };
  } catch {
    return {
      status: 503,
      body: { error: "Report service temporarily unavailable" },
    };
  }
}
export function reportHandler(action) {
  return async (req, res) => {
    const result = await reportService(action, req);
    res.setHeader("Cache-Control", "no-store");
    return res.status(result.status).json(result.body);
  };
}
