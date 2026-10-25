import test from "node:test";
import assert from "node:assert/strict";
import { issueSession, verifySession, reportService } from "./reports.mjs";
const secret = "synthetic-session-secret-of-at-least-32-characters";
const env = {
  REPORT_SESSION_SECRET: secret,
  STDB_SCENARIO_ID: "eaton",
  STDB_URI: "http://localhost:3017",
  STDB_DB: "test",
  STDB_SERVICE_TOKEN: "synthetic",
};
const now = () => 100000;
const session = issueSession("per_12345678", "eaton", now(), secret);
const body = {
  request_id: "request1",
  kind: "fire",
  lon: -118.1,
  lat: 34.2,
  observed_sim_ms: 1000,
};
const req = (value) => ({
  method: "POST",
  headers: { authorization: `Bearer ${session}` },
  body: value,
});
function result(rows) {
  const keys = Object.keys(rows[0] ?? {});
  return Response.json([
    {
      schema: {
        elements: keys.map((name) => ({
          name: { some: name },
          algebraic_type: { String: [] },
        })),
      },
      rows: rows.map((r) => keys.map((k) => r[k])),
    },
  ]);
}
function fake(reports = []) {
  const calls = [];
  const fetcher = async (url, options) => {
    if (url.includes("/call/")) {
      calls.push(JSON.parse(options.body));
      return new Response("");
    }
    if (options.body.includes("FROM person"))
      return result([{ scenario_id: "eaton" }]);
    if (options.body.includes("FROM clock"))
      return result([
        {
          min_sim_ms: 0,
          max_sim_ms: 2000,
          anchor_sim_ms: 1000,
          anchor_wall_ms: 100000,
          playing: false,
          active_branch_id: "b1",
        },
      ]);
    assert.match(options.body, /WHERE scenario_id = 'eaton'/);
    return result(reports.map((r) => ({ person_id: "per_12345678", ...r })));
  };
  return { fetcher, calls };
}

test("signed session binds identity/scenario and expires", () => {
  assert.equal(verifySession(session, 100001, secret).person, "per_12345678");
  assert.equal(verifySession(session + "x", 100001, secret), null);
  assert.equal(verifySession(session, 8000000, secret), null);
  assert.equal(verifySession(session, 100001, "wrong"), null);
});
test("join returns scoped session without service credential", async () => {
  const f = fake();
  const r = await reportService(
    "join",
    req({ join_code: "demo", label: "Test" }),
    { env, now, fetcher: f.fetcher, uuid: () => "12345678-0000" },
  );
  assert.equal(r.status, 200);
  assert.equal(r.body.person_id, "per_12345678");
  assert.equal(
    verifySession(r.body.report_session, now(), secret).scenario,
    "eaton",
  );
  assert.equal(f.calls[0][0], "eaton");
  assert.ok(!JSON.stringify(r).includes("STDB_SERVICE_TOKEN"));
});
test("tap receives server-bound person, active branch and fixed citizen trust", async () => {
  const f = fake();
  const r = await reportService("tap", req(body), {
    env,
    now,
    fetcher: f.fetcher,
  });
  assert.equal(r.status, 200);
  const report = JSON.parse(f.calls[0][0]);
  assert.equal(report.person_id, "per_12345678");
  assert.equal(report.branch_id, "b1");
  assert.equal(report.trust, 0.5);
});
test("missing session cannot submit a report", async () => {
  const f = fake();
  assert.equal(
    (
      await reportService(
        "tap",
        { ...req(body), headers: {} },
        { env, now, fetcher: f.fetcher },
      )
    ).status,
    401,
  );
  assert.equal(f.calls.length, 0);
});
test("future time and trust overrides are rejected", async () => {
  for (const value of [
    { ...body, observed_sim_ms: 1001 },
    { ...body, trust: 1 },
  ]) {
    const f = fake();
    assert.equal(
      (await reportService("tap", req(value), { env, now, fetcher: f.fetcher }))
        .status,
      400,
    );
    assert.equal(f.calls.length, 0);
  }
});
test("rate limit uses persisted report wall times", async () => {
  const f = fake([{ report_id: "other", created_wall_ms: 99999 }]);
  assert.equal(
    (await reportService("tap", req(body), { env, now, fetcher: f.fetcher }))
      .status,
    429,
  );
});
