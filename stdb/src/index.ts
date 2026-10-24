// Emberline hub: G1-b spike module (spec/04 §1). Only config, clock, cycle, command.
// Phase 2 replaces this with the full §2 module.
import { schema, table, t, SenderError, type InferSchema, type ReducerCtx } from 'spacetimedb/server';

// ponytail: identities stored as hex strings (04 §2.2 explicit fallback); compare ctx.sender.toHexString().
const config = table(
  { name: 'config' }, // private
  {
    id: t.string().primaryKey(),
    engine_identity: t.string(),
    service_identity: t.string(),
    operator_pin: t.string(),
  }
);

const clock = table(
  { name: 'clock', public: true },
  {
    scenario_id: t.string().primaryKey(),
    mode: t.string(),
    playing: t.bool(),
    speed: t.f64(),
    anchor_sim_ms: t.f64(),
    anchor_wall_ms: t.f64(),
    active_branch_id: t.string(),
    status: t.string(),
    resume_playing: t.bool(),
    pause_on_proposal: t.bool(),
    epoch: t.u32(),
    min_sim_ms: t.f64(),
    max_sim_ms: t.f64(),
  }
);

const cycle = table(
  { name: 'cycle', public: true },
  {
    cycle_id: t.string().primaryKey(),
    scenario_id: t.string().index('btree'),
    branch_id: t.string(),
    knowledge_sim_ms: t.f64(),
    burned_ha: t.f64(),
  }
);

const command = table(
  { name: 'command', public: true },
  {
    command_id: t.string().primaryKey(),
    scenario_id: t.string(),
    kind: t.string(),
    args_json: t.string(),
    status: t.string().index('btree'),
    created_wall_ms: t.f64(),
    actor: t.string(),
    result_json: t.option(t.string()),
  }
);

const spacetimedb = schema({ config, clock, cycle, command });
export default spacetimedb;

type Ctx = ReducerCtx<InferSchema<typeof spacetimedb>>;
type Clock = NonNullable<ReturnType<Ctx['db']['clock']['scenario_id']['find']>>;

function nowMs(ctx: Ctx): number {
  return Number(ctx.timestamp.microsSinceUnixEpoch / 1000n);
}

function getConfig(ctx: Ctx) {
  const cfg = ctx.db.config.id.find('singleton');
  if (!cfg) throw new SenderError('hub not initialised: call set_identities');
  return cfg;
}

function requireEngine(ctx: Ctx): void {
  if (ctx.sender.toHexString() !== getConfig(ctx).engine_identity) throw new SenderError('engine identity required');
}

function requirePin(ctx: Ctx, pin: string): void {
  if (pin !== getConfig(ctx).operator_pin) throw new SenderError('bad operator pin');
}

function simNow(c: Clock, now: number): number {
  const raw = c.anchor_sim_ms + (c.playing ? (now - c.anchor_wall_ms) * c.speed : 0);
  return Math.min(Math.max(raw, c.min_sim_ms), c.max_sim_ms);
}

function insertCommand(ctx: Ctx, scenario_id: string, kind: string, args: object, actor: string): void {
  const us = ctx.timestamp.microsSinceUnixEpoch;
  let i = 0;
  while (ctx.db.command.command_id.find(`cmd_${us}_${i}`)) i++;
  ctx.db.command.insert({
    command_id: `cmd_${us}_${i}`,
    scenario_id,
    kind,
    args_json: JSON.stringify(args),
    status: 'pending',
    created_wall_ms: nowMs(ctx),
    actor,
    result_json: undefined,
  });
}

export const set_identities = spacetimedb.reducer(
  { engine_identity: t.string(), service_identity: t.string(), operator_pin: t.string() },
  (ctx, { engine_identity, service_identity, operator_pin }) => {
    if (ctx.db.config.id.find('singleton')) throw new SenderError('identities already set');
    if (!operator_pin) throw new SenderError('operator_pin must be non-empty');
    ctx.db.config.insert({
      id: 'singleton',
      engine_identity: engine_identity.toLowerCase().replace(/^0x/, ''),
      service_identity: service_identity.toLowerCase().replace(/^0x/, ''),
      operator_pin,
    });
  }
);

export const clock_play = spacetimedb.reducer(
  { scenario_id: t.string(), pin: t.string() },
  (ctx, { scenario_id, pin }) => {
    requirePin(ctx, pin);
    const now = nowMs(ctx);
    let c = ctx.db.clock.scenario_id.find(scenario_id);
    if (!c) {
      c = ctx.db.clock.insert({
        scenario_id, mode: 'replay', playing: false, speed: 240,
        anchor_sim_ms: 0, anchor_wall_ms: now, active_branch_id: 'main', status: 'ok',
        resume_playing: false, pause_on_proposal: true, epoch: 0,
        min_sim_ms: 0, max_sim_ms: Number.MAX_SAFE_INTEGER,
      });
    }
    if (c.status === 'computing') throw new SenderError('clock is computing');
    ctx.db.clock.scenario_id.update({ ...c, anchor_sim_ms: simNow(c, now), anchor_wall_ms: now, playing: true });
    insertCommand(ctx, scenario_id, 'clock_play', {}, 'operator');
  }
);

export const publish_bundle = spacetimedb.reducer(
  { bundle_json: t.string() },
  (ctx, { bundle_json }) => {
    requireEngine(ctx);
    const b = JSON.parse(bundle_json);
    const cy = b.cycle;
    if (!cy) return;
    const row = {
      cycle_id: String(cy.cycle_id),
      scenario_id: String(cy.scenario_id),
      branch_id: String(cy.branch_id),
      knowledge_sim_ms: Number(cy.knowledge_sim_ms),
      burned_ha: Number(cy.burned_ha),
    };
    if (ctx.db.cycle.cycle_id.find(row.cycle_id)) ctx.db.cycle.cycle_id.update(row);
    else ctx.db.cycle.insert(row);
  }
);
