# Eaton perimeter sources and validation fallback

Reviewed 2026-10-25 for incident **2025-CALAC-009087**, Los Angeles County,
California. This implements acquisition and a limited fallback, not independent
historical accuracy acceptance. Cody's `research/01_data_sources.md` §10 names
candidate sources; `data/truth.py` builds timeline context, not an overnight
perimeter archive installer.

## What the source checks found

| Source | Result | Permitted use now |
|---|---|---|
| [Cal OES FIRIS public view](https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0) | Current Eaton-name/mission query returned zero objects. | Retain the dated query result. It does not prove no historical archive exists. |
| [NIFC WFIGS perimeter layer](https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/WFIGS_Interagency_Perimeters/FeatureServer/0) | Exact incident ID returned one polygon, OBJECTID 53438. `poly_DateCurrent` is null; `poly_PolygonDateTime=1778604836894` is a 2026 timestamp. | Later perimeter context only. Discovery, creation, or edit time cannot be substituted for the polygon's overnight observation time. |
| [Eaton perimeter archive service](https://services8.arcgis.com/RQZoR4rGIZ8wcN0C/arcgis/rest/services/Eaton_Fire_Perimeter/FeatureServer) | Advertises only layer 6, `Eaton_Perimeter_20250121`. | Not a Jan 7–8 overnight truth sequence. |
| [FIRIS aircraft study](https://doi.org/10.3390/rs17243962) | Figure 1 dates its Eaton perimeter to Jan 8, 2025, 18:26 PST. Data/code are available by request. | A candidate for a separately configured later experiment. It falls outside this replay's possible forecast targets. |
| [LA County investigation report](https://file.lacounty.gov/SDSInter/lac/1208708_InvestigationReportoftheEatonFireEvacuationAlerts_05-18-26_.pdf) | Includes timestamped overnight FireGuard maps in Figures 3, 7, 13, 16, 18 and 21. Section 1 explains these are retrospective thermal-front images. | Promising independent evidence candidate; obtain original GIS data and sensor lineage. PDF screenshots alone are not ready-to-score burned masks. |
| NIROPS path suggested in research notes | The checked `ftp.wildfire.gov/public/incident_specific_data/calif_s/` path returned HTTP 404. | Archive retrieval remains unresolved; do not infer that all incident archives are absent. |
| [NASA FEDS metadata](https://gis.earthdata.nasa.gov/portal/home/item.html?id=e2e14a2a640b404c903976b2fb4467f8) | Describes VIIRS-derived, 12-hourly cumulative affected-area polygons, with nominal rather than exact overpass time. This particular item covers the current year, not a verified 2025 download. | A future withheld-sensor sensitivity study after obtaining the correct archive and tracing contributing detections. Not independent truth when the same VIIRS evidence is assimilated. |

The frozen replay ends **2025-01-08 15:00 UTC** with a 360-minute horizon;
its last possible target is **21:00 UTC**. The study's perimeter is **2025-01-09
02:26 UTC**, 5 h 26 min later. Do not extend forecasts, interpolate time, or relabel
the final perimeter silently to make it fit.

## Repeat the public-source download

From the repository root, with Python 3.12:

```sh
PYTHONPATH=engine python -m emberline.hazard.perimeter_sources \
  --out /tmp/eaton-perimeter-candidates-20261025
```

Use a new output directory for each run. No credentials are needed. The command
saves layer metadata, the complete object-ID inventory, GeoJSON pages and
`source-ledger.json` containing exact query URLs, retrieval time and SHA256s.
It limits response sizes, checks every page's object identities, and fails on
truncation, duplicate IDs or ArcGIS errors. Zero matches is a successful search
with zero candidates, not a truth-data pass. Downloads always retain
`truth_eligibility=requires_independent_source_review`.

The checked-in `docs/evidence/eaton-perimeter-source-review.json` records this
review's snapshot identities and dispositions. Public raw files are retained
outside tracked model inputs. Requerying a mutable service may produce new hashes.

## How we can proceed without full perimeters

1. Continue software regression, causal replay, checkpoint/branch checks, dashboard
   rehearsal, synthetic twin experiments and measured performance testing. These
   checks do not depend on a claim of independent forecast skill.
2. Run `hazard.event_consistency` on a **frozen issue checkpoint** and later positive
   detections. It excludes already-assimilated IDs, detections at/before issue,
   events beyond the forecast horizon and unsupported grid footprints. Retain all
   exclusions. It never converts absent detections or unknown/cloudy coverage into
   unburned cells.

   ```sh
   PYTHONPATH=engine python -m emberline.hazard.event_consistency \
     --scenario eaton --checkpoint /path/to/frozen-issue.npz \
     --out /tmp/eaton-positive-consistency.json
   ```

   The checkpoint must come from the causal Rust replay, not be recomputed after
   inspecting future detections. Predeclare issue/lead/sensor selection before
   comparing model variants. Keep checkpoint and event-table hashes. Multiple
   nearby pixels are correlated, not independent validation cases.
3. Interpret its two quantities narrowly. For member arrivals T, normalized member
   weights w, footprint cell centers S, and detection time t:

   `fraction = sum_m w_m * mean_(c in S) I[T_mc <= t]`

   `any_mass = sum_m w_m * I[exists c in S: T_mc <= t]`

   These describe geometric consistency with a later positive event. They are not
   sensor detection likelihoods, Brier scores, calibrated probabilities of fire,
   full-area IoU, perimeter error or arrival-time error. They cannot measure false
   positives. The report explicitly says `scientific_acceptance=not_established`.
4. Keep the independent historical gate open. Do not tune on this diagnostic and
   call the same events a held-out test. To test sensor independence, exclude an
   entire chosen sensor's observations from every forecast input, rerun matched
   modes, and audit IDs and source lineage. This remains a sensor study rather
   than full-perimeter validation.

## Turn the FireGuard candidate into usable validation

The strongest next acquisition is original, timestamped FireGuard GIS geometry
and its product/coverage description for Jan 7 18:18 PST through Jan 8 07:00 PST.
The county report identifies the California National Guard program via LA County.
No request has been sent. Seek the original observations and lineage, not a
reconstruction fitted to Emberline forecasts.

Before import, a reviewer must establish all of the following:

- Confirm incident identity, CRS, observation-valid time, time zone, publication
  time, revision/version and file checksum for each snapshot. Retrospective truth
  can be published later; it must never enter earlier forecast inputs.
- Determine whether each polygon denotes active thermal front, detected heat,
  cumulative affected area or mapped burned extent. Active-front polygons cannot
  be scored as complete cumulative burned area without a justified conversion.
- Obtain explicit surveyed/observable coverage and quality flags. Outside coverage
  remains unknown; a perimeter polygon's exterior is not automatically surveyed.
- Trace contributing sensors and analyst inputs against our assimilated GOES,
  VIIRS and documented reports. A different publisher alone does not establish
  independence. Record dependence or exclude shared inputs before rerunning.

If only PDF maps are obtainable, treat digitization as a separate reviewed data
product: preserve the original PDF/page/hash, register each panel to identifiable
control points, reserve independent check points, record registration residuals,
digitize only visible features, and propagate positional/time uncertainty. Do not
invent a 90 m precision claim, infer coverage from a screenshot border, or turn
highlighted heat into an entire burned mask. A second reviewer should approve
geometry and semantics before it becomes truth.

Once independently mapped burned extent **and explicit coverage** exist, run
`hazard.truth_import --help`, use the frozen grid JSON and explicit timestamps,
then follow `HISTORICAL_VALIDATION.md` to freeze matched persistence/none/reinit/pf
cases. The importer checks geometry, holes, CRS/domain and coverage; it cannot
verify the source's scientific independence for us. Keep the original data,
checksums and reviewer decision alongside the raster artifacts.

If none of these sources yields usable early Eaton truth, the defensible next
step is a separate incident with an accessible time-resolved IR archive. Choose
it before viewing model scores, build its own terrain/forcing/source manifest,
and freeze incident-disjoint splits. Eaton can remain a real-input demonstration
and qualitative case study; it cannot alone establish generalization.
