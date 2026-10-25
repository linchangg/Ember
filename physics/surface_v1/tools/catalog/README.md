# SB40 catalog and independent references

These files were exported from unmodified USFS Behave commit
`7e3d7a44b7e7eed271ec3029ec34f0bd66466532` ([source](https://github.com/firelab/behave/tree/7e3d7a44b7e7eed271ec3029ec34f0bd66466532)).
No network access or C++ compiler is needed for normal Rust tests.

`export_behave.cpp` reads public `FuelModels` getters and exports exactly the forty
Scott/Burgan IDs. Other optional catalogs present upstream are deliberately excluded.
The output is `docs/spec/sb40.csv`; the legacy 13-model catalog is preserved separately.
`behave_sb40.cpp` calls the independent upstream `Surface` API at two wind speeds
for every SB40 model, producing eighty cases in `behave_sb40.json`.

To regenerate both these files and the existing 42-case oracle from a clean,
already downloaded checkout at that commit:

```sh
python3 physics/surface_v1/tools/oracle/regenerate.py --behave-source /absolute/path/to/behave
cargo test --manifest-path physics/surface_v1/Cargo.toml -p fire-behavior --test behave_oracle
```

The regeneration script refuses another commit or modified upstream source and
runs upstream's own tests before exporting. The SB40 comparison uses 3%, 4%, 5%
dead moisture and 30%, 60% live moisture, 30% slope and wind 0/3 m/s; upstream's
wind cap is disabled and the Rust test explicitly selects the matching policy.
Strict comparisons cover head spread and reaction intensity; they do not establish
historical forecast accuracy or validate the separate NB91 urban heuristic.
