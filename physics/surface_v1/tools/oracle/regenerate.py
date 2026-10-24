#!/usr/bin/env python3
"""Regenerate independent reference data from a separately checked out USFS source."""
import argparse
import json
import re
from pathlib import Path
import subprocess
import tempfile

PIN = "7e3d7a44b7e7eed271ec3029ec34f0bd66466532"
ROOT = Path(__file__).resolve().parent


def run(args, **kwargs):
    return subprocess.run(args, check=True, text=True, capture_output=True, **kwargs).stdout


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--behave-source", type=Path, required=True)
    args = parser.parse_args()
    source = args.behave_source.resolve()
    if run(["git", "rev-parse", "HEAD"], cwd=source).strip() != PIN:
        raise SystemExit("Refusing an unpinned Behave checkout")
    if run(["git", "status", "--porcelain", "--untracked-files=normal"], cwd=source).strip():
        raise SystemExit("Refusing modified Behave source")
    with tempfile.TemporaryDirectory(prefix="surface-oracle-") as directory:
        build = Path(directory) / "build"
        run(["cmake", "-S", str(source), "-B", str(build), "-G", "Unix Makefiles",
             "-DCMAKE_POLICY_VERSION_MINIMUM=3.5", "-DTEST_MORTALITY=OFF", "-DEXAMPLE_APP=OFF"])
        run(["cmake", "--build", str(build), "-j", "4"])
        upstream = re.sub(r"\x1b\[[0-9;]*m", "", run([str(build / "testBehave")]))
        passed = re.search(r"Total tests passed: (\d+)", upstream)
        failed = re.search(r"Total tests failed: (\d+)", upstream)
        if passed is None or failed is None or int(passed[1]) == 0 or int(failed[1]) != 0:
            raise SystemExit("Upstream Behave tests did not all pass")
        objects = sorted((build / "CMakeFiles/testBehave.dir/src/behave").glob("*.o"))
        if not objects:
            raise SystemExit("No Behave objects found")
        executable = Path(directory) / "reference"
        run(["c++", "-std=c++11", "-I", str(source / "src/behave"),
             str(ROOT / "behave_reference.cpp"), *map(str, objects), "-o", str(executable)])
        values = json.loads(run([str(executable)]))
        if len(values) != 42:
            raise SystemExit("Unexpected reference matrix size")
        (ROOT / "behave_reference.json").write_text(json.dumps(values, indent=2) + "\n")
        catalog = ROOT.parent / "catalog"
        for driver, output in [("export_behave.cpp", "../../docs/spec/sb40.csv"),
                               ("behave_sb40.cpp", "behave_sb40.json")]:
            run(["c++", "-std=c++11", "-I", str(source / "src/behave"),
                 str(catalog / driver), *map(str, objects), "-o", str(executable)])
            content = run([str(executable)])
            if driver == "behave_sb40.cpp":
                cases = json.loads(content)
                if len(cases) != 80:
                    raise SystemExit("Unexpected SB40 matrix size")
                content = json.dumps(cases, indent=2) + "\n"
            elif len(content.splitlines()) != 41:
                raise SystemExit("Expected exactly forty Scott/Burgan fuel models")
            (catalog / output).resolve().write_text(content)
        (ROOT / "provenance.json").write_text(json.dumps({
            "source": "https://github.com/firelab/behave", "commit": PIN,
            "compiler": run(["c++", "--version"]).splitlines()[0],
            "upstream_test_summary": upstream.splitlines()[-8:],
            "cases": len(values), "strict_comparison_cases": 36,
            "documented_curing_convention_cases": 6,
            "wind_limit": "disabled in upstream; Rust comparison explicitly uses None"
        }, indent=2) + "\n")
        print(upstream.splitlines()[-8:])
        print(f"Wrote {len(values)} independent reference cases")


if __name__ == "__main__":
    main()
