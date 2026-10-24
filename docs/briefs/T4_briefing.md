# T4: Claude briefing writer + number validator (`ai/briefing.py`, `ai/validate.py`)

**Goal.** When an alert is proposed, Claude rewrites its template text into a better briefing. Claude **never produces numbers**: a pure-function validator checks that every number, time, percentage and zone ID in Claude's output appears in the input. Anything that fails falls back to the template. The fallback is a feature, so make it obvious and solid.

**Follow `docs/briefs/README.md` §2 for all general rules.** Branch: `c-t4-briefing`.

## Read
- `spec/05_ai_voice.md` §1 (all of it), §4 "Agent system prompt" (you only store a copy), §8 tests 1–2, §9
- `spec/03_engine.md` §8 "Templates" (the `p_words` table)
- `spec/00_contracts.md` §3.1: `Alert`, `ZoneRisk`, `Cycle`, `SensorInfo`, `Briefing`
- `AGENTS.md`, the "Claude API" rule

## You own
- `engine/emberline/ai/__init__.py` (empty)
- `engine/emberline/ai/briefing.py`, `engine/emberline/ai/validate.py`
- `engine/emberline/ai/prompts/briefing_v1.md`, `engine/emberline/ai/prompts/cartesia_agent_v1.md`
- `engine/tests/test_validate.py`, `engine/tests/test_briefing.py`, `engine/tests/data/briefing_inputs/*.json`

## Claude API facts (checked Oct 24; don't "fix" these from memory)
- **Model `claude-sonnet-5-5`.** It **rejects** `temperature`, `top_p` and `top_k`, any forced `tool_choice` (`any`/`tool`), and assistant prefill. Each returns HTTP 400.
- **Lowest thinking setting:** `thinking={"type": "between_tools"}`. Put nothing else inside `thinking`. Don't set `output_config.effort` above `high`; leaving it out is fine.
- **Structured output** goes through `output_config`:
  ```python
  resp = await client.messages.create(
      model="claude-sonnet-5-5",
      max_tokens=2000,
      thinking={"type": "between_tools"},
      system=SYSTEM_PROMPT,
      messages=[{"role": "user", "content": json.dumps(inp, ensure_ascii=False)}],
      output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
  )
  if resp.stop_reason == "refusal": ...            # retry once, then template
  text = next(b.text for b in resp.content if b.type == "text")   # select blocks BY TYPE
  data = json.loads(text)
  ```
- **`SCHEMA`** is a plain JSON schema: `{"type": "object", "properties": {"headline": {"type": "string"}, "bullets": {"type": "array", "items": {"type": "string"}}, "text": {"type": "string"}, "voice_script": {"type": "string"}}, "required": [all four], "additionalProperties": False}`.
  - Enforce the length limits in the validator, not in the schema.
