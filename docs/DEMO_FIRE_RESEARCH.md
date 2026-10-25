# Emberline demo fire: final recommendation (Sat Oct 24 2026, evening)

## 1. Verdict

**Keep Eaton as the headline replay.** The real data already supports detection, the second sensor, live model updates, the risk map and the real-world contrast (capabilities 1, 3, 4, 5 and 7). The phone-call lead time (6) can be measured. Eaton also has the best-documented contrast of any candidate: aircraft were grounded at 18:45 PST, LA County Fire had no access to satellite mapping, and west-Altadena orders went out at 03:25 (AAR https://file.lacounty.gov/SDSInter/bos/supdocs/207915.pdf; Citygate https://file.lacounty.gov/SDSInter/lac/1208708_InvestigationReportoftheEatonFireEvacuationAlerts_05-18-26_.pdf).

Its weak points are fixed facts, and we can label them rather than hide them:
- Humans reported the fire before any satellite saw it.
- No real camera or aerial imagery exists for that night.
- The westward run was driven by embers, and our model has no spotting.

**Palisades is the runner-up.** It is better for capabilities 1 and 2, but it adds 5–7 h of build time with about 12 h left.

The real blocker is not the choice of fire. It is the missing `static/dem.tif` and `static/fbfm40.tif`, with no `statics.py`. That blocks every candidate, so it has to start first.

## 2. Scoring matrix

Scale is 0–3, where 3 is best. For switching cost, 3 means cheapest. For honesty risk, 3 means lowest risk.

| Fire | 1 Sat. detects | 2 Ground/aerial report | 3 Second sensor sharpens | 4 Live update | 5 Risk map | 6 Call before real order | 7 Real-world SA/alert failure | Data avail. | Switch cost | Honesty risk |
|---|---|---|---|---|---|---|---|---|---|---|
| **Eaton 2025** | 2: GOES-18 meso ~18:24, but 911 came at 18:13 | 1: documented text reports only; any camera is simulated | 2: VIIRS 375 m reaches Lake Ave, available 02:59 by file-created time | 3: 5-min GOES plus HRRR already ingested | 3: real zones in repo | 2: plausible; area-X GOES hit 74 min before 03:25; unmeasured | 3: grounded aircraft, FireGuard not shared, contested 03:25 orders | 3: GOES, HRRR, zones, timeline in repo | 3: already built | 1: 19 deaths, contested reviews, litigation |
| Palisades 2025 | 2: camera ~10:24, G18 meso ~10:26, 911 at 10:29 | 3: real ALERTCalifornia plume (licence partly unclear) | 2: G18 meso, then G18 CONUS (+5), then G16 (+16); Sentinel-2 at 10:36 (latency unverified) | 3 | 2: same zones layer; mapping to AP neighbourhoods not done | 3: first order 12:07, ~40 min after homes burned (AP) | 2: alerting, staffing and egress failures; no wrong-forecast story | 2: needs day-7 GOES, new grid, statics, HRRR crop | 1: +5–7 h | 1: 12 deaths, active criminal case |
| Woolsey 2018 | 0: reported 14:24, GOES-16 at 14:42 (own read) | 2: daytime, documented field reports | 1: night VIIRS unverified; GOES-17 offline | 3: long run | 1: zones must be hand-digitised | 2: orders lag hours | 3: Citygate AAR, WEA/EAS unused | 2 | 0: new UTM/zones/fuels, GOES-16 | 2 |
| Tubbs 2017 | 3: GOES-16 first, 21:47 PDT (pre-operational) | 0: night, no cameras | 1: S-NPP night pass unverified | 2 | 0: no 2017 zones | 3: hours of gap | 3: no WEA (Cal OES review) | 1: no hourly truth | 0 | 2 |
| Camp 2018 | 0: 911 06:25 vs GOES 06:52 (own read) | 1: NIST observations as text | 1: Landsat 8 ~10:45, after Paradise burned | 2 | 0: 2018 zones not found | 1: spotting beats the model | 3: CodeRED, no WEA | 2: NIST truth excellent | 0 | 0: 85 deaths, spotting mismatch |
| Marshall 2021 | 1: 911 11:09, sat 11:41 MST | 1 | 1: G16 and G17 agree, nothing sharper | 1 | 0 | 1: humans evacuated fast | 3: 215 reached in 42 min | 1 | 0 | 1: urban ember fire, all heuristic |
| Lahaina 2023 | 2 | 1 | 1 | 0: no HRRR for Hawaii | 0 | 1 | 3 | 0 | 0 | 0: 102 deaths |
| Smokehouse Creek 2024 | 2 (unverified) | 0 | 2 | 3 | 1 | 0: no late-alert gap | 1 | 2 | 0 | 3 |
| Park 2024 | 2 (unverified) | 1 (unverified) | 2 | 3 | 2 | 0 | 0: no failure documented | 2 | 0 | 2 |
| Thomas 2017 | 0: GOES-16 in safe mode | 0 | 1 | 0 | 0 | 1 | 1 | 0 | 0 | 2 |
| Aspen Acres 2026 | ? (unverified) | ? | 2 | 3 | 1 | 0: no documented failure | 0 | 1 | 0 | 3: no deaths reported |

Sources for the rows other than Eaton and Palisades: Woolsey https://file.lacounty.gov/SDSInter/bos/supdocs/144968.pdf; Tubbs https://bpb-us-e1.wpmucdn.com/sites.psu.edu/dist/1/33622/files/2018/10/SchmidtC-wdsq3i.pdf and https://www.sfchronicle.com/file/284/1/2841-Sonoma%20Assessment%20with%20Cover%20Letter.022618.pdf; Camp https://nvlpubs.nist.gov/nistpubs/TechnicalNotes/NIST.TN.2135.pdf and https://www.govtech.com/public-safety/deadly-camp-fire-did-not-prompt-an-emergency-alert.html; Marshall https://cimss.ssec.wisc.edu/satellite-blog/archives/44030 and https://digital-release.kdvr.com/news/local/42-minutes-to-send-the-1st-marshall-fire-alert/; Lahaina https://cimss.ssec.wisc.edu/satellite-blog/archives/53841; Thomas https://www.goes-r.gov/users/transitiontToOperations.html; Aspen Acres https://www.denver7.com/news/wildfire/colorado-wildfire-blog-aspen-acres-fire-becomes-seventh-largest-in-state-history. Rows marked "(own read)" are our own analysis of the NOAA AWS archive.

## 3. Eaton in detail

### 3a. Strongest honest storyline

All times are PST, Jan 7–8 2025. LOCAL means our own analysis of public files, labelled as such on screen.

| Time | Event | Real/Sim | Source |
|---|---|---|---|
| 18:13 / 18:17 | First calls to LASD and LACoFD. **People found it first; say so on screen.** | Real | AAR pp.13-14 https://file.lacounty.gov/SDSInter/bos/supdocs/207915.pdf |
| ~18:15–18:18 | Ignition (Citygate 18:15; CAL FIRE 18:18) | Real | https://www.fire.ca.gov/incidents/2025/1/7/eaton-fire ; Citygate |
| 18:23 | Watch Duty posts it | Real | AAR |
| **18:24** | **First satellite detection.** GOES-18 Meso Sector 2, scan 02:23:55Z, file 02:24:11Z | Real (LOCAL; not ingested yet) | s3://noaa-goes18/ABI-L2-FDCM/2025/008/02/ |
| 18:34 | GOES-18 CONUS: 2 px, ~385 MW total, available 02:34:33Z | Real (LOCAL, ingested) | `obs/goes18_fdcc.parquet` |
| 18:45 | **All aircraft grounded.** Citygate: "Nothing could fly" | Real | AAR p.15; Citygate §1.2.1 |
| 18:48 / 19:26 | "BE ALERT" advisory to 12 zones; first orders for 7 zones | Real | AAR p.24 |
| 18:30–03:30 | Model updates every 5 min from GOES. Hot-pixel centres step west from -118.108 (02:31Z) to -118.151 (10:31Z) | Real input, simulated forecast | LOCAL |
| ~21:30 | FSRI: spot fires west of the origin | Real (contested) | https://fsri.org/news/new-report-shows-fire-shifted-toward-west-altadena-hours-official-accounts |
| ~23:47–23:49 | Battalion Chief reports a structure fire at Glenrose Ave and W Loma Alta Dr, west of Lake. **Inject as a documented report.** | Real, documented and injected | Citygate Table 4 |
| 00:55 | First DINS-validated structure west of Lake (500 E Las Flores Dr) | Real | AAR p.25 |
| 01:12–03:00 | OEM receives no new alert direction | Real | AAR |
| 01:00–02:40 obs. | Four VIIRS 375 m night passes (NOAA-21, S-NPP, NOAA-20, NOAA-21) | Real (LOCAL; not ingested) | NOAA EFIRE files; https://svs.gsfc.nasa.gov/5558 |
| 02:11 | First GOES-18 hit in area X (west of Lake foothills), 2-of-3 persistence | Real (LOCAL) | `check_goes_report.html` |
| 02:18 | Radio: fire north of Farnsworth Park moving west. **Second documented report.** | Real, injected | AAR synopsis https://file.lacounty.gov/SDSInter/lac/1192777_AAR_EatonFireSynopsisofFindingsandTimeline.pdf |
| **02:59** | **Second sensor sharpens the picture.** S-NPP VIIRS file available (observed 01:25) with 2 px right at Lake Ave (-118.1317, 34.192–34.196), which the ~2–3 km GOES pixels cannot resolve | Real (LOCAL) | NOAA `noaa-nesdis-snpp-pds` |
| T_call | **Emberline warn/order and Cartesia call to the duty officer for west-Altadena zones** | Simulated: **not measured yet** | — |
| 03:25 | Real orders for 13 west-of-Lake zones. Same minute, the NOAA-21 file with 10 px west of Lake becomes available | Real | AAR App.10; Citygate Finding #20 |
| ~05:13 | FireGuard puts the front ~1 block west of Lake | Real | Citygate |
| ~05:15–05:43 | Calaveras zone order (AAR App.10 is ambiguous; media say 05:42) | Real | AAR; https://www.yahoo.com/news/17-deaths-eaton-fire-were-024433120.html |

Closing contrast, from cited sources only:
- Citygate: Incident Command had "no aerial fire spread intelligence" (Finding #19).
- LA County Fire lacked FireGuard access: https://www.fireengineering.com/firefighting/wildland-firefighting/with-altadena-burning-l-a-county-lacked-satellite-mapping-tool-used-by-other-agencies/
- The AAR recommends integrating NASA/NOAA thermal platforms.
- The point to make: public GOES and VIIRS data was flowing all night.

### 3b. What is real, simulated, or not shown

| Capability | Status |
|---|---|
| 1 Satellite detects | **Real.** Framed as "first satellite detection, 18:24 (meso) / 18:34 (CONUS)", never "found it first" |
| 2 Ground/aerial report | **Real text reports** (23:49, 02:18, optionally the 18:13 call). Any photo, video, helicopter or police camera is **SIMULATED** and labelled |
| 3 Second sensor | **Real** VIIRS 375 m at file-created availability. A URT (+2 min) run is an **assumption**: URT coverage of these passes is unverified (https://www.earthdata.nasa.gov/news/blog/wildfire-detection-us-canada-within-minute-satellite-observation). GOES-16 adds little (no meso over LA, oblique view). Skip it |
| 4 Live model update | Real inputs; forecast is simulated (labelled model output) |
| 5 Risk map | Real zone geometry (snapshot dated 2025-01-10; its statuses must never feed sim time) |
| 6 Call before real order | Real Cartesia call. **Lead time not yet measured.** Report it even if negative |
| 7 Real-world contrast | Real, cited from both AAR and Citygate |
| Not shown | Spotting or house-to-house spread; FIRIS or aerial IR (none existed); ALERTCalifornia Eaton archive (none found) |

### 3c. Remaining work, in priority order, to finish before Sun 09:00 PT

| # | Task | Owner | Hours | Notes |
|---|---|---|---|---|
| 1 | `statics.py`, `static/dem.tif` (resample `raw/dem_10m.tif` to the 90 m 340x340 EPSG:32611 grid, plus slope and aspect) and `static/fbfm40.tif` (LF2024 via the LANDFIRE Product Service; WIFIRE WCS fallback https://wifire-data.sdsc.edu/dataset/lf-surface-fuel-fbfm40/resource/8f8584e1-f8a4-471f-a0cf-b125f4ebc404) | Abby | 2–3.5 | **Critical path; start now.** Nothing physical runs without it |
| 2 | Compute decision: N=16 (labelled `+dev-n16`), window ending ~13:30Z, or a larger `cycle_min` | Team | 0.25 | Benchmark: 244 s per N=128 forecast on 2 workers (`docs/evidence/rust-hazard/benchmark.json`). The full window at N=128 is ~21 h on 2 workers (~5 h on 8, unbenchmarked). **Recommend N=16 and say so on screen.** Changing `cycle_min` is a decision change; flag it |
| 3 | `viirs.py` from NOAA S3 (no key): drop `FP_PersistentAnomalyCategory>0` (static pixel at -118.194, 34.153), set `available_ms` = file created time, write `obs/viirs.parquet` | Cody | 2–3 | Clone the `goes.py` pattern |
| 4 | `goes18_fdcm2` ingest (crop to the bbox) | Cody | 1–2 | Moves first detection to 18:24. Cut it if time is short; 18:34 CONUS is enough |
| 5 | Documented reports as branch injections (23:49 Glenrose, 02:18 Farnsworth), labelled "documented, injected" | Cody | 1 | Report effect was only 29.55 m in synthetic tests. Don't promise a visible jump |
| 6 | Fix `scenario.yaml` `honesty.real`: list VIIRS only once ingested and LANDFIRE only once on disk; mark `viirs_latency: urt` as an assumption | Cody | 0.1 | |
| 7 | **Freeze (D12)**, then precompute main, openloop and `demo_b1` in the background | Cody | ~1 people-time + 1–3 h compute at N=16 | The freeze target (Sat 20:00) has passed with `config_hash: null`. Freeze once, after 1–6 |
| 8 | Measure T_call vs 03:25 for each west-Altadena zone (main vs openloop) and put the number in the script | Cody | 0.5 | |
| 9 | Web FileSource and `arrival_q.bin` decode | Linda | 3–6 | Same cost for any fire; runs in parallel |
| 10 | Update the spec/08 demo script with this timeline and its labels | Any | 0.5–1 | |

Total is about 10–15 people-hours across three people, with compute running in parallel. That fits, provided #1 starts tonight.

## 4. Risks and other considerations

- **Spotting versus our model.** Spread west of Lake was ember-driven: "ember cast ... caused spot fires west of Lake Avenue after midnight" (AAR). The open-loop run will under-forecast there. Only assimilation (GOES/VIIRS "unexplained detection" and report injection) can recover it.
  - Never claim we caught the spot fires (23:49, 00:55).
  - Present the 02:11 GOES and 02:59 VIIRS signals as "satellite heat west of Lake", not "front crossed Lake". Citygate places the front 2 blocks east of Lake at 03:53.
- **The lead-time claim.** The 02:11 GOES signal trails the 23:49 and 02:18 responder reports, so compare only against the 03:25 electronic orders. Allowed wording: "Emberline's alert fired at T; the documented order for zone Z was at 03:25 (AAR App.10)". Not allowed: "would have saved lives" or any other outcome claim.
- **VIIRS latency.** Under honest file-created times, the lead from S-NPP is 26 min and the 10-pixel NOAA-21 pass gives none. Show the URT run only as a labelled assumption, or not at all.
- **Two discrepancies in the inputs.**
  - The NOAA-21 09:00Z pass was reported as "1 px at -118.1316" in one check and "none, persistent-anomaly only" in another. Re-check before naming it on screen.
  - GOES CONUS scan counts are 166 distinct vs 168 rows. Cosmetic.
- **Hindsight in the zones (D11).** The tiers were built from zones that were actually warned or ordered. Disclose it, per WAVE2 open decision 1.
- **Compute.** Seen live, a branch re-forecast takes minutes at N=128 and ~30 s or more at N=16, against the 10 s target. Precompute `demo_b1`.
- **Imagery licensing.**
  - ALERTCalifornia allows live-feed screen captures for public or educational use with the credit "ALERTCalifornia | UC San Diego" (https://alertcalifornia.org/?p=1501). No archive policy was found, and no Eaton camera was found.
  - Ignition videos (gas station, swim club) are litigation-sourced and copyrighted. Do not use them.
  - The FSRI spreadsheet licence is unstated, so skip it.
  - Any simulated camera frame must be watermarked SIMULATED.
- **Sensitivity.**
  - 19 deaths, 18 of them west of Lake (LA Times on the FSRI report, https://www.fireengineering.com/firefighting/wildland-firefighting/report-eaton-fire-marched-toward-west-altadena-hours-before-official-accounts; not Rep. Chu).
  - Historically Black neighbourhood, with a CA DOJ civil-rights inquiry (https://www.smartcitiesdive.com/news/california-doj-civil-rights-violations-eaton-fire-west-altadena/812309/).
  - SCE cause litigation, with the cause found by LACoFD and CAL FIRE and disputed by SCE (https://lacounty.gov/2026/08/04/county-of-los-angeles-fire-department-releases-the-investigative-reports-on-the-cause-of-the-eaton-fire/).
  - How to handle it:
    - Aggregate everything to zones.
    - Keep the cause out of scope.
    - Show FSRI and Citygate ("no failure", Capstone Finding #1) side by side without ruling between them.
    - Open with a short memorial line and a disclaimer.
    - Use "no warning preceded the west-of-Lake orders", not "no warning was ever sent".
- **Number hygiene.** Cite CAL FIRE's current 9,419 destroyed and 1,076 damaged, not Wikipedia's 9,418/1,071. Frame SCE's ~1,000 ac forecast as an 8-h, no-suppression run compared against the final 14,021 ac (https://www.yahoo.com/news/los-angeles-wildfires-were-10-100646516.html). Frame the 83% figure as a hazard-map gap, not a forecast error (https://www.yahoo.com/news/cal-fires-predictions-didnt-foresee-110037260.html).

## 5. Runners-up

1. **Palisades** (same day, same county zones layer, same UTM zone).
   - Real camera plume at ~10:24, G18 meso then CONUS then G16 detection order (https://cimss.ssec.wisc.edu/satellite-blog/archives/62428), and a wildland chaparral run that suits the model.
   - Documented order gap: homes burning at 11:27, order at 12:07 (AP, https://www.pbs.org/newshour/nation/homes-were-burning-and-roads-jammed-before-evacuation-order-for-palisades-fire-ap-finds).
   - It would beat Eaton if all of the following held:
     - written permission for ALERTCalifornia frames;
     - per-zone order times mapped to zone IDs;
     - a meso ingest (an extra download, not free);
     - a day-7 build of grid, statics and HRRR (+5–7 h).
   - **Cheap use tonight:** a cited "same day, other fire" slide showing the CIMSS detection ordering. Don't name the defendant; the case ended in a mistrial and has not been decided.
2. **Woolsey / Tubbs.**
   - Woolsey has the best official minute-level AAR and a long run.
   - Tubbs has the best satellite-first story.
   - Both need GOES-16-era data, a new UTM zone and hand-built zones, so they are post-hackathon options.
3. **Aspen Acres 2026** is the no-fatality, low-sensitivity option for after the hackathon, but it has no documented alert failure.

Line for `docs/process.md`: Demo-fire review: keep Eaton (D1). The real story is GOES-18 meso at 18:24 (people reported first, at 18:13), documented 23:49 and 02:18 reports, and S-NPP VIIRS at Lake Ave available 02:59, against the 03:25 orders. Critical path: Abby's statics and FBFM40, then VIIRS, freeze, then a precompute at N=16. Palisades is shown only as a side slide.