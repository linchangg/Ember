# 06: Demo scenario selection (Emberline)

**Recommendation.** PRIMARY: **Eaton Fire, Jan 7-8 2025, hybrid replay.** Real GOES-18/16, VIIRS, HRRR, 3DEP and LANDFIRE data, plus documented responder reports, with simulated cameras, people and phones layered on top and labeled. BACKUP: **Palisades Fire, same day.** It reuses the same GOES and HRRR downloads and needs only a different bounding box. FALLBACK MODE: a synthetic ignition on the Eaton terrain, fuels and wind (an OSSE-style test), clearly labeled "training scenario". Use it only if the real replay fails the first-hour checks.

All times are PST (UTC-8; there is no DST in January) unless marked Z.

---

## 1. Ranked candidates

| # | Candidate | Satellite visibility | Story / documented timeline | Modelability (wildland run before people?) | Data | Sensitivity | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | **Eaton 2025** (Altadena, night, Santa Ana) | Night ignition, so FDC detection is clean. GOES-18 at ~45° view zenith (est.), and a GOES-18 **1-min mesoscale sector over LA from 18:10Z Jan 7 to Jan 10**. GOES-16 also sees it, but obliquely. First VIIRS detection ~01:30 Jan 8 | **Best documented of any candidate.** County (Citygate, AAR) and state (FSRI) reports give minute-level times: west-of-Lake reports at 22:50 and 23:49, and orders at **03:25**. 19 deaths, 18 of them west of Lake Ave | **Partly.** The 21:00-03:30 westward run went mid-slope through San Gabriel foothill chaparral, which is credible for Rothermel. The entry into west Altadena was downslope plus embers, which is not | HRRR v4, GOES-16/18 on AWS, LANDFIRE pre-2025. No overnight aircraft perimeters (aircraft grounded) | **High.** Deaths, SCE litigation, racial-equity dimension | **PRIMARY** |
| 2 | **Palisades 2025** | GOES-18 Meso Sector 2 first detection ~18:26Z. GOES-18 CONUS +5 min, GOES-16 CONUS +16 min (CIMSS). Daytime ignition, smoke | 12 deaths. The evacuation-gridlock story is less clean than "officials ordered late" | **Good.** Long chaparral run in the Santa Monica Mtns before the urban edge | **Same files as Eaton** | High: arson prosecution of the Lachman-fire suspect, LADWP suits (unverified) | **BACKUP** |
| 3 | Aspen Acres 2026 (Beulah, CO) | 40 acres at ~06:00 to >23,100 acres by 17:00 on Jun 29, so very visible. GOES-19 (East) and GOES-18 (West). Daytime | Beulah order "just after 9 a.m.", Rye in the afternoon, ~180 structures, **no deaths reported** | **Good.** Wet Mountains wildland fuels, 100 mph gusts | HRRR v4, LANDFIRE 2024, NIFC perimeters. No after-action report yet | Low (cause disputed) | Strong alternative if the team wants no fatalities |
| 4 | Tubbs 2017 (Santa Rosa, night) | GOES-16 only, at its 89.5W *test* position. CIMSS: data "preliminary, non-operational". No GOES-West (GOES-17 not launched). VIIRS 10:07Z | Night run, documented Sonoma alert failure (no WEA), 22 deaths | **Very good.** ~15-20 km SW run over the Mayacamas before Coffey Park | Weak: HRRR v2, LANDFIRE 2014, L2 FDC archive for Oct 2017 (unverified) | Medium (litigation resolved in PG&E bankruptcy) | Great story, too much data risk |
| 5 | Marshall 2021 (CO, grass) | Daytime. First signature at 18:41Z on GOES-16 and GOES-17 | 1,084 homes, 2 ignitions, few minute-level order times | Short grass run, then mostly urban | Good | Medium (Xcel) | OK, urban-dominated |
| 6 | Morrill 2026 (NE) | Enormous (~642k ac) | Sparse population | Grass: ideal | Good | Low-Med | Physics showcase, weak people story |
| 7 | Camp 2018 | GOES-16 only, oblique, day, smoke | 85 deaths | Canyon run, then urban | HRRR v3 | Very high | Reject |
| 8 | Knowles 2026 (CO-UT) | Merged complex | Firefighter burnover deaths, investigation open | n/a | n/a | Very high | Reject |
| 9 | Lahaina 2023 | Small, daytime | 102 deaths | Urban | **No operational HRRR over Hawaii** | Very high | Reject |
| 10 | Fully simulated | Synthetic | Full control | Perfect | Trivial | None | Fallback mode only (§4) |

