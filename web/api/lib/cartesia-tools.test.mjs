import test from "node:test";
import assert from "node:assert/strict";
import { agentTool } from "./cartesia-tools.mjs";
import { decodeRows } from "./hub.mjs";
const env = {
  CARTESIA_WEBHOOK_SECRET: "synthetic-secret",
  STDB_URI: "http://localhost:3017",
  STDB_DB: "test",
  STDB_SERVICE_TOKEN: "synthetic-token",
};
const request = (body = { alert_id: "al_test" }) => ({
  method: "POST",
  headers: { "x-cartesia-webhook-secret": env.CARTESIA_WEBHOOK_SECRET },
  body,
});
function sqlResult(rows) {
  const keys = Object.keys(rows[0] ?? {});
  return [
    {
      schema: {
        elements: keys.map((k) => ({
          name: { some: k },
          algebraic_type: { String: [] },
        })),
      },
      rows: rows.map((row) => keys.map((k) => row[k])),
    },
  ];
}
const alert = {
  alert_id: "al_test",
  scenario_id: "eaton",
  branch_id: "main",
  state: "approved",
  zone_ids_json: '["z1"]',
  text: "DEMO REPLAY",
};

test("unauthorized callback cannot reach hub", async () => {
  let calls = 0;
  const r = await agentTool(
    "ack",
    { ...request(), headers: {} },
    {
      env,
      fetcher: async () => {
        calls++;
        throw Error();
      },
    },
  );
  assert.equal(r.status, 401);
  assert.equal(calls, 0);
});
test("malformed and SQL-injection identifiers are rejected", async () => {
  for (const body of [
    { alert_id: "al_x' OR 1=1" },
    { alert_id: "al_x", token: "bad" },
    null,
  ]) {
    assert.equal((await agentTool("ack", request(body), { env })).status, 400);
  }
});
test("ack submits service reducer; duplicate acknowledged alert is a no-op", async () => {
  for (const state of ["approved", "acknowledged"]) {
    const calls = [];
    const fetcher = async (url, options) => {
      calls.push([url, options]);
      return url.endsWith("/sql")
        ? Response.json(sqlResult([{ ...alert, state }]))
        : new Response("");
    };
    assert.equal(
      (await agentTool("ack", request(), { env, fetcher })).status,
      200,
    );
    assert.equal(
      calls.filter(([url]) => url.includes("/call/")).length,
      state === "approved" ? 1 : 0,
    );
    assert.ok(
      calls.every(
        ([, options]) =>
          options.headers.Authorization === "Bearer synthetic-token",
      ),
    );
  }
});
test("unapproved alert cannot be acknowledged", async () => {
  const fetcher = async () =>
    Response.json(sqlResult([{ ...alert, state: "proposed" }]));
  assert.equal(
    (await agentTool("ack", request(), { env, fetcher })).status,
    409,
  );
});
test("hub failure does not expose credentials or provider errors", async () => {
  const fetcher = async () => {
    throw Error("secret token");
  };
  const r = await agentTool("status", request(), { env, fetcher });
  assert.equal(r.status, 503);
  assert.ok(!JSON.stringify(r).includes("secret token"));
});
test("status returns only alert zones with published probabilities", async () => {
  const fetcher = async (_url, options) =>
    Response.json(
      sqlResult(
        options.body.includes("FROM alert")
          ? [alert]
          : [
              {
                zone_id: "z1",
                knowledge_sim_ms: 1,
                p_by_lead_json: "[0.2,0.3]",
                eta_q50_sim_ms: null,
                act_by_sim_ms: null,
              },
              { zone_id: "other", p_by_lead_json: "[1]" },
            ],
      ),
    );
  const r = await agentTool("status", request(), { env, fetcher });
  assert.equal(r.status, 200);
  assert.equal(r.body.zones.length, 1);
  assert.deepEqual(r.body.zones[0].p_by_lead, [0.2, 0.3]);
});
test("hub option fields decode the pinned wire format", () => {
  const field = {
    name: { some: "value" },
    algebraic_type: {
      Sum: {
        variants: [{ name: { some: "some" } }, { name: { some: "none" } }],
      },
    },
  };
  assert.deepEqual(
    decodeRows({ schema: { elements: [field] }, rows: [[[0, 12]], [[1, []]]] }),
    [{ value: 12 }, { value: null }],
  );
});
