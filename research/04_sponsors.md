# 04: Sponsor tools and prize tracks for Emberline

Agent 4 of 6. Researched Sat Oct 24, roughly 4:30 to 5:00 AM PT. "(unverified)" marks anything not confirmed from a primary source. Win odds are my judgment.

## TL;DR

- **Stack (one tool, one job):**
  - Cursor plus Grok Bot: how we build.
  - **SpacetimeDB:** live operating-picture state.
  - **Claude:** language reasoning over the ranked risk table (briefing, report parsing, Q&A brain).
  - **Cartesia Managed Agent:** outbound phone call to officials, with two-way Q&A.
  - **Grok Voice API (STT + TTS):** voice intake of public ground reports.
  - **Gemini:** camera frame to fire/smoke box to bearing.
  - **Vultr:** public HTTPS host.
  - **Apify:** Actor for official evacuation zones and orders.
- **Skip:** Convex, Tiger Data, Bright Data, ElevenLabs, Grok Imagine, Snowflake, Bitdeer, DigitalOcean, MongoDB, Auth0, Factory, Fetch.ai (stretch only), and all finance and shopping tracks.
- **Voice conflict resolved:** Cartesia is voice out to officials (Moment 1: a judge's phone rings). Grok is voice in from the public (Moment 2: a judge speaks a report and the forecast shifts). Different jobs, different directions.
- **Twilio SMS to a judge's phone is not realistic in 24 h** (verified). Trial accounts reach only 5 verified numbers and "custom message bodies… aren't available during trial." US 10DLC registration needs a paid account, and toll-free numbers need verification. Make the Cartesia call the alert. Push text alerts to the QR web page over the SpacetimeDB subscription instead of SMS.
- **Prize cap:** Neither Devpost nor the Hive FAQ states a cap. The only explicit opt-in is Commit Fellowship (checkbox plus form). Confirm at the help desk (unverified).

## 1. Rules and criteria beyond the snapshot

**General rules:**
- Devpost: teams of 2 to 4. Hard deadline Oct 25 at 9 AM PST, checked in.
- Required: project image, GitHub repo, a live or pre-recorded demo, and a video for asynchronous sponsor judging. All implementation must happen during the event (Hive FAQ).
- The /rules page holds only the Code of Conduct.

**Value discrepancies:**

| Prize | Devpost | Hive |
|---|---|---|
| SpaceXAI | $400 cash plus keyboard | Keyboard |
| Convex | $1,308 | $5,000 |
| Cartesia | $1,516 | Meta glasses TBD |
| SpacetimeDB | Not listed (presenting sponsor) | $2,048 / $1,024 / $512 |

**Per-sponsor details:**

- **SpaceXAI (Hive, verified):** "must be built with Cursor and must use the Grok Imagine or Voice API … Grok Bot for planning … bonus … encourages using space data." Grok Bot (x.ai/bot) is a desktop and iOS app of agent "teammates." The booth gives X Premium codes.
- **Cartesia guide:**
  - Route A: Managed Agent (prompt, voice, LLM, webhook tools, phone number). Route B: Sonic TTS inside the app.
  - Use Managed Agents, not the Line SDK, which loses hosting on Dec 1, 2026.
  - CARTESIACALHACKS applies to "first-time orders."
  - Judged 25% each: problem and originality, use of Cartesia, execution, impact and responsibility (accessibility, privacy).
  - Deliverables: 2-minute demo-first presentation, README (setup, features used, limitations), no secrets.
- **Fetch.ai hackpack:**
  - Must register on Agentverse, use the Chat Protocol, be discoverable in ASI:One, and show tool execution or multi-agent work.
  - **The primary workflow must run inside ASI:One with no custom frontend.**
  - README needs badges and agent addresses. Video of 3 to 5 minutes. Weights 25/20/20/20/15.
  - Prizes: $1,250 / $750 / $500 plus two $250 awards.
- **Figma:**
  - Best Figma Workflow $2,000, Best Stage Log $2,000, Best Design $500, Most Creative $500.
  - Needs Figma Slides, a stage log (Ideation, Prototyping, Handoff, Code; ≤200 words each), files shared with "Anyone", EDU verification, and a video of 3 minutes or less.
- **Factory Droid SDK:**
  - `pip install droid-sdk` plus the `droid` CLI and `FACTORY_API_KEY`. It is Factory's agent harness with Edit, Execute, Read and Grep tools, autonomy levels and custom MCP tools.
  - Criteria: real work in the running product, a designed trigger, scoped access, visible steps.
- **Notability:** judged on process notes ("real role … or added after the fact?").
- **Mana:** meaningful use 30%, usefulness 25%, originality 25%, execution 20%, plus up to 10 if the project runs inside Mana. Product unknown, because the page is rendered in JavaScript (unverified).
- **Not relevant:** Atlassian (Forge app for Jira/Confluence), Context.dev (scraping and search credits, no prize), Canva (SDK guide only).
- **MLH:**
  - Vultr: $100 credit via mlh.link/vultr-discover, entered as a gift code in Billing (verified).
  - Tiger Data: $1,000 in credits valid 30 days (verified).
  - .Tech, GoDaddy and ElevenLabs: promo codes on the MLH prizes page.
  - DigitalOcean: credits from an MLH coach.
  - Commit: opt in plus mlh.link/commit-fellowship-form.

## 2. Voice decision

**xAI's voice offering (verified, docs.x.ai).** The pricing page groups all of these under "Voice API":

| Capability | Endpoint / model | Key details | Price |
|---|---|---|---|
| Realtime speech-to-speech agent | `wss://api.x.ai/v1/realtime`, `grok-voice-3.0` | Server VAD, custom function tools, 20+ languages. Browsers need ephemeral tokens passed as subprotocol `xai-client-secret.<t>`. | $0.08/min |
| TTS | `POST /v1/tts` (also a WebSocket) | Returns MP3 by default (wav, μ-law 8 kHz). Voices eve, ara, rex; speed; normalization; pronunciation map. | $15 per 1M chars |
| STT | `POST /v1/stt`, `grok-voice-transcribe-2.0` | webm/opus, mp3, wav; 38+ languages; word timestamps. | $0.10/hr |
| Telephony | Direct SIP | **Inbound only** (Twilio SIP trunk). No outbound dialing documented. | n/a |

**Grok Imagine** generates images and video (about $0.02 per image, $0.05 to $0.08 per second of video). **Do not use it.** Generated fire or "satellite" imagery in an emergency operating picture is fabricated data. That hurts us on Microsoft's "responsible design", on Cartesia's responsibility criterion, and on our own honesty principle.

**Cartesia (verified):**
- Managed Agents use an LLM from a catalogue that includes Anthropic, OpenAI and Gemini (exact models via `GET /v1/agents/models`).
- **Webhook tools** call our HTTPS endpoint (20 s timeout, 16 KiB response cap) and pass the result to the LLM. **Dynamic variables** are set per call.
- **Outbound calls:** `POST https://api.cartesia.ai/agents/calls` with `from_number_id`, `agent_id` and `outbound_calls[{to_number, dynamic_variables}]`.
- **Cartesia Numbers** are free on any plan (Free plan: 1 slot) and dial US numbers only. $0.06/min plus $0.014/min telephony, plus LLM tokens.
- Not stated: whether the free plan can dial out without a card. Test early.

**ElevenLabs:** a third voice provider would duplicate a job. Skip.

**Role split:**

| Job | Provider | Why |
|---|---|---|
| **Voice out to officials:** a threshold crossing triggers an outbound call. The agent reads the act-by briefing (passed in as dynamic variables) and answers follow-ups ("When must Zone 4 leave? How confident? Which roads?") through webhook tools against the Emberline query API. "Acknowledged" calls `ack_alert`, which writes to SpacetimeDB, and the dashboard shows "Acknowledged by phone 23:14". | **Cartesia** Managed Agent, with Claude as its LLM if our account lists it | Outbound dialing is built in, so no Twilio. The voice call *is* the alert channel, which passes "core, not decorative". Accessible: any phone, hands-free. The acknowledgement loop shows judges the shared state. |
| **Voice in from the public:** on the QR phone page, hold to talk. `/v1/stt` transcribes, Claude parses into an Observation (point, radius, trust), and `/v1/tts` reads back "Got it: flames near Lake Ave and Woodbury, 2 min ago. Sending to the forecast." | **Grok Voice API** | Meets SpaceXAI's requirement with a distinct job (a sensor input). Multilingual. Plain REST, about 1.5 h, low risk. |
| Stretch: a realtime Grok agent that asks clarifying questions and calls `submit_report` | Grok realtime | Better UX. Adds ephemeral tokens and Web Audio PCM handling (about +2 to 3 h; Wi-Fi risk). |

**If time collapses:**
- Keep Grok. It is a hard requirement and costs about 1.5 h.
- Shrink the Cartesia agent to a one-way briefing call.
- Dropping Cartesia entirely loses the phone-ring moment, our strongest live interaction.

## 3. SpacetimeDB vs Convex (prize value)

| | SpacetimeDB | Convex |
|---|---|---|
| Value | 3 winners, $3,584 total | 1 winner, $5,000 (Hive) or $1,308 (Devpost) |
| Criteria | "Core real-time backend"; names "shared ops dashboard", "shared simulations", "AI agents coordinating in persistent world state" | None published |
| Fit | Near-verbatim description of a common operating picture | Generic |
| Competition (judgment) | Presenting sponsor, many entries, mostly games or chat. A disaster operating picture stands out. | Default backend for many Next.js teams; one slot |
| Python worker | No official Python client (unverified). HTTP reducer call or a TypeScript bridge; the technical agent decides. | Official Python client (unverified here) |

**Expected value (judgment):**
- SpacetimeDB: about 20 to 25% × an average of about $1,200, roughly $250 to $300.
- Convex: about 5 to 8% × $5,000 is roughly $250 to $400, or about $100 if the value is $1,308.

The dollars are a wash. **Pick SpacetimeDB** for criteria fit, three slots and judge perception.

**Use exactly one.** If both appear, each sponsor's judges will see the other as the real core. Fall back to Convex only if the technical agent finds a blocker in the Python bridge; then confirm the $5,000 at the booth and drop SpacetimeDB entirely. Both workshops are at Sat 4 PM, so attend SpacetimeDB's.

To read as "core", the demo should show two browsers plus the phone in sync, and a phone acknowledgement flipping the dashboard live. The README lists the tables and reducers (`forecast_cycle`, `zone_risk`, `observation`, `alert`, `ack`, `agent_action`).

## 4. Which model does which job

| Job | Model | Rationale |
|---|---|---|
| Briefing writer (ranked table to brief with act-by times) | **Claude** | Anthropic criterion 4: Claude does something that matters, and we know its failure modes. |
| Ground-report parser (transcript to Observation JSON) | **Claude** | Same job family. Keeps Grok as speech I/O only. |
| Q&A brain inside the Cartesia call | **Claude** via Cartesia's catalogue (Anthropic listed; exact models unverified) | One model and one set of guardrails for all language reasoning. |
| Camera frame to fire/smoke bounding box to bearing | **Gemini Flash** | Returns `box_2d` normalized to 0 to 1000 natively (verified). Box x-center plus heading and field of view gives the bearing. A real technical reason; also qualifies for MLH Gemini and Gen AI. |
| Snowflake Cortex, Bitdeer, Grok as a text LLM | none | Would duplicate Claude. |

**Claude failure modes for the README:**
1. **Altered numbers or times.** Mitigation: input is the table JSON only, and every claim must cite a row ID. A deterministic validator checks every time, zone and percentage against the table, and falls back to a template on failure.
2. **Over-confidence.** Mitigation: fixed probability-to-phrase mapping, with assumptions stated.
3. **Stale data.** Mitigation: the brief opens with the forecast's age and feed status.
4. **Mis-geocoded reports.** Mitigation: echo the parsed point back to the reporter, start with low trust and a large radius.
5. **Making the call.** Mitigation: Claude has no tool that issues orders; a human approves on the dashboard.

**Named community for Anthropic:** narrow, e.g. "LA County / Altadena-area incident commanders and OES duty officers on wind-driven night fires." Anthropic's criteria ask for a problem you lived or heard first-hand. **Do not fabricate a source.** If none exists, cite public after-action reporting honestly, or deprioritize this prize.

## 5. Compute and data

- **Vultr: add-on, about 1 h.** We need a public HTTPS host regardless: Cartesia webhooks, the Claude and Grok proxy, the QR page, and the live-mode worker. One CPU VM with Docker Compose and Caddy on the $100 credit. No GPU unless the ensemble is too slow on CPU; new-account GPU quotas are often restricted (unverified).
- **DigitalOcean: skip.** It would be a second host.
- **Laptop:** remains the offline replay fallback.
- **Tiger Data: skip.** Its natural job (archive of observations, snapshots and metrics) is a second database next to SpacetimeDB. It adds a network dependency to the replay, and the prize is small. Cached snapshot files already serve as the archive.

## 6. Scraping: Apify vs Bright Data

**Bright Data: skip.** Its rule is "if live public web data were removed, the product would stop working." Our core inputs come from satellite, weather, terrain and fuel APIs, not scraped pages, and the demo is a replay. The disqualification clause for "mocked/hardcoded data" is risky given the simulated people and cameras. A live-mode side panel would not make Bright Data essential.

**Apify: add-on with high expected value.** Six slots ($2,000 / $1,000 / $800 / $400 / $400, plus Best Actor $400) and loose criteria.

- **Honest job: "Official Evacuation Zones & Orders" Actor.**
  - Input: a bounding box or county list.
  - Sources: public county evacuation-zone ArcGIS FeatureServers (Napa publishes one), public Genasys Protect zone pages (check terms first), CAL FIRE incidents JSON, InciWeb.
  - Output: a normalized dataset with `zone_id`, polygon, `status` (order, warning or none), `updated_at`, `source_url`.
- **Why it is not bolted on:**
  1. **Zone polygons are the units of our risk layer.** We need them anyway, so the Actor replaces work.
  2. **Live mode** shows "Emberline: act by 23:40 · Official: no order".
  3. **Replay:** official order times give the honest "flagged at X / acted at Y" number. Historical Eaton statuses may need public after-action timelines (unverified).
- **Not duplicating the Store:** existing Store Actors already cover NIFC and InciWeb incidents. Zones and statuses are the new contribution, which supports Best Actor.
- Schedule it every 5 to 15 minutes in live mode, with a webhook into the backend.
- Estimate: about 3 h for Cody. Whether it must be published to the Store is unverified; ask at the 11 AM workshop.

## 7. Cheap add-ons

| Add-on | Verdict | Notes |
|---|---|---|
| Microsoft Built for the Real World ($3,800 + Surfaces) | **Core target, no tool** | Criteria map onto README and pitch: customer (IC/OES), measurable usefulness (lead time), path to production (replay-as-live, so live mode is the same code), responsible design (human approval, visible weights, LLM never decides, real vs simulated labelled), end to end. Attend the 10 AM workshop. |
| Cal Hacks Overall / Technically Complex / People-First | Core framing | Planet-First is a weaker fit; enter only if uncapped. |
| Commit Fellowship | Do it (15 min) | Opt in plus form. |
| .Tech domain (e.g. emberline.tech) | Do it (20 to 30 min) | Gives a clean QR URL and HTTPS name. Pick .Tech **or** GoDaddy, not both. |
| Notability | Conditional | Only if a teammate already writes on an iPad (Abby's Rothermel and filter derivations). |
| Figma | Conditional | Only if Linda's mockup is in Figma and she really hands it to code (Figma agent or MCP into Cursor). About 1 to 1.5 h for Slides and the stage log. |
| Mana ($5,000 on Hive) | Check the booth first | Use only if it owns a real job without overlapping Grok Bot (planning). |
| Factory Droid | Skip | An honest role exists: an "incident onboarding" Droid triggered by a new hotspot cluster or a "Track this fire" click, restricted to our MCP tools (`fetch_dem`, `fetch_fuels`, `fetch_hrrr`, `validate_grid`, `write_scenario`), with steps streamed to the dashboard. But it costs 3 to 4 h, and judges will ask why it isn't a script. |
| Fetch.ai | Skip (stretch only after 3 AM Sunday) | The ASI:One-only workflow makes it a parallel chat product. It could reuse the query API built for the Cartesia webhooks (about 2 to 3 h). Heavy competition. |

## 8. Prize matrix

Hours are incremental beyond the base product.

| Prize | Value | Criteria (short) | What we build | Est. h | Overlaps / conflicts | Demo visibility | Rec. |
|---|---|---|---|---|---|---|---|
| Cal Hacks Overall / Tech / People-First | Laptops, keyboards, Watches | App, intent, function, creativity, complexity | The product | 0 | none | all | **CORE** |
| Microsoft | $3,800 + Surfaces | Customer, metric, production path, responsible, end to end | README + pitch | 1 | none | high | **CORE** |
| SpacetimeDB | $2,048 / 1,024 / 512 | Core realtime backend | All operating-picture state and subscriptions | 0 (it is the backend) | Excludes Convex | high | **CORE** |
| SpaceXAI | $400? + keyboards | Cursor, Grok Voice, space data, Grok Bot | Grok STT/TTS reports, Cursor, Grok Bot plan | 1.5 | Split with Cartesia | med-high | **CORE** |
| Anthropic | TBA | Narrow community, first-hand problem, works, thoughtful | Brief + parser + validator | 0.5 | none | medium | **CORE** (needs a genuine source) |
| Cartesia | $1,516? / glasses | Voice core | Outbound call, Q&A, acknowledgement | 2 to 3 | Replaces Twilio | **highest** | **CORE** |
| Apify + Best Actor | $2,000…$400 + $400 | Meaningful use; Actor built at event | Evacuation-zones Actor | 3 | Excludes Bright Data | medium | **ADD-ON (P1)** |
| MLH Gemini / Gen AI | swag | Use Gemini | Camera bearing detector | ~0 (camera sensor) | none | medium | **ADD-ON** |
| MLH Vultr | Tab5 | Build on Vultr | Host | 1 | Excludes DigitalOcean | low | **ADD-ON** |
| MLH .Tech | mic + domain | Domain | emberline.tech | 0.3 | Excludes GoDaddy | low | **ADD-ON** |
| MLH A-Team | keyboard raffle | Cursor + Grok Bot described, opt in | Description text | 0.2 | none | none | **ADD-ON** |
| Commit | interview | Opt in + form | Form | 0.25 | none | none | **ADD-ON** |
| Notability / Figma / Mana | $396 / $2k×2 / $5k | Process artifacts | Only if already true | 0 to 1.5 | Mana vs Grok Bot | none | **CONDITIONAL** |
| Convex | $5k or $1.3k | none published | none | n/a | Duplicates SpacetimeDB | n/a | SKIP |
| Bright Data | $1.5k credits + $500 | Live web essential | none | n/a | Replay; disqualification risk | n/a | SKIP |
| Tiger Data | Stream Deck Mini | Build with Tiger | none | n/a | Second database | n/a | SKIP |
| Fetch.ai | $1,250… | ASI:One-only | none | n/a | Parallel product | n/a | SKIP |
| Factory | $2,000 | Droid does real work | none | n/a | 3 to 4 h | n/a | SKIP |
| ElevenLabs, Grok Imagine, Snowflake, Bitdeer, DigitalOcean, MongoDB, Auth0, Atlassian, Hyphenate, Visa, CRS, Capital One, Coinbase, Meta, Creao, Moss, Elastic, Armadin | n/a | n/a | n/a | n/a | Duplicate a job or off-domain | n/a | SKIP |

**Cody's integration budget:**
- **P0:** Claude brief, parser and validator about 3 h; Cartesia about 2.5 h; Grok about 1.5 h; Vultr about 1 h. SpacetimeDB is scoped by the technical agent.
- **P1:** Apify about 3 h; Gemini about 1.5 h (shared with the camera-sensor work).
- **P2:** domain, Commit and descriptions, about 1 h.
- **Total about 13 to 14 h. Cut Apify first if behind.**

## 9. Eligibility checklist

**Devpost**
- Image, repo, and a video of about 3:00 (fits both Figma's ≤3 min and Fetch.ai's 3 to 5 min).
- Demo-first: the phone rings and the forecast shifts within the first 60 s.
- Tick every relevant prize box. If there is a cap, the order is: Overall/Tech/People-First → Microsoft → SpacetimeDB → Cartesia → Anthropic → Apify → SpaceXAI → MLH.

**README sections**
- Who it's for (named community); problem (first-hand)
- Architecture (one box per sponsor tool, one job each)
- Real vs simulated data table; space data in (GOES/VIIRS)
- Run instructions for replay and live modes
- Where Claude can go wrong; responsible design (human approves, no automatic public alerts); path to production
- Cartesia (setup, features used, limitations)
- SpacetimeDB tables and reducers; Grok Voice usage; Gemini bearing; Apify Actor link
- Limitations; `.env.example` and no secrets

**Per prize**
- **SpaceXAI:**
  - Get the X Premium codes at the SpaceXSI booth.
  - **Confirm there that Grok STT/TTS counts as the "Voice API"** and ask about API credits.
  - Keep `.cursor/` rules and commit history; screenshot Cursor usage near the deadline.
  - Save Grok Bot planning screenshots in `docs/process/`.
- **MLH A-Team:** describe Cursor and Grok Bot on Devpost; opt into the raffle (mechanics unverified).
- **SpacetimeDB:** ask at the booth or the 4 PM workshop how to enter, since it is not on Devpost.
- **Cartesia:**
  - Redeem CARTESIACALHACKS at play.cartesia.ai and test-call a teammate's phone on Saturday morning.
  - README notes for privacy: no recordings stored, and numbers used only for the alert.
  - Prepare the 2-minute demo-first presentation.
- **Anthropic:** community, first-hand problem and the failure-modes section in both the README and the pitch. Attend the 1 PM workshop.
- **Microsoft:** README sections mirroring the five criteria, with the lead-time number ready.
- **Apify:** Actor link, git history showing it was built at the event, and the 11 AM workshop for the Best Actor rules.
- **MLH Gemini / Vultr / .Tech:** claim the codes, deploy on emberline.tech, opt in.
- **Commit:** checkbox plus form.
- **Conditional (Notability, Figma, Mana):** public artifact links. Figma also needs Figma Slides, the stage log and EDU verification.

## Open questions for Saturday booths

1. Is there a prize cap?
2. Does Grok STT/TTS count as the "Voice API", and are xAI API credits available?
3. How do we enter SpacetimeDB?
4. Is Convex $5,000 or $1,308?
5. What does CARTESIACALHACKS unlock, and can the free plan dial out?
6. Must the Apify Best Actor be published to the Store?
7. What is Mana?

## Sources

- Devpost: https://cal-hacks-13-0.devpost.com/ , https://cal-hacks-13-0.devpost.com/rules
- Hive JSON: https://hive.hackberkeley.org/api/live/prizes , /api/live/faq , /api/live/resources
- Cartesia: guide (Google Doc 1lBbGq4zbjx4tcgYP2WmcHu6w_E_N39BC); https://docs.cartesia.ai/calhacks ; https://docs.cartesia.ai/line/integrations/telephony/outbound-dialing.md ; https://docs.cartesia.ai/line/integrations/telephony/cartesia-numbers.md ; https://docs.cartesia.ai/agents/webhook-tools.md ; https://docs.cartesia.ai/agents/models.md ; https://docs.cartesia.ai/pricing.md
- xAI: https://docs.x.ai/llms.txt ; https://docs.x.ai/developers/model-capabilities/audio/text-to-speech.md ; https://docs.x.ai/developers/model-capabilities/audio/speech-to-text.md ; https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech.md ; https://docs.x.ai/developers/model-capabilities/audio/speech-to-speech/sip.md ; https://docs.x.ai/cookbook/examples/voice-agent-phone.md ; https://docs.x.ai/developers/pricing ; https://x.ai/bot
- Twilio trial limits: https://www.twilio.com/docs/messaging/guides/how-to-work-with-your-twilio-free-trial-account-us-only
- Fetch.ai: https://www.fetch.ai/events/calhacks-13-0 ; https://www.fetch.ai/events/hackathons/calhacks-13-0/hackpack
- MLH: https://www.mlh.com/events/prizes ; https://www.mlh.com/partners/vultr ; https://www.mlh.com/partners/tigerdata
- Factory: https://docs.factory.com/sdk/python
- Google Docs read: Figma (1HjzThDGveSLiilIdEPEHq12ZVLZ5eS0T8CQC5_m_-rg), Atlassian (12Y2Vamxbm9W_l2Eo4AM530R7HfNAYCXI-UI6F6Xnggw), Context.dev (1CM6dcuojhrRtcokWgspJ8ELeS864UVAneW83eQLliw4), Canva (1K7Yr2nThpxLt6mALIyIKlaW_fmTacVCFudH-TFM47rA). The Factory doc export failed.
- Gemini boxes: https://ai.google.dev/gemini-api/docs/image-understanding
- Apify Store wildfire Actors: https://apify.com/parseforge/nifc-wildfire-incidents-scraper , https://apify.com/crawlerbros/inciweb-wildfire-incident-scraper
- County evacuation-zone layer example: https://gis.napacounty.gov/arcgis/rest/services/Hosted/EvacuationZones_Public/FeatureServer
- Unreadable (rendered in JavaScript or Drive): mana.space/a/calhacks, mlh.link/spacexsi, the Apify Drive folder, the Bitdeer file.