- **Client:** `anthropic.AsyncAnthropic(timeout=20.0)`. It reads `ANTHROPIC_API_KEY` from the environment. Load `engine/.env` with a tiny loader (it reads `KEY=VALUE` lines and doesn't override variables already set); don't add `python-dotenv` just for this.
- **Errors:** catch `anthropic.APIStatusError` and `anthropic.APIConnectionError`. On any error, do one retry for a refusal or for malformed JSON, then fall back.

## Steps

1. `uv add anthropic`
2. **`ai/prompts/briefing_v1.md`.** Write the system prompt from the bullets in `05` §1 "System prompt". Every bullet becomes an instruction; add nothing that conflicts.
3. **`ai/prompts/cartesia_agent_v1.md`.** Copy the bullets from `05` §4 "Agent system prompt" as a markdown list. It's only stored here; T11 uses it.
4. **`ai/briefing.py`:**
   ```python
   MODEL = "claude-sonnet-5-5"
   PROMPT_VERSION = "briefing_v1"
   def build_input(alert: Alert, risks: list[ZoneRisk], zone_names: dict[str, str],
                   egress_roads: dict[str, int], critical_types: dict[str, list[str]],
                   cycle: Cycle, sensors: list[SensorInfo], k_ms: int) -> dict
   def input_hash(inp: dict) -> str            # sha256 hex of json.dumps(inp, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
   async def draft(inp: dict, alert: Alert, cycle: Cycle, client=None) -> Briefing   # NEVER raises
   ```
   - **`build_input`** produces exactly the JSON shape in `05` §1 "Input". Rules for each field:
     - **Time strings:** `issued_pst` and `data_through_pst` are `HH:MM` PST from `k_ms` and `cycle.data_through_sim_ms`. `forecast_age_min = round((k_ms − cycle.knowledge_sim_ms)/60000)`.
     - **`alert`:** `alert.level` and `alert.rule_text`.
     - **`zones`:** one entry per `alert.zone_ids`, in that order:
       - `p_60`, `p_120` (from `p_by_lead` via `lead_idx`), rounded to 2 decimals;
       - `eta_q50_pst`, `act_by_pst` (or null);
       - `evac_min`, `population`;
       - `critical`: **types only**, e.g. `["nursing home"]`, never names;
       - `egress_roads`, `trend`.
     - **`fire`:** `burned_ha`, `growth_ha_per_h` and `head_bearing_deg` from the cycle, rounded to whole numbers.
     - **`sensors`:** name, last-scan PST and status.
     - **`honesty`:** the literal string from the spec.
     - Assert that `len(json.dumps(inp)) ≤ 4096`; if it's bigger, drop zones after the first 3.
   - **`draft`:**
     1. Call Claude as above; time it.
     2. On success, run `validate(data, inp, alert.level)`.
     3. If it validates, return a `Briefing` with `grounded=True`, `fallback_used=False`, and Claude's fields.
     4. **On any failure** (exception, refusal twice, bad JSON, validation failure), return a `Briefing` with `grounded=False`, `fallback_used=True`, and `unmatched_tokens` = the validator's list (or `["error: <type>"]`). Use `headline = alert.text[:90]`, `bullets = []`, `text = alert.text` and `voice_script = alert.voice_script` (the **template** already in the alert).
     5. Always set:
        - `briefing_id = f"brf_{alert.alert_id}"`
        - `model = MODEL`
        - `prompt_version = PROMPT_VERSION`
        - `input_hash`, `latency_ms`, `created_wall_ms = now`
        - the scenario/branch/cycle/alert IDs
   - **CLI** for Cody's one-real-call check:
     ```bash
     uv run python -m emberline.ai.briefing --fixture-cycle 20
     ```
     It builds the input from the fixture bundle of the 20th cycle (its first proposed alert, its `zone_risk`, its `cycle`, its `sensors`, plus zone names, egress and critical types from `fixtures/static/fixture/*`), prints the input, calls `draft`, and prints the Briefing JSON plus `VALIDATED` or `FALLBACK: <tokens>`.
5. **`ai/validate.py`.** Pure functions; the most important file in this task.
   ```python
   BANNED = ("will reach", "will burn", "guaranteed", "certain", "would have saved", "evacuate now")
   def p_words(p: float) -> str        # 0.9 very likely / 0.7 likely / 0.5 more likely than not / 0.3 possible / else unlikely
   def extract_tokens(s: str) -> list[str]
   def allowed_tokens(inp: dict) -> set[str]
   def validate(out: dict, inp: dict, level: str) -> tuple[bool, list[str]]   # (ok, unmatched/failed items)
   ```
   - **`extract_tokens`** runs in this order. After each step, replace the matched spans with spaces so they aren't counted twice.
     1. Times `\b\d{1,2}:\d{2}(?:\s?[AaPp]\.?[Mm]\.?)?`, normalised to 24 h `HH:MM` (so `11:40 PM` → `23:40` and `9:05` → `09:05`).
     2. Percentages `\b\d{1,3}(?:\.\d+)?\s?%`, normalised to `"<int>%"`.
     3. Zone IDs `\b[A-Z][A-Z0-9]+-[A-Z0-9-]+\b`.
     4. Numbers `\b\d[\d,]*(?:\.\d+)?\b`, normalised by removing commas and stripping trailing `.0`.
   - **`allowed_tokens(inp)`:** walk the input JSON recursively.
     - Every string value goes through `extract_tokens`.
     - Every number `x`: add `str(x)` normalised. If `0 ≤ x ≤ 1`, also add `f"{round(x*100)}%"` and `str(round(x*100))`, because `0.54` ≡ `54%`. For ints, also add the comma form.
     - Every zone ID and zone name.
     - The literal year `2025`.
   - **`validate`:**
     1. **Every token** from every output field (headline, each bullet, text, voice_script) must be in `allowed_tokens(inp)`. The one exception: plain integers ≤ 10 are allowed. Collect the misses.
     2. **Probability words.** Find the phrases from `P_WORDS` plus `unlikely` in the lowercased output, matching the **longest phrase first** and then blanking it, so `very likely` and `unlikely` aren't also counted as `likely`. Every phrase found must equal `p_words(z["p_60"])` or `p_words(z["p_120"])` for some zone in the input.
     3. **Banned phrases,** case-insensitive. `evacuate now` is allowed only when `level == "order_recommended"`.
     4. **Prefix rules:**
        - `text` starts with `"DEMO REPLAY (Jan 2025)."`;
        - `voice_script` starts with `"This is an Emberline demo replay of January 2025."`;
        - `voice_script` ends with `"You make the call."` (ignore trailing whitespace).
     5. **Lengths:**
        - headline ≤ 90 chars;
        - ≤ 4 bullets, each ≤ 120 chars;
        - text ≤ 300 chars;
        - voice ≤ 110 words (`len(s.split())`).

     Return `(len(failures) == 0, failures)`, where each failure is a short string such as `"time 23:45"`, `"pword likely for FIX-Z2"`, `"banned: will reach"` or `"len text 312"`.
6. **`engine/tests/test_validate.py`:** the **12 table-driven cases** from `05` §8 test 1, built from one fixture-derived input.
   - **One correct output → accepted.**
   - **Each of these → rejected with the right failure string:**
     - an altered time;
     - an altered percentage;
     - an altered population;
     - an invented zone ID;
     - a wrong probability word;
     - `will reach`;
     - `evacuate now` at warning level;
     - a missing DEMO prefix;
     - text over 300 chars;
     - voice over 110 words;
     - 5 bullets.
   - **Extra cases:**
     - `11:40 PM` matches `23:40`;
     - `54%` matches 0.54;
     - `2,140` matches 2140;
     - "two roads" and "3 roads" pass (small ints);
     - `very likely` isn't misread as `likely`.
7. **`engine/tests/test_briefing.py`:**
   - **Offline:**
     - `build_input` on the fixture's cycle-20 alert has the right keys, critical lists contain types only, and the size is ≤ 4 KB.
     - `input_hash` is stable across dict key order.
     - `draft` with a **fake client** (an object whose `messages.create` is an async function returning a stub response with `.stop_reason` and `.content = [SimpleNamespace(type="text", text=json.dumps(...))]`):
       - a valid output → `grounded=True`;
       - an output with a wrong time → `fallback_used=True` with the template text;
       - a client that raises → `fallback_used=True`, and no exception escapes.
   - **Smoke** (`05` §8 test 2; only when `EMBERLINE_LIVE_TESTS=1` and `ANTHROPIC_API_KEY` are set):
     - 3 inputs from the fixture's proposed alerts (cycles 18, 20 and 30), saved as `engine/tests/data/briefing_inputs/c18.json`, `c20.json` and `c30.json` so they're reviewable;
     - send them to real Claude and assert `grounded` on ≥ 2 of 3;
     - print the failures for the rest.
8. Run `uv run pytest -q`. If you have a key, also run `EMBERLINE_LIVE_TESTS=1 uv run pytest -q tests/test_briefing.py` and the CLI. Paste both outputs.

## Done when
- The 12 validator cases plus the extras pass, and the offline briefing tests pass.
- If a key was available, the live smoke test reached ≥ 2/3 grounded, and the CLI output is in the PR body.
- If no key was available, the PR says so plainly.

## Don't
- Let Claude compute or round any number. All rounding happens in `build_input`.
- Raise out of `draft`. The engine loop must never crash because of Claude.
- Call `upsert_alert_draft` or touch the hub. Wave-2 T10 does that.
- Add sampling parameters, a forced tool or prefill (each is a 400).