---

## 2. PRIMARY: Eaton hybrid replay

### Window
- **Full replay:** Tue Jan 7 2025, 18:00 PST to Wed Jan 8, 07:00 PST = **2025-01-08 02:00Z to 15:00Z**. The whole UTC window falls on **day-of-year 008**. Sunrise is ~06:58, so the main run is entirely at night.
- **Demo focus:** 21:00 to 03:30 PST (05:00Z to 11:30Z). This covers the westward foothill run and the gap before the 03:25 orders.
- **Pitch cut:** start at ignition, scrub to ~22:30, then run "live" from there to 03:25.

### Bounding box (~30 x 30 km)
- **Lat 34.08 to 34.35 N, Lon -118.26 to -117.94 W** (≈29.5 km E-W x 30 km N-S). Centre ≈ 34.215, -118.10.
- It covers La Cañada Flintridge/JPL, Altadena (east and west), Pasadena, Sierra Madre, Arcadia, Monrovia and Mt Wilson. It excludes the Palisades (-118.54) and Hurst (Sylmar) fires.
- **Ignition:** 34.205 N, -118.088 W. This is the hillside above Eaton Canyon wash, across from Altadena Dr / Canyon Close Rd (Wikipedia; unverified precision). Time: ~18:15 (Citygate) / 18:18 (FSRI). Use 18:15 PST = **02:15Z**.
- **Key line: Lake Avenue ≈ -118.1316** (unverified, check on a map). Everything west of it is "west Altadena". The 13 zones ordered at 03:25 were ALD-ARROYOSECO, CANON, CASITAS, CHANEY, FARNSWORTH, GARDEN, LAUREL, MEADOWS, MILLARD, PALM, WAPELLO, WHITEPARK and LCF-JPL.

### Satellite products
- **GOES-18 (GOES-West, 137.2W): primary.**
  - `noaa-goes18/ABI-L2-FDCC/2025/008/02..15/` is the PACUS sector, every 5 min, ≈156 files.
  - `ABI-L2-FDCM` is the 1-min mesoscale. CIMSS documents a GOES-18 1-min MDS over LA from 18:10Z Jan 7 to Jan 10. Check whether it is M1 or M2.
  - Estimated IR pixel at Altadena ≈ 2.3 x 3 km at ~45° view zenith. This is my own geometry calculation (unverified).
- **GOES-16 (GOES-East, 75.2W): secondary.** `noaa-goes16/ABI-L2-FDCC/...`. Very oblique (~60° VZA, ~4-5 km pixels, est.). It is still a second, independent sensor stream for the "pluggable sensors" story. GOES-16 was still GOES-East in Jan 2025; GOES-19 took over on Apr 7 2025.
- **Optional backdrop:** ABI-L1b band 7 (3.9 µm) imagery for the "satellite view" panel.
- **VIIRS 375 m:** S-NPP, NOAA-20 and NOAA-21 (VNP14IMG / VJ114IMG / VJ214IMG).
  - NASA SVS reports the **first VIIRS detection of Eaton at "01:30 AM on January 8"**. That is presumably PST (= 09:30Z), since 01:30Z would be before ignition (unverified).
  - Expect 2-3 night passes between ~08:30Z and ~11:30Z (00:30-03:30 PST), **before the 03:25 orders**. For comparison, CIMSS shows a VIIRS pass at 09:20Z on Jan 9.
  - There is no other VIIRS pass in the window. The day pass is ~20-22Z.

### Visibility assessment
- The ignition should show within 1-3 frames (unverified).
- The westward run went from Eaton Canyon (-118.09) past Rubio Canyon (~-118.12) to north of Farnsworth Park (~-118.14). That is ~5 km, about 2 GOES-18 pixels: **visible but marginal**, stepping pixel to pixel. Offshore flow means no cloud, and 3.9 µm largely sees through smoke.
- Two artefacts to handle:
  - **PSF bleed:** very hot pixels light up their neighbours, so require 2-of-3-frame persistence.
  - **Terrain parallax:** L2 is navigated to the ellipsoid, so fires at 300-1,500 m appear ~0.3-1.5 km NE of their true position (est., unverified).

