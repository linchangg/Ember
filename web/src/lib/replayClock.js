import { simNow } from '../contracts'

// Replay clock (spec/00 section 3.2). One interface, two backends:
//   hub   - SpacetimeDB `clock` row, when VITE_STDB_URI is set and reachable
//   local - the same anchor math in the browser (spec/06 "FileSource"), the offline fallback
// Components only see ClockState and these methods.

const readPin = () => {
  const saved = import.meta.env.VITE_OPERATOR_PIN || localStorage.getItem('emberline_pin')
  if (saved) return saved
  const typed = window.prompt('Operator PIN')
  if (typed) localStorage.setItem('emberline_pin', typed)
  return typed ?? ''
}

const fromRow = (r) => ({
  scenario_id: r.scenarioId,
  mode: r.mode,
  playing: r.playing,
  speed: r.speed,
  anchor_sim_ms: r.anchorSimMs,
  anchor_wall_ms: r.anchorWallMs,
  active_branch_id: r.activeBranchId,
  status: r.status,
  resume_playing: r.resumePlaying,
  pause_on_proposal: r.pauseOnProposal,
  epoch: r.epoch,
  min_sim_ms: r.minSimMs,
  max_sim_ms: r.maxSimMs,
})

export function createClock(manifest) {
  const scn = manifest.scenario_id
  let state = {
    scenario_id: scn,
    mode: 'replay',
    playing: false,
    speed: 240,
    // local clock opens 90 min after ignition so the fire is already on screen; Restart rewinds to replay start
    anchor_sim_ms: Math.min(manifest.replay_end_sim_ms, manifest.t_ignition_sim_ms + 90 * 60000),
    anchor_wall_ms: Date.now(),
    active_branch_id: 'main',
    status: 'ok',
    resume_playing: false,
    pause_on_proposal: true,
    epoch: 0,
    min_sim_ms: manifest.replay_start_sim_ms,
    max_sim_ms: manifest.replay_end_sim_ms,
  }
  let hub = null
  const clock = {
    kind: 'local',
    note: '',
    get: () => state,
    now: () => simNow(state),
    // every change re-anchors first, as the hub does
    reanchor(patch) {
      const now = simNow(state)
      state = { ...state, anchor_sim_ms: now, anchor_wall_ms: Date.now(), ...patch }
    },
    play() {
      if (hub) return hub.reducers.clockPlay({ scenarioId: scn, pin: readPin() })
      clock.reanchor({ playing: true })
    },
    pause() {
      if (hub) return hub.reducers.clockPause({ scenarioId: scn, pin: readPin() })
      clock.reanchor({ playing: false })
    },
    seek(simMs) {
      if (hub) return hub.reducers.clockSeek({ scenarioId: scn, simMs, pin: readPin() })
      state = { ...state, anchor_sim_ms: Math.min(state.max_sim_ms, Math.max(state.min_sim_ms, simMs)), anchor_wall_ms: Date.now(), epoch: state.epoch + 1 }
    },
    setSpeed(speed) {
      if (hub) return hub.reducers.clockSetSpeed({ scenarioId: scn, speed, pin: readPin() })
      clock.reanchor({ speed })
    },
  }

  const uri = import.meta.env.VITE_STDB_URI
  if (uri) {
    import('../module_bindings')
      .then(({ DbConnection }) => {
        const adopt = (row) => {
          if (row.scenarioId === scn) state = fromRow(row)
        }
        DbConnection.builder()
          .withUri(uri)
          .withDatabaseName('emberline')
          .withToken(localStorage.getItem('emberline_stdb_token') || undefined)
          .onConnect((c, _identity, token) => {
            if (token) localStorage.setItem('emberline_stdb_token', token)
            c.db.clock.onInsert((_ctx, row) => adopt(row))
            c.db.clock.onUpdate((_ctx, _old, row) => adopt(row))
            c.subscriptionBuilder()
              .onApplied(() => {
                for (const row of c.db.clock.iter()) adopt(row)
                hub = c
                clock.kind = 'hub'
              })
              .subscribe(`SELECT * FROM clock WHERE scenario_id = '${scn}'`)
          })
          .onConnectError((_ctx, err) => {
            clock.note = `hub unreachable (${err?.message ?? err}); using local clock`
          })
          .build()
      })
      .catch((err) => {
        clock.note = `hub client failed to load (${err?.message ?? err}); using local clock`
      })
  }
  return clock
}
