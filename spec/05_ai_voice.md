# 05: AI and voice: Claude, Cartesia, Grok, Vercel functions

**Owners:**
- **Cody:** engine side: `ai/briefing.py`, `ai/validate.py`, `notify/cartesia.py`, plus the Cartesia agent setup.
- **Linda:** the Vercel functions in `web/api/*` (§2–4). They are TypeScript, and her phone page calls them.

> **v1.1 changes after review:**
> - Claude API parameters corrected for Sonnet 5.5 (no forced tool, no temperature)
> - `Cartesia-Version` header and per-call errors
> - Default duty officer is a teammate's phone
> - Demo framing line added
> - Grok model names to re-verify
> - Hub auth by identity

**One job each** (`SPEC.md` D7–D9):

| Job | Provider | Runs in |
|---|---|---|
| Briefing writer (risk table → alert text + voice script) | Claude `claude-sonnet-5-5` | Engine (`ai/briefing.py`) |
| Report parser (transcript → structured ground report) | Claude `claude-haiku-5-5` | Vercel `web/api/report-voice.ts` |
| Voice **out to officials**: outbound call, Q&A, acknowledgement | Cartesia Managed Agent (LLM = Claude if their catalogue offers it) | Cartesia + Vercel webhooks |
| Voice **in/out with the public**: speech → text, readbacks, resident alert audio | Grok Voice API (`/v1/stt`, `/v1/tts`) | Vercel `web/api/report-voice.ts`, `web/api/tts.ts` |
| (P2) Camera frame → fire/smoke box → bearing | Gemini Flash | Vercel `web/api/camera-frame.ts` |

**Hard rule.** No model computes a probability, a time, or a fire behavior. Models only phrase, parse and converse over numbers that our physics and risk code produced. Every model output is validated or echoed back.

## 1. Claude briefing (`engine/emberline/ai/briefing.py`)

**When:** on every new alert proposal. During precompute for the mainline (cached), live for branches. The alert is published with **template text first** (`03_engine` §8). The Claude draft replaces it through `upsert_alert_draft` only if it validates and the alert is still `proposed`.

**Input** (JSON, ≤ 4 KB; the model sees nothing else):

```json
{
  "issued_pst": "23:05", "data_through_pst": "23:05", "forecast_age_min": 0,
  "alert": {"level": "order_recommended", "rule_text": "P(fire reaches zone within 1 h) ≥ 50%..."},
  "zones": [ {"zone_id": "ALD-FARNSWORTH", "name": "Farnsworth", "p_60": 0.54, "p_120": 0.81,
              "eta_q50_pst": "23:58", "act_by_pst": "23:40", "evac_min": 45, "population": 2140,
              "critical": ["nursing home: <name>"], "egress_roads": 2, "trend": "up"} ],
  "fire": {"burned_ha": 1830, "growth_ha_per_h": 410, "head_bearing_deg": 250},
  "sensors": [{"name": "GOES-18", "last_scan_pst": "23:05", "status": "ok"}, {"name": "VIIRS", "last_pass_pst": null}],
  "honesty": "Retrospective replay of Jan 7–8 2025. Decision support only."
}
```

**Call (Messages API, `claude-sonnet-5-5`).** Checked against the Sonnet 5.5 migration guide on Oct 24:
- **No `temperature`/`top_p`/`top_k`.** Sampling parameters return a 400 error.
- **No forced `tool_choice`.** `tool` and `any` also return a 400 error.
- **No assistant prefill.**
- **Use structured outputs:** `output_config.format` with a JSON schema (`additionalProperties: false` on every object; check the structured-outputs docs for the exact field shape). The schema:
  - `headline` (≤ 90 chars)
  - `bullets` (≤ 4 strings, ≤ 120 chars each)
  - `text` (≤ 300 chars)
  - `voice_script` (≤ 110 words)
