/** Shared-secret authenticated agent tools. No provider or outbound-call invocation. */
import { timingSafeEqual } from "node:crypto";
import { hubClient } from "./hub.mjs";

export async function agentTool(
  action,
  req,
  { env = process.env, fetcher = fetch } = {},
) {
  if (req.method !== "POST")
    return { status: 405, body: { error: "POST required" } };
  const expected = env.CARTESIA_WEBHOOK_SECRET;
  const actual = req.headers?.["x-cartesia-webhook-secret"];
  if (!expected || typeof actual !== "string" || actual.length > 1024)
    return { status: 401, body: { error: "Unauthorized" } };
  const a = Buffer.from(actual),
    b = Buffer.from(expected);
  if (a.length !== b.length || !timingSafeEqual(a, b))
    return { status: 401, body: { error: "Unauthorized" } };
  const body = req.body;
  if (
    !body ||
    typeof body !== "object" ||
    Array.isArray(body) ||
    typeof body.alert_id !== "string" ||
    !/^al_[A-Za-z0-9_-]{1,150}$/.test(body.alert_id) ||
    Object.keys(body).some((k) => k !== "alert_id")
  ) {
    return { status: 400, body: { error: "Invalid alert request" } };
  }
  try {
    const hub = hubClient(env, fetcher);
    const alerts = await hub.sql(
      `SELECT * FROM alert WHERE alert_id = '${body.alert_id}'`,
    );
    if (alerts.length !== 1)
      return { status: 404, body: { error: "Unknown alert" } };
    const alert = alerts[0];
    if (
      !["approved", "calling", "called", "acknowledged"].includes(alert.state)
    ) {
      return { status: 409, body: { error: "Alert is not approved" } };
    }
    if (action === "ack") {
      if (alert.state !== "acknowledged")
        await hub.call("ack_alert", [alert.alert_id, "phone"]);
      return { status: 200, body: { ok: true } };
    }
    if (action !== "status")
      return { status: 400, body: { error: "Unknown tool" } };
    // Validate hub identifiers before building any additional SQL.
    if (
      ![alert.scenario_id, alert.branch_id].every(
        (x) => typeof x === "string" && /^[A-Za-z0-9_-]+$/.test(x),
      )
    ) {
      throw new Error("Invalid hub row");
    }
    const zones = JSON.parse(alert.zone_ids_json);
    if (!Array.isArray(zones) || !zones.every((x) => typeof x === "string"))
      throw new Error("Invalid zones");
    const risk = await hub.sql(
      `SELECT * FROM zone_risk WHERE scenario_id = '${alert.scenario_id}' AND branch_id = '${alert.branch_id}'`,
    );
    const response = {
      alert_id: alert.alert_id,
      state: alert.state,
      text: alert.text,
      notice: "Demo replay; current published values, not a delivery receipt",
      zones: risk
        .filter((x) => zones.includes(x.zone_id))
        .map((x) => ({
          zone_id: x.zone_id,
          knowledge_sim_ms: x.knowledge_sim_ms,
          p_by_lead: JSON.parse(x.p_by_lead_json),
          eta_q50_sim_ms: x.eta_q50_sim_ms,
          act_by_sim_ms: x.act_by_sim_ms,
        })),
    };
    if (Buffer.byteLength(JSON.stringify(response)) > 16000)
      throw new Error("Response too large");
    return { status: 200, body: response };
  } catch {
    return { status: 503, body: { error: "Service temporarily unavailable" } };
  }
}

export function handler(action) {
  return async (req, res) => {
    const result = await agentTool(action, req);
    res.setHeader("Cache-Control", "no-store");
    return res.status(result.status).json(result.body);
  };
}
