# Emberline: build spec (v1.1)

**The fire map that updates every time a satellite looks.** Emberline is a Cal Hacks 13.0 project. It replays a real fire night through a live-style system:

1. GOES fire detections arrive every 5 minutes.
2. A physics ensemble re-fits itself to each one.
3. The forecast becomes a ranked list of who is in the way and when each zone must act.
4. A human approves the alert, and the duty officer's phone rings.

v1.1, Sat Oct 24 2026 ~06:15 PT. It folds in two independent reviews: a feasibility/consistency review and a coding-agent dry run. Owners: Cody (backend/data/integrations), Abby (physics + metrics), Linda (frontend + Vercel functions).

**How to use this spec**
1. Read this file: decisions, priorities, milestones, cut lines.
2. Then `spec/00_contracts.md`, the shared contracts everyone codes against.
3. Then only your component file. Coding agents start at `AGENTS.md`.
4. The reasons behind each decision are in `research/01–06`.
5. Who builds what next, and the prompts for coding agents: `docs/build_process.md`.

| File | What | Owner |
|---|---|---|
| `spec/00_contracts.md` | Conventions, types, cycle bundle, files and URLs, hub index, HazardModel, alert rules, repo, analytic fixture | Cody (frozen at M0) |
| `spec/01_data_pipeline.md` | Scenario builder; G1-a GOES go/no-go | Cody (Abby: DEM/LANDFIRE/slope) |
| `spec/02_physics.md` | MTT solver, urban spread, ensemble, particle filter, likelihoods, gate | Abby |
| `spec/03_engine.md` | Clock, sources, physics cache, publisher, risk, alerts, branching, local API, metrics | Cody (Abby: `metrics.py`) |
| `spec/04_hub.md` | SpacetimeDB module (tables, reducers, auth, reset), engine HTTP client, web adapter | Cody |
| `spec/05_ai_voice.md` | Claude briefing + validator, report parser, Cartesia call, Grok STT/TTS, Vercel functions | Cody (engine side), Linda (`web/api/*`) |
| `spec/06_frontend.md` | Dashboard, map, slider, alert card, Evaluation card, `/control`, data layer | Linda |
| `spec/07_phone.md` | QR phone page | Linda |
| `spec/08_demo_and_submission.md` | Demo script, honest-number protocol, Q&A, sponsor checklists, Devpost | Everyone |
| `spec/09_extensions.md` | Optional extensions after M3: gates, designs, schema v3 additions, acceptance tests | Per extension |

---

## 1. What judges see (3 minutes; full script in `08` §1)

1. **Hook.** "That night a weather satellite imaged Altadena every 5 minutes. Most spread forecasts are run from a fixed start and aren't re-fit to each new look. Emberline re-fits every time the satellite looks."
2. **Replay at 240×.**
   - A dark 3D terrain map; GOES-18 "looks" every 5 min.
   - The forecast redraws ahead of the fire; the slider scrubs past ↔ +6 h.
   - The right panel ranks zones by act-by time.