- `thinking: {"type": "between_tools"}`. This is the lowest setting on Sonnet 5.5; `disabled` returns a 400 error.
- `max_tokens: 2000`; timeout 20 s (precompute doesn't care).
- Select response content blocks **by type**.
- Retry once on a malformed or refused response (`stop_reason: "refusal"`). Then use the template.
- **Verify with one real call at M1.**

**System prompt** (`ai/prompts/briefing_v1.md`; record `prompt_version`):
- You write decision-support briefings for an incident commander. Use only facts in the input JSON.
- `text` must begin with "DEMO REPLAY (Jan 2025)." `voice_script` must begin with "This is an Emberline demo replay of January 2025."
- Refer to facilities by type ("a nursing home"), never by name. The input carries types only.
- Copy times, percentages, zone names and counts exactly.
- Probability words must follow this mapping: ≥ 90% "very likely", ≥ 70% "likely", ≥ 50% "more likely than not", ≥ 30% "possible", otherwise "unlikely".
- Lead with the action and the act-by time. State the data age.
- Never say "will", "guaranteed", "certain" or "would have saved". Never recommend beyond the alert level given. End the voice script with "You make the call."

**Validator** (`ai/validate.py`); a pure function with unit tests:
1. Extract every number, time (`\d{1,2}:\d{2}`), percentage and zone ID from all output fields.
2. Each must appear in the input after normalization (`0.54` ≡ `54%`; `23:40` ≡ `11:40 PM`). Small integers ≤ 10 are allowed, e.g. "two roads".
3. Probability words must match the mapping for the referenced zone's P.
4. Banned phrases: `will reach`, `will burn`, `guaranteed`, `certain`, `would have saved`, `evacuate now` (unless the level is `order_recommended`).
5. Length limits are enforced.

On any failure: `grounded=False`, `unmatched_tokens=[...]`, use the template, `fallback_used=True`. Emit `Claude draft rejected by validator → template used`. The UI shows a small "template" tag. That's a feature, not a bug: it's our failure-mode story.

**Store:** `Briefing` row with `input_hash`, `model`, `prompt_version`, `latency_ms`.

## 2. Voice report from a phone (`web/api/report-voice.ts`; Vercel Node function)

The phone page (`07_phone`) records ≤ 15 s of audio. **Encode WAV in the browser** (16 kHz mono PCM via Web Audio). That avoids iOS Safari's `audio/mp4` vs `webm` mismatch. POST it as `multipart/form-data`: `audio`, `person_id`, `scenario_id`, optional `tap_lon` / `tap_lat`, and `observed_sim_ms` (the phone's `simNow()`).

**Steps**
1. **Rate limit:** 1 request per 20 s per `person_id` (in-memory map; fine for a demo).
2. **Grok STT:** `POST https://api.x.ai/v1/stt` with the audio and `Authorization: Bearer XAI_API_KEY`. **Re-verify the model name with a curl at M1:** research 04 read `grok-voice-transcribe-2.0` on docs.x.ai, while aggregators list `grok-stt`. Also verify the field names. Get the transcript.
3. **Claude Haiku parse** (`claude-haiku-5-5`):
   - Haiku 5.5 **accepts** a forced tool (`tool_choice: {type: "tool", name: "parse_report"}`).
   - **Omit `temperature`.**
   - Set `output_config: {effort: "low"}` for latency.
   - Select blocks by type.
   - Tool `parse_report`: Input: the transcript + the person's assigned location, its nearest street names (from a small static `streets.json` list for the spawn set), + `tap` if present.

   ```json
   {"is_report": true, "kind": "fire|smoke|embers|all_clear", "direction_deg": null, "distance_m": null,
    "landmark": "end of Lincoln Ave", "confidence": 0.7, "readback": "Got it: flames near the end of Lincoln Avenue, about now."}
   ```

   Rules: never invent a location. Use `tap` if given, else the person's location plus an offset only if direction and distance were spoken.
4. **Compute the point and radius:**
   - `point = tap ?? offset(person_loc, direction_deg, distance_m) ?? person_loc`
   - `radius_m = max(250, 0.5·(distance_m ?? 300))`
   - `trust = 0.5·confidence`, clipped to [0.2, 0.5]. Citizen reports are never fully trusted.
5. **Hub:** if `is_report`, call `submit_report(report_json, transcript)` over HTTP (`04` §3) with the **Vercel service identity token** (`STDB_SERVICE_TOKEN`):
   - `report_json` contains `provenance: "injected"`, `channel: "voice"`, `summary: readback`. It contains **no transcript**.
   - The transcript goes into the private `report_text` table.
6. **Response:** `{report_id | null, readback, kind, lon, lat, radius_m}`. The phone then calls `/api/tts` with `readback`. If `is_report` is false: `"I didn't catch a fire report. Hold the button and describe what you see."`

**Latency target:** STT ~1 s + parse ~1.5 s + hub ~0.3 s ≤ 3 s. The engine's branch re-forecast adds ≤ 5 s.

**Tap-only fallback: `/api/report-tap`.** Request body: `{scenario_id, person_id, lon, lat, kind, observed_sim_ms}`. It runs steps 4–5 with no audio, trust 0.4 and `summary` = "Tap report: {kind}". It is always available, including when Grok is down. **Offline**, the dashboard uses the engine's `/dev/report` instead (`03` §11).

