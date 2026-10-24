# T3: zone risk + frozen alert rules (`risk/risk.py`, `risk/alerts.py`)

**Goal.** Two pure-function modules, with no I/O beyond reading one YAML file. Together they turn ensemble arrival times into what the operator sees:
- `risk/risk.py` turns ensemble arrival times into ranked `ZoneRisk` rows.
- `risk/alerts.py` turns those rows into alert proposals under the frozen rules, with template text.

The publisher (wave-2 T10) calls them every cycle. They are on the M1 critical path.

**Follow `docs/briefs/README.md` §2 for all general rules.** Branch: `c-t3-risk-alerts`.

## Read
- `spec/03_engine.md` §7 ("Zone risk" and "People") and §8 (Alerts)
- `spec/00_contracts.md`:
  - §3.1: `LEADS_MIN`, `lead_idx`, `ZoneRisk`, `Alert`, `Person`
  - §3.3: `ZoneFeatureProps`
  - §7: the frozen alert rules YAML
- **`engine/emberline/scripts/make_fixture.py` lines 179–700.** The fixture already implements this logic for one simple case (equal weights, one zone per alert). **Your output must agree with it on the fixture** (step 6). Read it, match its wording and semantics, but **don't import from it and don't edit it.**

## You own
- `engine/emberline/risk/__init__.py` (empty, 0 bytes)
- `engine/emberline/risk/risk.py`
- `engine/emberline/risk/alerts.py`
- `config/alert_rules.yaml`
- `engine/tests/test_risk.py`, `engine/tests/test_alerts.py`

Don't create `risk/metrics.py`; that's Abby's.

## Steps

1. `uv add pyyaml numpy` (no-ops if already present).
2. **`config/alert_rules.yaml`:** copy the YAML block in `spec/00_contracts.md` §7 **exactly**.
3. **`risk/risk.py`:**
   ```python
   SEVERITY = {"order": 3, "warning": 2, "watch": 1, "clear": 0}
   TIER_MULT = {1: 2.0, 2: 1.3, 3: 1.0}

   def p_at(p_by_lead: list[float], lead_min: int) -> float          # p_by_lead[lead_idx(lead_min)]; NEVER index by minutes
   def zone_probs(arr_min: np.ndarray, w: np.ndarray, k_min: float) -> list[float]   # 12 values, P(A <= k_min + LEADS_MIN[i]) weighted
   def eta_quantile(arr_min: np.ndarray, w: np.ndarray, q: float, k_min: float, horizon_min: int) -> float | None
   def zone_risks(
       zone_arr: np.ndarray,            # (N, Z) float32, minutes since t_ref; np.inf = never
       weights: np.ndarray,             # (N,) normalized
       k_ms: int, t_ref_ms: int,
       zones: list[dict],               # ZoneFeatureProps dicts, in the SAME column order as zone_arr
       evac_min: dict[str, float],      # current hub values (operator edits)
       prev_p120: dict[str, float] | None,   # previous cycle on this lineage; None = first cycle
       critical_types: dict[str, list[str]], # zone_id -> e.g. ["nursing home", "school"] from assets.geojson `type`
       open_r2_zones: set[str],
       cycle_id: str, scenario_id: str, branch_id: str,
       horizon_min: int = 360, act_by_q: float = 0.3,
   ) -> list[ZoneRisk]                  # ranked, rank starts at 1
   def apply_open_orders(risks: list[ZoneRisk], open_r2_zones: set[str]) -> list[ZoneRisk]   # status -> "order", then re-rank
   def rank(risks: list[ZoneRisk]) -> list[ZoneRisk]
   def people(persons: list[Person], risks: list[ZoneRisk]) -> list[Person]   # copy p_60, eta_q50_sim_ms, act_by_sim_ms from the person's zone; return ONLY persons whose values changed
   ```
   Rules, exactly per `03` §7:
   - **Time conversion.** `k_min = (k_ms − t_ref_ms) / 60000`. A member arrival `A` (minutes) maps to `t_ref_ms + round(A·60000)` ms.
   - **`zone_probs[i]`** = Σ `w[m]` over members with `A[m] ≤ k_min + LEADS_MIN[i]`. Round to 6 decimals.
   - **`eta_quantile`:** sort the members by A and walk the cumulative weights; it is the first A whose cumulative weight is ≥ q (use `q − 1e-9` to dodge float error). Return `None` if that A is `inf` or `> k_min + horizon_min`.
   - **ETA fields.** `eta_q10/q50/q90_sim_ms` come from `eta_quantile` converted to ms.
   - **Act-by.** `act_by_sim_ms = eta_q30_ms − evac·60000` (None if eta_q30 is None). `act_by_basis = f"q30 arrival − {evac:g} min evac"`. `evac` comes from `evac_min[zone_id]`, falling back to `zone["evac_min_default"]`.
   - **Status:**
     - `order` if `p_at(60) ≥ 0.5` **or** the zone is in `open_r2_zones`;
     - else `warning` if `p_at(120) ≥ 0.3`;
     - else `watch` if `p_at(360) ≥ 0.1`;
     - else `clear`.
   - **Score.** `risk_score = p_at(120) · (1 + log10(1 + population)) · TIER_MULT[tier]`, rounded to 6 decimals.
   - **Rank** sorts by status severity descending, then `act_by` ascending (None last), then `risk_score` descending.
   - **Trend.** If `prev_p120` is None or doesn't contain the zone, `flat`. Otherwise it's `up` if the delta is > 0.05, `down` if < −0.05, else `flat`.
   - **Drivers** (at most 3, in this order):
     - `f"P {p120*100:.0f}% by +2h"`;
     - then each critical type (e.g. `"nursing home"`), never a facility name;
     - then `f"{egress_roads} egress road"` if `egress_roads ≤ 1`.
   - `population`, `housing`, `n_critical` and `tier` are copied from the zone props.