### Documented timeline to show (PST, Jan 7-8 2025)

| Time | Event | Source |
|---|---|---|
| ~18:15 / 18:18 | Ignition, Eaton Canyon | Citygate / FSRI |
| 18:45 | All aircraft grounded by wind | Citygate, AAR |
| 18:48 | Advisory alerts to east Altadena zones | Citygate, AAR |
| 19:26 | First evacuation orders (east Altadena zones) | Citygate, AAR |
| 21:08-21:52 | FireGuard (DoD satellite) shows a large mid-slope westward movement in the mountains, not yet at the communities. **Incident Command did not have this imagery** | Citygate; LA Times via Fire Engineering |
| ~21:30 | State report: fire moving west toward N. Lake Ave | FSRI/state report via LA Times |
| 22:50 | 911 call: fire visible from E. Calaveras St (west Altadena). Citygate also logs fire near Midwick Dr / Glen Canyon Rd, ~¾ mi east of Lake | LA Times; Citygate |
| 23:20 | Winds at Rubio Canyon shift from NW/NNW to NE/NNE | Citygate |
| **23:49** | **First structure fire west of Lake**: Glenrose Ave and W. Loma Alta Dr (Battalion Chief) | Citygate |
| ~00:00 | Field staff recommend expanding orders west; not acted on | AAR; LA Times |
| 01:00 | Sheriff begins door-to-door evacuations west of Lake, north of Loma Alta (no electronic alert) | Citygate |
| ~01:30 | First VIIRS detection | NASA SVS (tz unverified) |
| 02:18 | Radio: fire moving west in foothills north of Farnsworth Park | Citygate, AAR |
| **03:25** | **Evacuation orders for 13 west-Altadena zones.** No west Altadena warning was ever sent | Citygate, AAR; FSRI |
| 03:27 | Western front exits Rubio Canyon into NE Altadena | Citygate |
| 05:13 | FireGuard: front ~1 block west of Lake Ave | Citygate |
| ~06:00 | Community conflagration "well underway" | Citygate |

The sources disagree on the official story. LA County's Citygate investigation (May 2026) found **no misconduct**. The McChrystal Group after-action review and the state-commissioned FSRI report describe systemic information gaps. Show the facts and times; do not adjudicate blame.

### The honest number (fix every rule in a config file and commit it before the first full run)
1. **Area X** is the "west Altadena foothills": burnable cells west of Lake Ave (-118.1316), north of ~34.19 N, between Lake Ave and Millard Canyon (~-118.16). These are the ALD-FARNSWORTH, MILLARD and CHANEY zone edges. For GOES, use the GOES-18 fixed-grid pixel(s) whose parallax-corrected footprint lies entirely west of Lake.
2. **T_sat** is the first satellite detection in Area X. For GOES-18 FDC, the fire mask must be in the fire classes (verify the code list) in ≥2 of 3 consecutive frames. VIIRS counts if nominal or high confidence. Report GOES and VIIRS separately.
3. **T_fc** is the earliest forecast issue time at which P(fire reaches Area X within 6 h) ≥ 0.5. Every forecast may use only data available at its issue time:
   - The HRRR cycle used must be the latest one actually available. Assume ~1 h latency.
   - No post-fire LANDFIRE.
   - No parameter tuning after looking at the score. Commit the hash.
4. **Headline:** "Lead time = T_sat − T_fc minutes." Show it for **DA vs open-loop (no assimilation)**, so the value of the filter is visible. **Report it even if it is negative.**
5. **Context line, kept separate and not a score:** "Emberline's forecast reached 50% for zone ALD-FARNSWORTH at T_fc. Electronic evacuation orders for that zone went out at 03:25. The first structure fire west of Lake Ave was reported at 23:49." Add on screen: *"Retrospective replay. Officials did not have this tool. This is not a claim about outcomes."*
6. Optional cross-check: compare against the documented point observations at 23:49, 02:18, 03:27 and 05:13. Use them as validation hits, or feed them as real ground reports (see below).