## 3. Grok TTS (`web/api/tts.ts`)

- `POST {text, voice?: "eve"}` → Grok `POST https://api.x.ai/v1/tts` → returns `audio/mpeg`. Verify the request shape in the docs.
- Cache by `sha1(text|voice)` in memory and in the `Cache-Control` header.
- Max 400 characters.
- **Used for:**
  - (a) report readbacks;
  - (b) the **resident in-app alert**: when an alert for the phone's zone becomes `approved`, the phone requests `/api/tts` with "Emberline demo replay. Alert for {zone}: fire {p_words} within {lead}. Act by {time}."

**Why Grok and not Cartesia here:** public-facing voice and officials-facing voice are separate jobs and separate prizes (`SPEC.md` D8). Mention in README that **Grok Voice API is used for speech-to-text and text-to-speech of public reports**. Confirm at the SpaceXSI booth that this satisfies "Grok … Voice API". If not, swap the phone readback to Grok's realtime voice endpoint (stretch, +2–3 h).

## 4. Cartesia outbound call to the duty officer (Moment 1)

### Setup (Cody, at the G1-c gate ~08:30–09:30)
1. Sign in at play.cartesia.ai and redeem `CARTESIACALHACKS`. Create an API key, then a **Managed Agent**:
   - **LLM:** Claude if it is listed (`GET /v1/agents/models`); otherwise their default.
   - **Voice:** a calm Sonic voice.
   - **System prompt:** below.
   - **Dynamic variables:** `alert_id`, `voice_script`, `zones_spoken`, `act_by_spoken`, `p_words`, `issued_spoken`.
   - **Webhook tools:** below.
2. Get **one Cartesia Number** (free plan: 1 slot, dials US numbers). Store `CARTESIA_FROM_NUMBER_ID` and `CARTESIA_AGENT_ID`. **Deploy the agent:** an undeployed agent returns `404 deployment not found` when dialing.
3. **Test:** outbound call to a teammate's phone with a dummy `voice_script`. **Pass = the phone rings, the script is read, and "acknowledged" triggers the ack tool.** If the free plan can't dial out, ask at the booth for a credit. Otherwise: **fallback D7**, the dashboard plays the voice script and the "call" is shown as unavailable.
4. **Default duty officer = a teammate's phone.** Save the Cartesia number as a contact, so iOS "Silence Unknown Callers" and spam filters let it through, and put it on speaker for the judge. Calling a judge's own phone is optional: they type the number into the **dashboard** (`06` §5.4), which posts to the engine's `/dev/duty-officer`.

**Agent system prompt** (store a copy in `engine/emberline/ai/prompts/cartesia_agent_v1.md`):
- You are Emberline's briefing line for a fire duty officer.
- Read `{{voice_script}}` first, verbatim.
- Answer follow-up questions only from `{{...}}` variables or the `get_status` tool. If you don't know, say so. Never estimate numbers yourself.
- Never issue or confirm an evacuation order; the officer decides.
- When the officer says "acknowledged", "copy" or "received", call `acknowledge` with `{{alert_id}}`, confirm, and end the call politely.
- Keep answers under 20 seconds.

**Webhook tools.** All are Vercel functions. Cartesia allows a 20 s timeout and a 16 KiB response.

| Tool | Endpoint | Returns |
|---|---|---|
| `get_status(alert_id)` | `POST /api/cartesia/status` | Alert state + current `zone_risk` for its zones (p_60, p_120, eta_q50, act_by in spoken PST) + forecast age. Read via hub SQL. |
| `acknowledge(alert_id)` | `POST /api/cartesia/ack` | Calls `ack_alert(alert_id, "phone")` with the service identity token; returns `{ok}` |

Verify Cartesia's webhook signature or shared-secret header (check the docs). At minimum, require a `?k=` secret matching `CARTESIA_WEBHOOK_SECRET`.

### Placing the call (engine `notify/cartesia.py`)

On `alert_approved`:

```
POST https://api.cartesia.ai/agents/calls
{ "agent_id": CARTESIA_AGENT_ID, "from_number_id": CARTESIA_FROM_NUMBER_ID,
  "outbound_calls": [{ "to_number": <E.164 from DUTY_OFFICER_NUMBERS or /dev/duty-officer>,
                       "dynamic_variables": {...} }] }
```

Verify the request shape in docs.cartesia.ai (outbound dialing).

