"""lead_times: sign convention, first-alert logic, and zone_orders.json keyed by real zone ids."""

import json
from pathlib import Path

from ember.scripts.lead_times import compute, first_times, lead_min, load_branch

REPO = Path(__file__).resolve().parents[2]
EATON = REPO / "data" / "scenarios" / "eaton"
MIN = 60_000
T0 = 1_736_335_500_000  # 2025-01-08 03:25 PST


def alert(level: str, t: int, zones: list[str], state: str = "proposed") -> dict:
    return {"level": level, "triggered_sim_ms": t, "zone_ids": zones, "state": state}


def write_branch(root: Path, branch: str, cycles: dict[int, list[dict]]) -> None:
    d = root / "eaton" / branch
    for k, alerts in cycles.items():
        (d / str(k)).mkdir(parents=True)
        (d / str(k) / "bundle.json").write_text(json.dumps({"knowledge_sim_ms": k, "alerts": alerts}))
    index = {"cycles": [{"knowledge_sim_ms": k, "bundle": f"cache/eaton/{branch}/{k}/bundle.json"} for k in cycles]}
    (d / "index.json").write_text(json.dumps(index))


def test_lead_sign():
    assert lead_min(T0, T0 - 30 * MIN) == 30  # ours earlier -> positive
    assert lead_min(T0, T0 + 10 * MIN) == -10  # ours later -> negative
    assert lead_min(T0, None) is None and lead_min(None, T0) is None


def test_first_alert_logic():
    first = first_times([
        ("warning", T0 - 60 * MIN, ["A"]),
        ("warning", T0 - 90 * MIN, ["A"]),  # earlier warning wins whatever the order seen
        ("order_recommended", T0 - 20 * MIN, ["A", "B"]),  # an order also sets warn for B
        ("advisory", T0 - 200 * MIN, ["C"]),  # advisories never count
    ])
    assert first["A"] == {"warn": T0 - 90 * MIN, "order": T0 - 20 * MIN}
    assert first["B"] == {"warn": T0 - 20 * MIN, "order": T0 - 20 * MIN}
    assert "C" not in first


def test_compute_from_cache(tmp_path):
    k1, k2 = T0 - 60 * MIN, T0 - 55 * MIN
    write_branch(tmp_path, "main", {
        k1: [alert("warning", k1, ["W1"])],
        # a superseded copy of the k1 warning re-appears later with its original triggered time
        k2: [alert("order_recommended", k2, ["W1", "X"]), alert("warning", k1, ["W1"], "superseded")],
    })
    truth = {
        "sources": {}, "west_altadena_0325_zone_ids": ["W1", "W2"],
        "entries": [{"zone_id": "W1", "official_level": "order", "t_sim_ms": T0},
                    {"zone_id": "W2", "official_level": "order", "t_sim_ms": T0},
                    {"zone_id": "E1", "official_level": "warning", "t_sim_ms": T0 - 120 * MIN}],
    }
    res = compute(truth, {"main": load_branch(tmp_path, "eaton", "main"),
                          "openloop": load_branch(tmp_path, "eaton", "openloop")})
    main = res["branches"]["main"]
    assert main["zones"]["W1"]["warn_lead_min"] == 60 and main["zones"]["W1"]["order_lead_min"] == 55
    assert main["zones"]["W2"]["order_lead_min"] is None  # no alert -> null, never guessed
    assert main["zones"]["E1"]["official_order_sim_ms"] is None
    s = main["west_altadena_0325_summary"]["order"]
    assert (s["n_earlier"], s["n_later"], s["n_no_alert"], s["median_lead_min"]) == (1, 0, 1, 55)
    assert main["alerted_without_official"] == ["X"]
    assert main["cache_through_sim_ms"] == k2
    assert res["skipped"][0]["branch"] == "openloop"
    assert "definition" in res and "sources" in res


def test_zone_orders_ids_exist():
    truth = json.loads((EATON / "truth" / "zone_orders.json").read_text(encoding="utf-8"))
    for layer in ("vectors", "web"):
        ids = {f["properties"]["zone_id"] for f in
               json.loads((EATON / layer / "zones.geojson").read_text(encoding="utf-8"))["features"]}
        rows = truth["entries"] + truth["advisories"]
        assert {r["zone_id"] for r in rows} <= ids
        assert set(truth["west_altadena_0325_zone_ids"]) <= ids
    assert all(r["official_level"] in ("warning", "order") and r["source"]["url"] for r in truth["entries"])
    west = [r for r in truth["entries"] if r["zone_id"] in truth["west_altadena_0325_zone_ids"]]
    assert len(west) == 13 and {r["t_sim_ms"] for r in west} == {T0}
