# 08: Demo, pitch, honest number, sponsors, submission (everyone)

## 1. Demo script (3:00; rehearse ×3 at M5)

**Roles**
- **Presenter:** Cody. Talks and drives the dashboard.
- **Operator:** Abby. Holds the `/control` tablet, approves alerts, runs the fallbacks.
- **Wrangler:** Linda. Gives a judge the QR code, puts the duty-officer phone (a teammate's, on speaker) in front of the judges, and watches the phones.

**Start state** (set before judges arrive):
- Clock paused at 21:55 PST.
- `pause_on_proposal` on.
- Dashboard pitched at the west-Altadena foothills.
- QR code up on `/control`.
- The judge's phone may already be joined during the intro.

| t | Beat | Clock | Who / what | Fallback |
|---|---|---|---|---|
| 0:00 | "Nineteen people died in the Eaton Fire, eighteen west of Lake Avenue. That night a weather satellite imaged Altadena every 5 minutes. Most spread forecasts are run from a fixed start and aren't continuously re-fit to new observations. **Emberline re-fits to every satellite look.**" | paused | Presenter | — |
| 0:20 | Operator presses **Play (240×)**. GOES pulses every 5 min; the forecast redraws. The presenter drags the slider into the future ("where it can be in two hours") and points at Lake Ave. | 240× | Presenter / operator | Screenshot slide |
| 0:40 | The judge's phone shows **Act by** and a countdown. "You live here now." | 240× | Wrangler | `/control` view |
| ~0:50 | A zone crosses the frozen rule. **The hub auto-pauses**; the top bar shows "Paused for operator review" and the alert card opens. "Claude drafted this; a validator checked every number against the forecast. A human approves." Operator holds **Approve & call**. | **auto-paused** | Operator | Template text is fine |
| 1:00 | **The duty-officer phone rings** (teammate's phone, speaker, judge listening). The Cartesia agent reads the briefing; the judge asks "how sure are you?" and then says "acknowledged". The dashboard flips to **Acknowledged by phone (23:14 replay time)**. The resident judge's phone buzzes and speaks its alert. | paused | Judge | "Play briefing here" + in-app alert |
| 1:40 | Operator resumes at **60×** for ~10 s, then **pauses**. | 60× → paused | Operator | — |
| 1:50 | **Moment 2.** "You see flames up the street. Hold the button and say it." Grok transcribes, the readback plays, the branch forks, and the forecast visibly shifts (the before/after outline). | paused (`computing` then resumes paused) | Judge / presenter | `/control` tap report → scripted `demo_b1` (labeled "Cached rehearsal branch") |
| 2:25 | Operator toggles the **Evaluation** card. "Computed after the full replay, with settings frozen beforehand: on these foothills our forecast first gave ≥50% for fire by the time it was actually seen at T_fc. The satellite saw it at T_sat. That's the lead time, and here it is without assimilation. Evacuation orders went out at 03:25. That's context, not a claim." | paused | Presenter | — |
| 2:45 | One slide: sensors → filter → physics ensemble → risk → human-approved call. "Live mode is the same code pointed at today's satellite. Next: more sensors, then floods." | — | Presenter | — |

**Before the next group:** operator presses **Reset (hard)** (`04` §2.2), clearing alerts, people, reports and branches, then seeks to 21:55. **Test this reset between rehearsals.**

## 2. The honest number: protocol and rules

**Protocol** (written once; `02` §8, `03` §5):
1. Choose `pf` vs `reinit` and tune priors using the twin experiment and **the first 2 h of the fire (18:15–20:15 PST) only**. Area X results are never used to choose or tune.
2. Freeze the area X box, alert rules, act-by quantile, priors and model version with `scripts/freeze.py`. Target Sat 20:00. The hash shows on screen.
3. The **first full `main` + `openloop` run after the freeze is reported (v1)**. Any later change → v2 is published **alongside** v1 and disclosed.

**Definitions** (`03` §9):
- `t_sat`: the first satellite detection in area X. For GOES-18, a `GOES_STRONG` pixel with ≥ 50% of its area in area X, wholly west of Lake Ave, in 2 of 3 scans. VIIRS is the fallback.
- `t_fc`: the earliest forecast that gave P(fire in area X by `t_sat`) ≥ 0.5. It must have predicted the right **time**, not just "within 6 h".
- **Lead** = `t_sat − t_fc`.
  - Report it for assimilation **and** open-loop, **even if negative or null**.
  - Show the q10–q90 ETA interval at `t_fc` and whether `t_sat` fell inside it.
  - (P2) Show a false-alarm count in zone-hours for zones that didn't burn.

**Rules**
- **No future data.** HRRR is the newest **available** cycle, asserted in code.
- The 03:25 orders are context for the comparison, not an outcomes claim.
- **Never say:** "would have saved lives", "would have warned them hours earlier", "the county failed". **Do say:** "the county's and state's own reviews document an information gap that night; this targets it."

## 3. Sensitivity (Eaton)

- Open with one respectful sentence about the dead, then move to the information gap.
- **Never show** death locations, names, house numbers, facility names or individual damage records. Exposure is aggregated by zone.
- **Don't** name or blame officials or SCE. The cause is out of scope; the ignition is just a point.
- If asked about West Altadena's history as a Black community, acknowledge it briefly and factually. Don't use it as a pitch device.
- The judge's phone is a "resident who gets an act-by time", never a "victim".

## 4. Prepared Q&A (one sentence, then stop)

| Question | Answer |
|---|---|
| vs NOAA NGFS / Watch Duty? | NGFS tells you **where the fire is** within minutes. We forecast **where it will be in the next 6 h and who's in the way**, re-fit at the same cadence. Detections like NGFS's are our input. |
| vs Technosylva / WIFIRE? | They run spread simulations on demand from a perimeter. We assimilate **every** GOES scan into an ensemble, learn the model's biases, and report lead time against open-loop. Complementary. |
| Why physics, not ML? | It generalizes to unseen terrain and winds, and it's explainable. The filter learns wind-direction and spread-rate bias from each look. |
| How accurate? | Show the Evaluation card (DA vs open-loop, ETA interval) and Sørensen vs NASA FEDS perimeters. Then the limits. |
| What can't it model? | House-to-house spread and embers are labeled heuristics. HRRR winds are 3 km. GOES pixels are ~2.5 km, with approximate parallax correction. |
| Real vs simulated? | Real: satellite, winds, terrain, fuels, zone names, official timeline. Stand-ins: residents, phones, duty officer. (Answer only if asked; not labelled on screen.) |
| Who decides? | A human approves every alert. Claude only phrases numbers our model computed, a validator rejects any altered number, and no AI can send or order anything. |
| Privacy? | Random IDs and an assigned demo location. Voice goes to xAI (STT) and text to Anthropic (parsing). Transcripts are private and deleted at reset. Phone numbers stay in memory, masked. |
| Who uses it Monday? | An OES analyst can run this replay workflow on any past fire for after-action review and training today. Live mode is the same code reading the live GOES bucket (roadmap). |
| Path to production? | Evaluate on many past fires, then pilot with one county OES. Feed alerts into existing tools (e.g. Genasys). NOAA's RRFS succeeds HRRR (NWS notice SCN 26-48). |
| How fast? | Quote the **measured demo-laptop number** from `/healthz` (physics ms per cycle), and the GOES latency median from `goes_latency_median_min`. |
| Why SpacetimeDB? | The engine, dashboard, control tablet, judges' phones and the voice agent coordinate through one live shared state: the clock, the alert state machine, reports and acknowledgements. |

## 5. Sponsor eligibility checklists

| Prize | Must-do | Owner |
|---|---|---|
| **Cal Hacks** Overall / Technically Complex / People-First (Planet-First only if uncapped) | Pitch along the rubric | Cody |
| **Microsoft: Built for the Real World** | README sections titled exactly: *Customer understanding*, *Measurable usefulness*, *Path to production*, *Responsible design*, *End-to-end functionality* | Cody |
| **SpacetimeDB** | Ask at the booth or the 16:00 workshop how to enter. README: tables, reducers, the shared-state story. Demo: two browsers + phones in sync; the ack flips the dashboard. | Cody |
| **SpaceXAI: Make it Legendary** | Built with **Cursor** (keep `.cursor/rules`, commit history, screenshots in `docs/process/`). **Grok Voice API** (STT + TTS) in the product; confirm at the booth that it counts. **Grok Bot** planning screenshots. Space data is core. | All |
| **MLH: A-Team with SpaceXSI** | Devpost description says how Cursor and Grok Bot were used; opt into the raffle | Cody |
| **Cartesia** | 2-min demo-first presentation (the phone ring). README: setup, features (Managed Agent, outbound call, webhook tools, dynamic variables), limitations, privacy/accessibility. No secrets. | Cody |
| **Anthropic** | Named community: *"LA County fire and OES duty officers deciding evacuation timing on wind-driven night fires in foothill communities."* Real problem: county AAR / Citygate / FSRI **plus** a first-hand conversation note (`SPEC.md` §8 Q5; never invented). Claude's role and the failure-modes table (`05` §9). Monday user = OES after-action/training analyst. | Cody |
| **MLH Commit Fellowship** | Opt in **and** submit `mlh.link/commit-fellowship-form` | Cody |
| MLH Gemini / Vultr / .Tech, Apify | Only if the P2 item actually ships | — |
| Notability / Figma / Mana | Only if already true | — |

## 6. Devpost submission (target Sun 08:30; hard close 09:00)

- [ ] Team name, table number, and the pitch:
  > *"Emberline re-fits a wildfire spread forecast every time a satellite looks. It assimilates GOES and VIIRS detections into a physics ensemble every 5 minutes, ranks who is in the way with act-by times, and phones a duty officer after a human approves. Shown as a retrospective replay of the Eaton Fire night."*
- [ ] Image: a dashboard screenshot with the alert card.
- [ ] **Public repo.** Check that no secrets are in the history (`git log -p | grep -iE "sk_|key=|token|\+1[0-9]{10}"`) and that `.env.example` exists.
- [ ] **Video ≤ 3:00, demo-first:** phone rings → forecast shifts → problem → how built → Evaluation card. Backup video at M4 (Sun 02:00); final at M5.
- [ ] **Description:**
  - real vs simulated table
  - architecture (one tool, one job)
  - Cursor + Grok Bot usage
  - limitations
  - responsible design
  - the v1 config hash (and v2, if any)
- [ ] All relevant prize boxes ticked. If there's a cap, order them: Overall/Tech/People-First → Microsoft → SpacetimeDB → Cartesia → Anthropic → SpaceXAI → MLH.
- [ ] Commit Fellowship form; SpacetimeDB entry per the booth.

## 7. README outline

1. Pitch + 30-s GIF.
2. Who it's for + the problem (documented gap + first-hand note).
3. Real vs simulated.
4. Architecture + "one tool, one job".
5. Evaluation (definitions, v1 numbers, hash).
6. How to run: replay, offline, rebuild the scenario, precompute.
7. Sponsor sections:
   - Microsoft (5 headings)
   - SpacetimeDB
   - Cartesia
   - Grok + Cursor/Grok Bot
   - Claude (role + failure modes)
8. Limitations (`02` §13) + roadmap: live mode, RRFS, WindNinja, structure-spread model, more sensors, flood.
9. Data and licenses: NOAA, NASA FIRMS, USGS, LANDFIRE, Census, OSM (ODbL), NAIP; pyretechnics (EPL-2.0).
10. Team and process (`docs/process.md`).

## 8. Demo-readiness checklist (M5, Sun 06:00)

- [ ] v1 (and any disclosed v2) precompute for `main`, `openloop` and `demo_b1`. `effect.json` shows a visible shift.
- [ ] **Hard reset → full script → hard reset → full script**: works twice in a row.
- [ ] **Offline (Wi-Fi off):**
  - local SpacetimeDB + engine + dashboard
  - cached briefings
  - "Play briefing here"
  - dashboard click-to-report → `/dev/report` → branch
- [ ] **Online:** venue Wi-Fi and phone hotspot. Cartesia call to the teammate phone (number saved as a contact). Two judge phones on cellular.
- [ ] Backup video on the laptop **and** a phone; screenshot slides per beat.
- [ ] Charger, power strip, hotspot, second laptop with the build and the cache.
- [ ] Three timed runs ≤ 3:00; Q&A drill (§4).
- [ ] Nothing on screen shows a real person's name, address, phone number or facility name.