### What is real and what is simulated

| Real (label "REAL DATA") | Simulated (label "SIMULATED") |
|---|---|
| GOES-18/16 FDC, VIIRS AF, HRRR v4 winds, 3DEP terrain, LANDFIRE fuels | Fixed cameras (place them at real lookout sites, e.g. Mt Wilson / Echo Mountain) and the judge-injected camera feed |
| Official evacuation zone names and the 03:25 order time | People/phones: the judge-as-resident, synthetic population agents |
| **Documented responder reports** used as ground-report observations at their logged times (23:49 Glenrose/W Loma Alta; 02:18 north of Farnsworth Park), geocoded to intersections, labeled "documented report" | Ground reports submitted by judges |
| Exposure: Census population, building footprints, HIFLD hospitals, nursing homes and schools | SMS/voice alerts (the officials are fictional recipients) |

Real cameras did see the Eaton fire (ALERTCalifornia network). Whether their archive can be reached is unverified, so do not plan on it.

### Modelability and the WUI mitigation
- The **physics-credible portion** is the 21:00-03:30 westward run through front-range chaparral (LANDFIRE SH/chaparral models). Score the honest number on the foothill/edge cells only.
- **Inside the community** (LANDFIRE NB1 urban), use two clearly flagged heuristics:
  - **(a) Ember spotting kernel.** Burning cells above an intensity threshold drop spot ignitions downwind with lognormal distance (mean ~0.3-1 km under Santa Ana flow) and per-cell probability. The reports explicitly mention spot fires reaching west Altadena before the main front.
  - **(b) Urban proxy spread.** Give vegetated NB1 cells a slow constant house-to-house spread rate, e.g. a Hamada-style ~0.3-1 km/h downwind, or a low-load fuel model (GR2/TU1).