3. **Moment 1: the phone rings.**
   - A zone crosses a rule frozen before the replay, and the hub **auto-pauses**.
   - Claude's draft has been validated; the operator approves.
   - Cartesia calls the duty-officer phone (a teammate's, on speaker for the judge). The judge asks a question and says "acknowledged".
   - The dashboard shows "Acknowledged by phone". The judge-resident's phone buzzes and speaks its own alert.
4. **Moment 2: the forecast moves.**
   - A judge holds to talk on their phone: "flames above the end of Lincoln Avenue."
   - Grok transcribes, Claude parses, and the report becomes an observation.
   - The engine forks a branch and re-forecasts; the before/after outline shifts within about 5 s.
5. **Evaluation (post-hoc) + architecture.**
   - Lead time on the west-Altadena foothills, with vs without assimilation.
   - The 03:25 orders as context only.
   - One architecture slide; the roadmap.

## 2. Locked decisions

| # | Decision | Revisit if |
|---|---|---|
| D1 | **Demo fire: Eaton, Jan 7–8 2025, hybrid replay.** Real: satellite, winds, terrain, fuels, zone names, timeline. Simulated (labeled): residents, phones, duty officer. **Backup: Palisades** (same GOES/HRRR files). Last resort: synthetic "TRAINING SCENARIO". | **G1-a 09:30:** `area_x_satellite_hit` finds no GOES-18 hit before 03:25 PST → VIIRS as the honest-number sensor, else Palisades |
| D2 | **Grid:** EPSG:32611, 90 m, 340×340 (30.6 km), upper-left (381480, 3800280). 60 m (510²) only after everything works. | — |
| D3 | **Physics:** custom numba minimum-travel-time solver with elliptical Rothermel spread (pyretechnics fuel table). **Urban NB91 spread is P0.** SIR particle filter with tempering, jitter and re-anchoring. N=128, 6 h horizon, 5-min cycle, branch-independent RNG. | **Sat 18:00 gate:** `pf` vs `reinit`, judged on the twin experiment + the **first 2 h of the fire only** (`02` §8). Ties go to `reinit`. |
| D4 | **Physics outputs precomputed and cached for the whole replay.** Risk, alerts and people are re-derived at publish time from current hub state. Live compute runs only for injected reports (branch from a 30-min checkpoint). | — |
| D5 | **Shared state hub: SpacetimeDB.** Engine via HTTP reducers + SQL polling; identity-based auth; operator PIN. Science data stays in files. **Convex = design-note fallback only.** | **G1-b 09:30:** the spike fails → Convex. Never both. |
| D6 | **Forecast delivery:** one `arrival_q.bin` per cycle (uint8, 5 quantiles, 2-min units) on the native grid with 4 named corners; the client recolors per frame. Phones get danger polygons only. | — |
| D7 | **Alert channel = Cartesia outbound call** after human approval. The default callee is a **teammate's phone** (saved contact, speaker). The in-app alert on phones always works. **No Twilio SMS.** | **G1-c 09:30:** Cartesia can't dial out → "Play briefing here" + in-app alert |
| D8 | **Voice split:** Cartesia = voice out to officials; Grok Voice API (STT + TTS) = voice in/out with the public | The SpaceXSI booth says STT/TTS doesn't count → Grok realtime for the readback |
| D9 | **LLM: Claude.** `claude-sonnet-5-5` briefing (structured outputs; no temperature/forced tool), `claude-haiku-5-5` parser. Claude never computes numbers; a validator + template fallback catches any change. | — |
| D10 | **Hosting:** the laptop runs the engine (outbound only) and the dashboard. Hub on Maincloud (dev on local `spacetime start`). Vercel hosts `/p` + `web/api/*`. Offline: everything local except phones. | Maincloud limits → self-host on Vultr |
| D11 | **Act-by** = q30 arrival − zone evacuation minutes. Tier 1 = hospital or nursing/assisted living, or ≤ 1 egress road → 75 min; otherwise 45. Editable on the dashboard; edits survive replay. | — |
| D12 | **Honesty protocol** (`08` §2): **one** freeze (target Sat 20:00) after tuning on the first 2 h only; the first full run after it is reported (v1); later changes are disclosed as v2. Lead time is reported DA vs open-loop even if negative. Everything is badged; urban spread is labeled heuristic; every alert says "demo replay". | — |

**Skipped on purpose:**
- Tiger Data, Bright Data, ElevenLabs, Snowflake, Fetch.ai, Factory, DigitalOcean, Twilio.
- Grok Imagine: generated imagery would be fabricated data.
- Convex: fallback only.

## 3. Architecture

```mermaid
flowchart LR
  subgraph Files["Files (science data)"]
    SC[data/scenarios/eaton: grid, terrain, fuels, HRRR fxx0-9, GOES/VIIRS pixel tables, zones, exposure, manifest]
    CA[cache/eaton/branch/k: physics.json, zone_arrivals.npz, arrival_q.bin, contours; checkpoints; briefings]
  end
  subgraph Engine["Python engine (demo laptop, outbound only)"]
    CL[Clock] --> SR[Replay sources]
    SR --> PL[Physics loop] <--> HZ[[HazardModel: MTT ensemble + PF (Abby)]]
    PL --> CA
    CA --> PB[Publisher: risk + alerts + people from live hub state]
    PB --> BR[Claude briefing + validator]
    PB --> NT[Cartesia call on approval]
    LOC[Local API :8787: /cache, /static, /dev/report, /dev/duty-officer]
  end
  subgraph Hub["SpacetimeDB: shared operating picture"]
    T[(clock, branch, cycle, cycle_head, zone_risk, alert, briefing, person, spawn_point, report, activity_event, metric, command)]
  end
  subgraph Web["Web (Vite/React/deck.gl)"]
    D[dashboard on laptop] ; P[/p on Vercel] ; C[/control]
    API[web/api: report-voice, report-tap, tts, cartesia/status, cartesia/ack]
  end
  SC --> SR
  PB -- "publish_bundle (HTTP)" --> T
  T -- "SQL poll: commands, clock, zones, people" --> PB
  T -- subscriptions --> D & P & C
  CA -- assets --> D
  P --> API -- "Grok STT -> Claude parse -> submit_report" --> T
  NT --> CART[Cartesia agent] -- webhook tools --> API -- ack_alert --> T
```

**Boundary rule.** All computation lives in Python. The hub runs small state machines and fans out. Vercel functions are thin adapters.

## 4. Priorities

**P0** (the demo does not exist without these):
- Scenario build: grid, DEM, fuels, HRRR, GOES-18 FDCC pixels, zones, exposure, area X
- Fake model, then the MTT ensemble **with urban spread** and at least the `reinit` filter
- Physics cache + publisher + hub
- Risk, act-by, alert proposal, auto-pause, approval, hard reset
- Dashboard: terrain, forecast overlay, slider, zones panel, alert card, activity feed
- Claude briefing with validator + template
- Offline mode

**P1** (the two moments + evaluation):
- PF with tempering (vs reinit)
- Cartesia call + acknowledgement
- Phone page: join, act-by, danger, in-app alert
- Voice report → branch re-forecast, plus the `demo_b1` rehearsal branch and `effect.json`
- VIIRS detections
- Evaluation card (DA vs open-loop)
- GOES scan-beam pulse
- Spotting (first P1 to cut)

**P2** (only after M3 is green): full designs, entry gates, contract additions (schema v3, additive) and acceptance tests are in **`spec/09_extensions.md`**. Everything is behind a feature flag; extension code freezes Sun 04:00. Default order:
1. E1: risk-aware evacuation routes with live reroute on the phone
2. E2: "Ask the operating picture" (Claude agent with read-only tools)
3. E8-A: live "California now" GOES-18 hotspots (backs "usable Monday"), or E7: Palisades evaluation with the same frozen config
4. E3 what-if branches, E4 Grok realtime voice, E5 camera + triangulation, E6 satellite polish (GOES-16 display-only, meso flash, VIIRS orbits)
5. E9 Apify zones/orders Actor; `.tech` domain (10 min, anytime)
6. Tier 3, only from idle hands: E10 Meshtastic off-grid channel, E11 aerial IR + simulated units, E12 post-fire debris flow, E13 physics v2 (60 m, terrain wind; reported as v2 only)

**Never cut:** offline mode, the honesty labels, the template fallback, hard reset.

## 5. Milestones and gates (PT)

| When | Milestone | Done means |
|---|---|---|
| **Sat 08:00** | **M0: skeleton** | Repo up. `contracts.py` / `contracts.ts` + the **analytic fixture** committed (no physics dependency). `FakeHazardModel` merged. Linda's dashboard renders the fixture via `FileSource`. |
| **Sat 09:30** | **G1 gates** | (a) GOES check → Eaton or Palisades; area X box confirmed on a map. (b) SpacetimeDB spike → SpacetimeDB or Convex. (c) Cartesia test call to a teammate → call or in-app only. (d) DEM + LANDFIRE on the grid (Abby). |
| **Sat 13:00** | **M1: end-to-end on fake physics** | Real GOES manifest → physics cache → publisher → hub → dashboard. Zones ranked, alert proposed, auto-pause, approve, hard reset all work. One real Claude briefing call verified. |
| **Sat 15:00** | Urban spread in physics | The fire can reach Altadena zones in real physics |
| **Sat 18:00** | **M2 + gate** | MTT ensemble on Eaton in the loop. **PF vs reinit chosen** from the first 2 h only. Cartesia call fires on approval. |
| **Sat 20:00** | **Freeze** | `freeze.py` run once; v1 precompute of `main` + `openloop` → this is the reported run |
| **Sat 22:00** | **M3: the two moments** | Phone page on Vercel. Voice report → branch in ≤ 10 s. `demo_b1` + `effect.json`. Evaluation card from `metrics.py`. P1 freeze. |
| **Sun 02:00** | **M4** | P2 only if M3 is green. Backup video. README + Devpost draft. |
| **Sun 06:00** | **M5: rehearse** | Reset-twice test; offline + online rehearsals; 3 timed runs; final video |
| **Sun 08:30** | **Submit** | Devpost (hard close 09:00). Judging 10:30–12:30. |

**Sleep.** Stagger about 4 h each; never all three asleep. Suggested: Linda 23:00–03:00, Abby 01:00–05:00, Cody 02:30–06:00.

## 6. Who does what first (after the v1.1 rebalancing)

**Cody**
- Repo + contracts + analytic fixture by 08:00.
- G1-a GOES check (coding agent) and the SpacetimeDB spike by 09:30.
- Cartesia test call.
- Then HRRR + GOES table + zones → engine → hub → M1.
- Owns `ai/briefing.py`, `notify/cartesia.py`, `risk.py`, `alerts.py`.
- Converts `web/` to TypeScript (strict) without changing Linda's look or behaviour; Linda reviews (`docs/build_process.md` C1).

**Abby**
- `FakeHazardModel` by 08:00.
- **Pulls DEM + LANDFIRE + slope/aspect herself** (`01` §6) by 09:30.
- Fuel table → MTT → ensemble → urban spread by 15:00 → PF → 18:00 gate.
- `metrics.py` after the gate.

**Linda**
- Dashboard on the fixture from 08:00, applying her mockup's look.
- **Owns `web/api/*`** (`05` §2–4) and the phone page.
- **09:00 booth run**, 30 min:

| Where | Ask / get |
|---|---|
| SpaceXSI | X Premium codes. Does Grok STT/TTS count as the "Voice API"? xAI credits? |
| Cartesia | Redeem `CARTESIACALHACKS`. Can the free plan dial out? |
| SpacetimeDB | How to enter (not on Devpost) |
| Help desk | Is there a prize cap? |

**Workshops, one person each if possible:** Microsoft 10:00, Anthropic 13:00, SpacetimeDB 16:00.

## 7. Accounts and keys (never commit keys)

| Account / key | Who | For | When |
|---|---|---|---|
| GitHub repo (public at submit) | Cody | all | 06:30 |
| SpacetimeDB login + `set_identities` (engine + Vercel identities, operator PIN) | Cody | hub | 08:30 |
| Vercel | Linda | web + functions | M1 |
| Anthropic API key | Cody (engine), Linda (Vercel) | briefing, parser | M1 |
| Cartesia key + agent (deployed) + 1 number; `Cartesia-Version` | Cody | call | 08:30 test |
| xAI API key | Linda | STT/TTS | M2 |
| NASA FIRMS MAP_KEY (email address) | Cody | VIIRS | 08:00 |
| LANDFIRE LFPS (email address) | Abby | fuels | 08:00 |

`.env.example` lists every variable (`00` §9).

## 8. Open questions and owners

1. Is GOES-18 good enough for Eaton's westward run? (G1-a; Cody)
2. Does Cartesia's free plan dial out? (booth; Linda)
3. Prize cap? SpacetimeDB entry? Does Grok STT/TTS count? (booth; Linda)
4. Pre-fire LA County zone polygons (`01` §6 source order and fallback)
5. **Anthropic's "problem you lived or heard first-hand":** someone talks to a person with lived experience today (mentor tables, `#ask-mentors`, any LA-area or fire/EMS contact) and writes down what they said. **Never invent a source.**
6. Linda's mockup wins on look; `00_contracts` wins on data.