- **Headers:** `Authorization: Bearer CARTESIA_API_KEY`, plus the **required `Cartesia-Version` header** (`CARTESIA_VERSION`; value from the docs).
- **A 200 can still carry per-call errors:** check each `calls[i].error`.
- **Success:** `update_alert_call(alert_id, "calling", agent_call_id, ...)`. Set `called` once the API confirms the call was placed; poll the call status if an endpoint exists.
- **Failure:** `failed` plus an event. The UI offers "Play briefing here".
- `update_alert_call` never changes an `acknowledged` alert's state (`04` §2.2), so a late `called` is harmless.

**Privacy and responsibility (README + Cartesia criterion)**
- Numbers are held in memory or `.env` only and shown masked (`•••• 4821`). We don't store recordings.
- The judge consents before we call.
- The call is decision support for a trained official, never a public alert.
- Accessibility: hands-free, any phone.

## 5. (P2) Camera sensor: Gemini bearing (`web/api/camera-frame.ts`)

- `/cam` page (laptop webcam, "placed" at a real lookout from `sensors.json`, e.g. Echo Mountain, with `heading_deg` and `hfov_deg`) posts a JPEG every 3 s.
- **Gemini Flash** with a prompt to return `box_2d` for `flame` / `smoke`, normalized to 0–1000, plus a confidence.
- **Bearing:** `bearing = heading + (x_center/1000 − 0.5)·hfov`; `sigma = 2° (flame) | 5° (smoke) + box_width/1000·hfov/4`.
- Require the same class in **2 consecutive frames** before emitting.
- Call a service reducer `submit_observation(obs_json)` with the service identity. Add it to `04` §2.2 when you start P2. It mirrors `submit_report` with `command.kind = "observation"`.
- The engine branches exactly as for reports. Label the observation `provenance: "simulated"`: a real webcam placed at an assumed location.

## 6. (P2) "Ask the operating picture"

A Claude tool-use agent in the engine with read-only tools: `get_risk_table`, `get_zone`, `get_recent_observations`, `diff_cycles(a, b)`. Its one write tool is `draft_alert(zone_id)` → creates a `proposed` alert only. Every tool call is logged as an `ActivityEvent` with actor `claude.agent`. Skip unless M3 is green with time to spare.

## 7. Config and keys

| Var | Where | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | engine `.env` + Vercel | briefing, parser |
| `XAI_API_KEY` | Vercel | STT/TTS |
| `CARTESIA_API_KEY`, `CARTESIA_AGENT_ID`, `CARTESIA_FROM_NUMBER_ID` | engine `.env` | outbound call |
| `CARTESIA_WEBHOOK_SECRET` | Vercel + Cartesia tool URL | webhooks |
| `CARTESIA_VERSION` | engine `.env` | required header |
| `STDB_URI`, `STDB_DB`, `STDB_SERVICE_TOKEN` (Vercel's own identity, registered by `set_identities`) | Vercel | hub reducers/SQL |
| `GEMINI_API_KEY` | Vercel | P2 camera |

## 8. Tests

1. **Validator:** 12 table-driven cases (correct; altered time; altered %; invented zone; wrong probability word; banned phrase; over-length). All are rejected except the correct one.
2. **Briefing smoke test:** 3 recorded inputs from the fixture → Claude → validator passes on ≥ 2 of 3. If it passes on fewer, tighten the prompt; the template covers the rest.
3. **Parser:** 8 transcripts ("flames above the end of Lincoln", "lots of smoke to the north maybe half a mile", "uh testing", "it's all clear here", Spanish "veo llamas cerca de mi casa", …) → expected `is_report`/`kind`; no invented coordinates.
4. **Cartesia dry run:** `notify/cartesia.py --dry-run` prints the exact request body. One real call to a teammate at G1-c and again at M2.

## 9. Failure modes, for the README and the Anthropic pitch

| Risk | Mitigation |
|---|---|
| **Altered numbers or times** | Grounding validator + template fallback; the model never sees raw ensembles |
| **Over-confidence** | Fixed probability-word mapping; data age in every briefing; "you make the call" |
| **Stale data** | Forecast age and sensor status in the input; the briefing states them |
| **Mis-parsed citizen reports** | Readback to the reporter; low trust (≤ 0.5); radius ≥ 250 m; the filter weighs reports against satellite evidence and can reject them |
| **Model acting on its own** | No tool can approve, send or order; humans approve in the hub; the engine sends only `approved` alerts |
| **Voice agent hallucinating** | Answers only from dynamic variables or `get_status`; scripted refusal otherwise |