4. **`risk/alerts.py`:**
   ```python
   ACTIVE = {"proposed", "approved", "calling", "called", "acknowledged"}
   P_WORDS = ((0.9, "very likely"), (0.7, "likely"), (0.5, "more likely than not"), (0.3, "possible"))
   def p_words(p: float) -> str                       # falls through to "unlikely"
   def load_rules(path: Path = REPO / "config" / "alert_rules.yaml") -> dict
   @dataclass
   class AlertResult:
       new: list[Alert]          # newly proposed (state "proposed")
       superseded: list[Alert]   # existing hub alerts moved proposed -> superseded (copies with state changed)
       @property
       def all(self) -> list[Alert]: return self.new + self.superseded
   def evaluate(
       risks: list[ZoneRisk],         # this cycle, ranked
       hub_alerts: list[Alert],       # every alert on this lineage so far, any state (mirrored from the hub)
       k_ms: int, cycle_id: str, scenario_id: str, branch_id: str,
       rules: dict, zone_names: dict[str, str], critical_types: dict[str, list[str]],
       data_through_ms: int,
   ) -> AlertResult
   def template(level, zones: list[ZoneRisk], zone_names, critical_types, p: float, lead_h: int,
                act_by_ms: int | None, k_ms: int, data_through_ms: int) -> tuple[str, str]   # (text, voice_script)
   ```
   Semantics. These match the fixture's `propose()`, generalised to merges.
   - **Rule tests per zone** (skip zones with `population < rules["min_population"]`):
     - `R1_WARNING` holds when `p_at(120) ≥ 0.30`.
     - `R2_ORDER` holds when `p_at(60) ≥ 0.50`, **or** when `act_by` is not None and `act_by − k_ms ≤ 15·60000` (negative counts too).
     - Read thresholds, leads and `act_by_within_min` from `rules`; don't hard-code them.
   - **Blocking.** A zone/level is blocked, and gets no new proposal, when any alert in `hub_alerts` plus this cycle's new ones has the zone in `zone_ids` and either:
     - (a) the same level and a state in `ACTIVE`; or
     - (b) the same level and `k_ms − triggered_sim_ms < cooldown_min·60000`, whatever its state; or
     - (c) the level being tested is `warning`, and an `order_recommended` alert for the zone is in `ACTIVE`.
   - **Merge.** Group the unblocked zones that fired the same rule this cycle. If a group has ≥ `merge_min_zones` (3) zones, emit **one** alert. Otherwise emit one alert per zone.
     - `zone_ids` are in risk-rank order.
     - The alert uses `zone_ids[0]` for `alert_id`, `act_by_sim_ms` and P.
   - **`alert_id`** = `f"al_{branch_id}_{zone_ids[0]}_{rule_id}_{k_ms}"`. `level` is `warning` for R1 and `order_recommended` for R2. `rule_text` comes from the yaml. `state` is `"proposed"` and `draft_source` is `"template"`.
   - **Supersede.** For each new `order_recommended` alert, every alert in `hub_alerts` that has `state == "proposed"`, `level == "warning"` and shares a zone → a copy with `state="superseded"` goes into `superseded`.
     - **Never touch any other state.**
     - If a warning and an order fire for the same zone in the **same** cycle, keep only the order: (c) blocks the warning once the order is added, so evaluate R2 before R1.
   - **Template (`03` §8 and the fixture's `alert_copy`):**
     - R1 uses `lead_h = 2` and `p = p_at(120)`; R2 uses `lead_h = 1` and `p = p_at(60)`.
     - `{LEVEL}` is `WARNING` or `EVACUATION ORDER`; `level_words` is `a warning` or `an evacuation order`.
     - Zones: in `text`, the zone IDs joined with `, `; in `voice`, the zone **names** joined in spoken form ("A, B and C").
     - `pop` is the sum over zones. Critical types are the union of `critical_types` (types only, never names), e.g. `", nursing home, school"`.
     - Times are PST `HH:MM` in `text`. In voice they're spoken: copy the fixture's `spoken()` behaviour by reimplementing it.
     - If `act_by` is None, use `unknown` / `an unknown time`.
     - `Forecast {k}` and `satellite data through {data_through_ms}`, both in PST.
     - Lengths: `text ≤ 300` chars and `voice ≤ 110` words. If a merge makes it longer, list the first 3 zones plus `"+N more"`.
5. **`engine/tests/test_risk.py`:**
   - Hand-built 4-member example with weights `[0.4, 0.3, 0.2, 0.1]`, arrivals `[10, 50, 100, inf]`, `k_min = 0`:
     - `zone_probs[lead_idx(60)] == 0.7` (members at 10 and 50);
     - `eta_quantile(q=0.3) == 10`;
     - `eta_quantile(q=0.5) == 50`;
     - `eta_quantile(q=0.95)` is None (inf).
   - Status thresholds at their exact boundaries: 0.5 / 0.3 / 0.1.
   - Ranking, including None act-by sorting last.
   - Trend at ±0.05 boundaries; `flat` on the first cycle.
   - Drivers never contain a facility name (pass `critical_types` with types only and assert the strings).
   - `people()` returns only the changed persons, with values equal to their zone's.
   - **Operator edit (`03` §14 test 5, the risk half).** The same `zone_arr` with `evac_min` 45 vs 90 → act-by differs by exactly 45 min.
6. **`engine/tests/test_alerts.py`:**
   - **`03` §14 test 4.** A synthetic sequence for one zone:
     - cycle A crosses R1 → proposed warning;
     - 10 min later it still holds → nothing (active/cooldown);
     - later, R2 holds → order proposed, and the proposed warning is returned in `superseded`.
     - Repeat with the warning **approved** (`state = "approved"` in `hub_alerts`): the order is still proposed, and the warning is **not** superseded.
   - **Merge.** 3 zones crossing R1 in one cycle → 1 alert with 3 ranked `zone_ids`. 2 zones → 2 alerts.
   - **Cooldown applies to merged zones.** Next cycle, none of the 3 can re-fire R1.
   - **`act_by_within_min`** with a negative value fires R2.
   - Template lengths hold for a 6-zone merge. Text starts with `"DEMO REPLAY (Jan 2025)."`; voice starts with `"This is an Emberline demo replay of January 2025."`.
   - **Fixture cross-check (important).** Load every cycle bundle of `fixtures/cache/fixture/main` in order (via `index.json`).
     - For each cycle, take its committed `zone_risk` rows as `risks`.
     - Run `evaluate(...)` with `hub_alerts` = all alerts produced so far by **your** `evaluate` (apply supersede transitions as you go), and `data_through_ms` = `bundle.cycle.data_through_sim_ms`.
     - Get zone names from `fixtures/static/fixture/zones.geojson` and critical types from `assets.geojson` (`type` by `zone_id`).
     - Assert that the list of `(cycle index, rule_id, zone_ids[0], state)` you produce equals the fixture's committed alerts, counting cycles from 1 and skipping the 3 pre-ignition bundles. That list is exactly:
       - `(18, R1_WARNING, FIX-Z3, proposed)`
       - `(20, R1_WARNING, FIX-Z2, proposed)`
       - `(30, R2_ORDER, FIX-Z3, proposed)`, `(30, R1_WARNING, FIX-Z3, superseded)`
       - `(32, R2_ORDER, FIX-Z2, proposed)`, `(32, R1_WARNING, FIX-Z2, superseded)`

     Don't compare text; the fixture's text uses a simplified data-through time.
7. `uv run pytest -q` passes.

## Done when
- Both test files pass, including the fixture cross-check.
- The whole suite is green.
- The PR body shows the cross-check result.

## Don't
- Touch the hub, files or Claude. These modules are pure.
- Format numbers with anything other than what the template says. The Claude validator (T4) checks that every number matches its input.
- Put facility names anywhere.