- Render urban-cell output as **"ember exposure (heuristic)"** with a distinct style. Never present it as a Rothermel prediction.
- **Expected risk:** HRRR (3 km) shows mostly NE flow, so the open-loop model will likely push the fire SW into east Altadena and Pasadena rather than west along the slopes. The terrain-channelled westward run may only appear once the filter assimilates the GOES/VIIRS detections. That is the DA story, but it only works if GOES actually resolves the run (first-hour check #1).

### Data availability
- **Inputs:** HRRR v4 cycles 2025-01-08 00-15Z (plus Jan 7 18-23Z), sfc f00-f06. LANDFIRE LF 2023 or 2024 (pre-fire; verify). 3DEP 1/3".
- **Perimeters:** none are timestamped overnight, because aircraft were grounded. Use NIFC WFIGS daily perimeters from Jan 8, NASA FEDS 12-hourly VIIRS perimeters, and CAL FIRE DINS for the final footprint (aggregated only). FireGuard is not public, so cite its Citygate timestamps as text.

### Sensitivity guidance
- **Facts to know:**
  - 19 deaths, 18 of them west of Lake Ave.
  - West Altadena is a historically Black neighbourhood; nearly half of Altadena's Black households were destroyed.
  - SCE faces suits from residents, the County (Mar 2025) and the DOJ (Sep 2025), and cause reports point to an Edison tower. Cal Fire's cause finding was still pending as of Sep 2025 (unverified current status).
- **Do:**
  - Open with one sentence: "19 people died in the Eaton fire. We replay this night because the county's and state's own reviews document an information gap."
  - Aggregate exposure to zones.
  - Keep the "retrospective, not outcome" disclaimer on screen.
- **Don't:**
  - Show death locations, victim names or house numbers (the county reports redact them as 34**; keep it that way).
  - Name or blame individual officials or SCE. Keep cause out of scope: ignition is a point.
  - Say "would have saved lives".
  - Make the judge's phone a "victim". They are a resident who gets an act-by time.

---

## 3. BACKUP: Palisades (same night, same files)
- **Why:** the FDCC files are whole-sector and HRRR files are CONUS-wide, so the downloads for Eaton already contain Palisades. Only the bbox and the ignition change. The early run is long and through wildland chaparral, which is better physics than Eaton.
- **Window:** Tue Jan 7 2025, 10:00 to Wed Jan 8, 04:00 PST = **2025-01-07 18:00Z to 01-08 12:00Z**. That spans DOY 007-008. It includes the daytime run toward Palisades Highlands / Pacific Palisades and the overnight push west toward Malibu (timing unverified).
- **Bbox:** lat 33.98 to 34.25 N, lon -118.72 to -118.40 W. The southern part is ocean, so mask it as non-burnable.
- **Ignition:** ~34.07 N, -118.54 W (Temescal Ridge / Skull Rock area, unverified precision), ~10:30 PST.
- **GOES:** GOES-18 Meso Sector 2 first detection ~18:26Z, then GOES-18 CONUS +5 min and GOES-16 CONUS +16 min (CIMSS NGFS dashboard arithmetic). Daytime: expect weaker FDC confidence and smoke.
- **VIIRS:** a day pass ~20-22Z Jan 7 and night passes ~09-11Z Jan 8 (unverified).
- **Honest number:** satellite lead time only, for Area X = Palisades Highlands, or the Big Rock/Malibu edge for the night segment. Official order times come from the LAFD after-action report (not reviewed here, unverified).
- **Sensitivity:** 12 deaths; an active criminal case; LADWP suits.

**Alternative backup with no fatalities:** Aspen Acres (Beulah, CO), Mon Jun 29 2026, ~05:00-18:00 MDT (11:00Z Jun 29 to 00:00Z Jun 30). Bbox ~37.93-38.20 N, -105.17 to -104.83 W. Uses GOES-19 and GOES-18. It needs a separate data pull, and the ignition point and time are unconfirmed.

---

## 4. Fully simulated vs hybrid

| | Fully simulated | Hybrid (recommended) |
|---|---|---|
| **Gains** | Guaranteed drama. Ignition placed in wildland, so there is no urban-physics problem. Free placement of people and cameras. No sensitivity issues | Real space data goes in (SpaceXAI fit). A real, independently documented timeline. A credible honest number |
| **Loses** | "Real space data goes in" is weakened: GOES/VIIRS observations would be *synthesized* from a truth run. The honest number is graded against our own simulation, so judges will discount it | Less control. GOES may only marginally resolve the run. Urban spread is a heuristic. Sensitivity must be handled |

**Recommendation:** ship the hybrid. Also build the synthetic mode on the Eaton grid as a cheap fallback; it is mostly the same code. In that mode, a "truth" ensemble member generates synthetic GOES and VIIRS observations with the same likelihood functions, and the screen is labeled "TRAINING SCENARIO".

---

## 5. Verify on a laptop in the first hour (go/no-go for Eaton by ~hour 2)
1. **GOES-18 FDCC, 2025/008/02-15.** Plot fire-mask pixels per frame over the bbox. Find:
   - (a) the first detection time after 02:15Z
   - (b) **whether, and when, a pixel lying entirely west of Lake Ave (-118.1316) first turns fire** (this is the go/no-go)
   - (c) PSF bleed and persistence behaviour
   - (d) which mesoscale sector (M1/M2) covered LA, and whether FDCM shows the run more clearly
2. **GOES-16 FDCC,** same window. Is it usable as a second sensor, or too coarse?
3. **VIIRS 375 m for Jan 8, 00-15Z.** Confirm the ~01:30 PST pass and whether detections fall west of Lake before 03:25. Access needs a FIRMS MAP_KEY or an Earthdata login. Use an existing teammate account only; do not create new ones per the brief.
4. **HRRR 2025-01-08 00-15Z.** Does it show NE Santa Ana flow and the ~23:20 PST (07:20Z) NE/NNE shift? Note the actual cycle availability latency for no-leakage.
5. **LANDFIRE FBFM40 for the bbox.** What fraction of Altadena is NB1? Which fuel models line the foothills? Is the version pre-2025?
6. **One open-loop spread run from ignition.** Does any westward foothill spread appear by 03:00? This tells you how much the DA has to do.
7. **Evacuation-zone polygons.** Find a public source for the LA County/Genasys zones, e.g. an ArcGIS layer (unverified availability). If none exists, hand-draw the 13 west-Altadena zones coarsely and label them approximate.
8. **Time handling:** keep everything internal in UTC and display PST. Confirm that FDC filenames use scan start times.

If check 1(b) fails, meaning no GOES-18 pixel entirely west of Lake turns fire before the 03:25 PST orders (11:25Z), make VIIRS the honest-number sensor or switch to the Palisades backup.

---

## Sources
- Citygate Associates, *Investigation Report of the Eaton Fire Evacuation Alerts* (LA County, May 18 2026): https://file.lacounty.gov/SDSInter/lac/1208708_InvestigationReportoftheEatonFireEvacuationAlerts_05-18-26_.pdf
- LA County AAR, *Eaton Fire Synopsis of Findings and Timeline* (McChrystal Group review): https://file.lacounty.gov/SDSInter/lac/1192777_AAR_EatonFireSynopsisofFindingsandTimeline.pdf
- LA County release on the west Altadena investigation: https://recovery.lacounty.gov/2026/05/18/county-of-los-angeles-fire-department-releases-independent-investigation-findings-of-west-altadena-evacuation-decisions/
- Fire Engineering / LA Times, "Eaton Fire marched toward west Altadena hours before official accounts": https://www.fireengineering.com/firefighting/wildland-firefighting/report-eaton-fire-marched-toward-west-altadena-hours-before-official-accounts
- Fire Engineering / LA Times, "L.A. County lacked satellite mapping tool": https://www.fireengineering.com/firefighting/wildland-firefighting/with-altadena-burning-l-a-county-lacked-satellite-mapping-tool-used-by-other-agencies/
- FSRI: https://fsri.org/news/new-report-shows-fire-shifted-toward-west-altadena-hours-official-accounts (links LA Times 2025-11-20)
- Fire Engineering, "No misconduct, delay in evacuation notices": https://www.fireengineering.com/firefighting/wildland-firefighting/eaton-fire-no-misconduct-delay-in-evacuation-notices-to-west-altadena-ca-report-says/
- Wikipedia, Eaton Fire: https://en.wikipedia.org/wiki/Eaton_Fire
- CIMSS, NGFS detections of fires near LA: https://cimss.ssec.wisc.edu/satellite-blog/archives/62428
- CIMSS, first 4 days of the LA wildfires (GOES-18 1-min MDS): https://cimss.ssec.wisc.edu/satellite-blog/archives/62559
- CIMSS, Day Night Band over California: https://cimss.ssec.wisc.edu/satellite-blog/archives/62490
- NASA SVS 5558, spread of the Palisades and Eaton fires (FEDS, first VIIRS detection): https://svs.gsfc.nasa.gov/5558
- CIMSS, Marshall Fire: https://cimss.ssec.wisc.edu/satellite-blog/archives/44030
- Denver Gazette, Marshall timeline: https://www.denvergazette.com/2023/06/08/a-timeline-of-the-marshall-fire-and-the-aftermath/
- CIMSS, Northern California wildfires 2017 (GOES-16 preliminary): https://cimss.ssec.wisc.edu/satellite-blog/archives/25665
- NESDIS, GOES-19 operational as GOES-East: https://www.nesdis.noaa.gov/news/noaas-goes-19-now-operational-goes-east-providing-critical-new-data-forecasters
- Denver Gazette, Aspen Acres roundup: https://www.denvergazette.com/2026/06/29/colorado-wildfire-roundup-aspen-acres-explodes-to-23000-acres-evacuations-from-custer-county-to-i-25/
- CPR, Aspen Acres: https://www.cpr.org/2026/06/29/beulah-evacuate-aspen-acres-11-fire-two-fires-burn-in-southern-colorado/
- Denver7, Aspen Acres 50,000 acres / 180 structures: https://www.denver7.com/news/wildfire/the-50-000-acre-aspen-acres-fire-has-destroyed-180-structures-latest-on-colorados-wildfires
- UPR, Knowles/Snyder firefighter deaths: https://www.upr.org/environment/2026-06-30/firefighters-killed-snyder-knowles-wildfire-utah-colorado-border
- IQAir, Morrill Fire: https://www.iqair.com/us/newsroom/wildfire-map-spotlight-morrill-fire-nebraska
- Windy community, HRRR availability in Hawaii: https://community.windy.com/topic/32477/hrrr-availability-in-hawaii
