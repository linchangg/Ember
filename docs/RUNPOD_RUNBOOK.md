# Eaton precompute on RunPod

The Rust model is CPU-only (rayon). Pick **CPU pods**; a GPU would sit idle.
Each branch is one sequential replay (cycles depend on the previous one), so
parallelism comes from more cores per pod and one pod per branch.

## Readiness (Sat Oct 24, late)

| Item | Status |
|---|---|
| Rust model, statics, HRRR, GOES-18 CONUS, zones | Ready (statics on main `9c4ed23`) |
| Worker count | **Fixed locally:** `precompute --memory-gb`. fire-bridge silently caps workers at `(budget − arrivals) / (cells × (640 + 56 × weather frames))` bytes, ~0.2–0.4 GB each on Eaton; the 1 GB default ran 2–4 workers whatever `--workers` said |
| Timeout | Pass `--timeout-s 900`; the 120 s default is shorter than one 128-member cycle here |
| Cache portability | **Blocker for the demo laptop.** Cycle keys include `checkpoint.environment()` (OS, numpy/scipy/pyproj/pydantic versions), so a Linux cache misses on Windows and `run.py` would recompute live. Fix: replay precomputed bundles (`BundleReplayLoop`, today fixture-only) |
| Final inputs | VIIRS, GOES-18 meso and the injected-report branch are not built. Any input change re-keys every later cycle, so a run before they land is a rehearsal |
| Briefings | Generated during precompute (needs `OPENROUTER_API_KEY` on the pod) or in a second pass; `--no-briefings` bundles have none |
| Physics plausibility | **Check first.** At N=16, burned area is ~22 ha at ignition +65 min; compare with GOES hot-pixel extent before spending hours |
| Freeze (D12) | `scripts/freeze.py` does not exist; `config_hash` is null |

## Measured (laptop, Ryzen AI 9 365, real Eaton, N=16)

- 2 workers / 1 GB: 40–90 s per cycle
- 16 workers / 12 GB: 18–43 s per cycle (identical outputs), ~70 min for the full replay

128 members is roughly 8x the work per cycle. Measure on the pod before committing hours.

## Pod setup (Ubuntu image, as root)

```bash
apt-get update && apt-get install -y build-essential git curl tmux
python3 -m pip install uv==0.13.5
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal --default-toolchain 1.99.1
. "$HOME/.cargo/env"
git clone https://github.com/linchangg/Legendary-Fire-Project.git && cd Legendary-Fire-Project
uv sync --project engine --locked
cargo build --manifest-path physics/surface_v1/Cargo.toml -p fire-bridge --release --locked
```

Upload the git-ignored HRRR files (22 files, 30 MB) from the laptop, then check them:

```bash
scp -P <port> data/scenarios/eaton/forcing/*.nc root@<host>:Legendary-Fire-Project/data/scenarios/eaton/forcing/
cd data/scenarios/eaton/forcing && sha256sum -c forcing.sha256
```

Sanity checks, then a 3-cycle timing run:

```bash
uv run --project engine python -m ember.hazard.compatibility
uv run --project engine pytest engine/tests -q
nproc; free -g
```

## Run (one branch per pod, inside tmux)

```bash
W=$(nproc); MEM=$(( $(free -g | awk '/Mem:/{print $2}') * 3 / 4 ))
uv run --project engine python -m ember.scripts.precompute --scenario eaton \
  --branch main --filter pf --profile target --workers $W --memory-gb $MEM --timeout-s 900 \
  --no-briefings 2>&1 | tee main.log
```

`--branch openloop --filter none` on a second pod. Keep `--profile dev` as the fallback.

Download `engine/cache/eaton/<branch>/` (or `--cache-root`) with `tar czf` + `scp`.
