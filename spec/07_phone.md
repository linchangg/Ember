# 07: Phone page `/p`: the judge becomes a resident (owner: Linda, both the UI and the `web/api/*` endpoints)

**Goal.**
1. A judge scans the QR code on `/control`.
2. Their phone becomes a resident at a **simulated location in west Altadena**, shows a live act-by countdown, and buzzes and speaks when an alert for their zone is approved.
3. It lets them **speak a fire report** that moves the forecast (Moment 2).

**Constraints**
- Served from **Vercel over HTTPS**, which the microphone requires.
- Talks only to the hub (SpacetimeDB over `wss`) and to `web/api/*`.
- Must work on cellular, so it never touches the laptop.

## 1. Flow

1. **Open** `https://<vercel-app>/p?j=demo` (the join code selects the spawn set in `spawn_points.json`). A consent screen:
   > "Emberline demo. You'll play a resident of Altadena on the night of Jan 7, 2025, at a simulated location. We store a random ID and your reports. If you speak a report, the audio is sent to xAI for transcription and the text to Anthropic for parsing. Transcripts are deleted at reset. Not a real alert system."

   [Join]

   The tap also unlocks audio playback and requests microphone permission.
2. **Join:**
   - `person_id = per_<uuid8>` is created once and kept in localStorage.
   - Call `join_as_person(scenario_id, person_id, joinCode, label = "Judge " + 2 random letters)`.
   - The hub assigns the next spawn point (`04` §2.2). Spawn points are hand-picked streets in at-risk west-Altadena zones (`01` §6).
3. **Main screen** (single column, big type, works one-handed). Elements in order:
   - **Status banner:**
     - zone name + status chip: Clear / Watch / Warning / ORDER
     - "**Act by 23:40**" with a countdown in **sim time** (uses `simNow()` from the `clock` row), or "No action needed yet"
     - "Chance fire reaches your area within 1 h: 34%" (the `person` row's `p_60`, which equals the zone's value, so it always matches the dashboard)
     - "Updated 23:05"
   - **Map** (MapLibre GL, light vector basemap, ~55% of the height):
     - "You are here (demo location)" marker
     - danger polygons from `cycle_head.danger`: `lead60_p30` (red fill α .35) and `lead120_p30` (orange outline)
     - your recent reports as circles
     - (P2) the route line to the assembly point
   - **Hold-to-talk button:** "**Hold to report what you see**". Release sends.
     - Under it: "or tap the map, then choose: 🔥 Fire · 💨 Smoke · ✨ Embers · ✅ All clear".
   - **Status buttons:** I'm safe · I'm evacuating · I need help (`set_person_status`).
4. **When an alert covering my zone becomes `approved`:**
   - full-width banner in amber (warning) or red (order) + `navigator.vibrate([300,100,300])`
   - play `/api/tts` audio: "Emberline alert for {zone}: fire {p_words} within {lead}. Act by {time}."
   - show the alert `text`
   - fire once per alert ID (remember in localStorage)
5. **After a report:**
   - Play the readback audio from the `/api/report-voice` response, then show "Sent to the forecast · trust: citizen report".
   - When the report row becomes `accepted` and a `cycle_head` for a **new branch** arrives with `knowledge_sim_ms ≥ report.observed_sim_ms`, show "Forecast updated with your report ✓".
   - If the report is `rejected`, show the reason kindly.

## 2. Recording (Moment 2 path)

- Use `getUserMedia({audio: true})` with an `AudioContext` and a small `AudioWorklet` or `ScriptProcessor` recorder. Downsample to **16 kHz mono 16-bit PCM WAV** in the browser.
  - This avoids iOS Safari `audio/mp4` vs `webm` incompatibility with the STT API.
- Cap at 15 s. Show a level meter while recording.
- POST `multipart/form-data` to `/api/report-voice` (`05` §2) with `audio`, `person_id`, `scenario_id`, `observed_sim_ms = simNow()`, and `tap_lon`/`tap_lat` if the user tapped the map first.
- Handle errors with retry; the tap fallback stays visible.

## 3. Data subscriptions (keep them small; `04` §4)

| Table | Filter | Use |
|---|---|---|
| `clock` | `scenario_id` | `simNow()`, active branch |
| `cycle_head` | `scenario_id` (all branches; pick active) | danger polygons, last update |
| `person` | own `person_id` | risk fields, location |
| `alert` | `scenario_id` (filter client-side to own zone, `state ∈ approved/calling/called/acknowledged`) | alert banner |
| `report` | own `person_id` | report status |

Never subscribe to `zone_risk`, `cycle` or `observation` on the phone. The payload target is < 100 KB.

## 4. Accessibility, language and privacy

- Font ≥ 18 px; status text plus colour; works with VoiceOver: the alert banner is an `aria-live="assertive"` region.
- (P2) A Spanish toggle for the 12 static strings. Grok STT handles Spanish reports, and the parser prompt accepts any language. A People-First touch for a bilingual community; mention it only if it works.
- No GPS in replay mode, since the judge is in SF. In live mode (roadmap), the location would come from GPS with consent.
- We store a random ID, assigned location, reports and transcripts for the session only.

## 5. Endpoints this page uses (built by Cody, `05`)

| Endpoint | Purpose |
|---|---|
| `POST /api/report-voice` | Speech → transcript → parse → `submit_report` → readback (`05` §2) |
| `POST /api/report-tap` | `{scenario_id, person_id, lon, lat, kind, observed_sim_ms}` → `submit_report` |
| `POST /api/tts` | Text → mp3 (readbacks, alert audio) |

All of them use the Vercel service identity (`STDB_SERVICE_TOKEN`). No key is ever sent to the browser.

## 6. Acceptance tests (manual, on two real phones: iOS Safari + Android Chrome, on cellular)

1. Join assigns distinct spawn points to two phones. Refreshing keeps the same person.
2. The act-by countdown matches the dashboard's zone act-by within 1 sim-minute during 240× play.
3. Approving an alert on the dashboard → the phone banner + audio within 2 s.
4. A spoken report "I see flames above the end of Lincoln Avenue" → readback within 4 s → the dashboard shows the report circle → a branch forecast within a further ≤ 10 s → the phone shows "Forecast updated".
5. Wi-Fi off on the phone (cellular only): everything still works.
