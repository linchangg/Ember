# Judging demo: synthetic cached replay

This is the verified synthetic `rust-fixture` package. It is a cached replay. Forecast skill is unproven. It is not an Eaton historical forecast.

The globe at `/` is a separate NASA EONET world-event display. Its Eaton marker is that world event. It does not open this forecast.

## Entry

`http://127.0.0.1:5175/judge`

The page header says **Synthetic scenario · cached replay · forecast skill unproven**. The link **World events globe · not this forecast** returns to `/`. From the globe, **Synthetic forecast dashboard** returns to `/judge`.

## Startup

From the repository root:

```sh
uv run --project engine python -m emberline.hazard.fixture_server \
  --root "/Users/abbyzhou/Documents/ChatGPT/legendary fire/handoff/emberline-engine-package-v1/forecast" \
  --port 8791 \
  --allow-origin http://127.0.0.1:5175
```

In a second terminal:

```sh
cd "/Users/abbyzhou/Documents/ChatGPT/legendary fire/github-work/web"
VITE_DATA_SOURCE=file VITE_SCENARIO_ID=rust-fixture \
VITE_ASSET_BASE=http://127.0.0.1:8791/ \
npm run dev -- --host 127.0.0.1 --port 5175
```

Open `http://127.0.0.1:5175/judge`. The replay opens at Jan 7, 6:00 PM PST, before the first forecast. Leave **Lead** at **+120 min**.

## Shutdown

Stop the Vite process, then stop the fixture server. Do not delete or rewrite the handoff package.

## Three exported moments

Lead stays +120 min. `p_by_lead[3]` is that lead. The panel rounds `100 * p` to one decimal place. Rank 1 is the top of the zone list.

1. Forecast appears.
   - Before: `1736302200000`, no cycle.
   - Cycle `cyc_rust-fixture_main_1736302800000`.
   - Issue `1736302800000` (Jan 7, 6:20 PM PST). View `1736310000000` (Jan 7, 8:20 PM PST).
   - Rank: FIX-Z2, FIX-Z3, FIX-Z1, FIX-Z4 are `watch`; FIX-Z5 and FIX-Z6 are `clear`.
   - Every zone has `p_by_lead[3] = 0`.
   - `burned_ha` 35.3573. `front_km` 2.04. Two simulated `goes_pixel` observations.

2. FIX-Z2 changes from watch to warning.
   - Previous cycle `cyc_rust-fixture_main_1736308200000`, issue `1736308200000` (7:50 PM PST): FIX-Z2 rank 1, `watch`, `p_by_lead[3] = 0.2796` (panel 28%).
   - Cycle `cyc_rust-fixture_main_1736308500000`, issue `1736308500000` (7:55 PM PST). View `1736315700000` (9:55 PM PST).
   - FIX-Z2 stays rank 1 and becomes `warning`. `p_by_lead[3] = 0.4197` (panel 42%).
   - FIX-Z3 stays rank 2 and `watch`. Its +120 probability changes from 0.2097 to 0.2099.

3. FIX-Z2 changes from warning to order.
   - Previous cycle `cyc_rust-fixture_main_1736311500000`, issue `1736311500000` (8:45 PM PST): FIX-Z2 `warning`, `p_by_lead[3] = 0.5887` (panel 58.9%).
   - Cycle `cyc_rust-fixture_main_1736311800000`, issue `1736311800000` (8:50 PM PST). View `1736319000000` (10:50 PM PST).
   - FIX-Z2 stays rank 1 and becomes `order`. `p_by_lead[3] = 0.5893` (panel still 58.9%).
   - FIX-Z3 stays rank 2 and `warning`, with `p_by_lead[3] = 0.5893`.

The order change is a status change. The +120 probability does not move enough to change the rounded panel percent.

## 90-second script

0:00 — “This is the synthetic dashboard, opened at `/judge`. The operator is looking at a cached replay of the rust-fixture scenario. The header says forecast skill is unproven. The globe’s Eaton marker is a NASA world event and is not this forecast.”

0:15 — “The honesty list is the data split. Terrain, fuels, weather, observations, zones, and residents are simulated. The map and zone list are derived Rust forecasts. Nothing here is an Eaton historical forecast.”

0:30 — “Issued time is the replay slider. It is Jan 7, 6:00 PM PST, and no forecast has been issued. Lead is separate. It is +120 minutes, so View is later than Issued. Moving Lead does not create a new issue.”

0:45 — Seek to 6:20 PM. “The first forecast is cycle `cyc_rust-fixture_main_1736302800000`. Issued 6:20 PM, view 8:20 PM. FIX-Z2 is rank 1. At +120 minutes every zone probability is 0. The bundle records 35.3573 burned hectares and two simulated GOES pixels.”

1:05 — Seek to 7:55 PM. “Cycle `cyc_rust-fixture_main_1736308500000`. FIX-Z2 changes from watch to warning. Its +120 probability changes from 0.2796 at 7:50 PM to 0.4197, which the panel shows as 42%. It stays rank 1.”

1:20 — Seek to 8:50 PM. “Cycle `cyc_rust-fixture_main_1736311800000`. FIX-Z2 changes from warning to order. The +120 probability is 0.5893, and the panel stays at 58.9%. FIX-Z3 remains warning at rank 2.”

1:30 — Return the slider to 6:00 PM. “Back to the start: no forecast issued. This is a synthetic cached replay, and forecast skill is unproven.”
