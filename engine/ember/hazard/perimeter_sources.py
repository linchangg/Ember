"""Snapshot public Eaton perimeter candidates, without approving them as truth.

Source timestamps, mapped coverage and independence require human review. An empty
query is a dated search result, not proof that an archive never held the incident.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen


SOURCES = {
    "firis": (
        "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/"
        "CA_Perimeters_NIFC_FIRIS_public_view/FeatureServer/0",
        "UPPER(incident_name) LIKE '%EATON%' OR mission = '2025-CALAC-009087'",
    ),
    "wfigs": (
        "https://services3.arcgis.com/T4QMspbfLg3qTGWY/arcgis/rest/services/"
        "WFIGS_Interagency_Perimeters/FeatureServer/0",
        "attr_UniqueFireIdentifier = '2025-CALAC-009087'",
    ),
}


def read_public_json(url: str) -> bytes:
    with urlopen(url, timeout=45) as response:
        raw = response.read(20_000_001)
    if len(raw) > 20_000_000:
        raise ValueError("Source response exceeds 20 MB bound")
    return raw


def snapshot(out: Path, fetch=read_public_json) -> dict:
    """Bounded, paginated download with raw checksums; never manufacture valid times."""
    out.mkdir(parents=True, exist_ok=False)
    ledger = {
        "incident_id": "2025-CALAC-009087",
        "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
        "scientific_acceptance": "not_established",
        "sources": [],
    }
    for name, (base, where) in SOURCES.items():
        entry = {
            "source_id": name,
            "url": base,
            "where": where,
            "artifacts": [],
            "truth_eligibility": "requires_independent_source_review",
        }
        ledger["sources"].append(entry)

        def save(label, url):
            raw = fetch(url)
            path = out / f"{name}-{label}.json"
            path.write_bytes(raw)
            entry["artifacts"].append(
                {
                    "path": path.name,
                    "url": url,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
            data = json.loads(raw)
            if not isinstance(data, dict) or "error" in data:
                raise ValueError("Source returned an invalid response or ArcGIS error")
            return data

        try:
            metadata = save("metadata", base + "?f=json")
            if metadata.get("geometryType") != "esriGeometryPolygon":
                raise ValueError("Expected polygon feature layer")
            oid_field = metadata.get("objectIdField")
            if not isinstance(oid_field, str) or not oid_field:
                raise ValueError("Missing object-ID field metadata")
            ids = save(
                "ids",
                base
                + "/query?"
                + urlencode({"where": where, "returnIdsOnly": "true", "f": "json"}),
            )
            if "objectIds" not in ids:
                raise ValueError("Missing object-ID inventory")
            object_ids = ids["objectIds"]
            if object_ids is None:
                object_ids = []  # ArcGIS returns null for a successful empty query.
            if (
                not isinstance(object_ids, list)
                or len(object_ids) > 5000
                or any(type(i) is not int or i < 0 for i in object_ids)
                or len(set(object_ids)) != len(object_ids)
                or ids.get("exceededTransferLimit")
            ):
                raise ValueError("Invalid, duplicate or truncated object-ID inventory")
            count = 0
            for offset in range(0, len(object_ids), 200):
                chunk = object_ids[offset : offset + 200]
                page = save(
                    f"features-{offset // 200:03d}",
                    base
                    + "/query?"
                    + urlencode(
                        {
                            "objectIds": ",".join(map(str, chunk)),
                            "outFields": "*",
                            "returnGeometry": "true",
                            "outSR": "4326",
                            "f": "geojson",
                        }
                    ),
                )
                features = page.get("features")
                if (
                    page.get("type") != "FeatureCollection"
                    or not isinstance(features, list)
                    or len(features) != len(chunk)
                    or page.get("exceededTransferLimit")
                ):
                    raise ValueError(
                        "Incomplete feature page; do not treat it as full coverage"
                    )
                if any(
                    not isinstance(f, dict) or not isinstance(f.get("properties"), dict)
                    for f in features
                ):
                    raise ValueError("Invalid feature properties")
                received = [f["properties"].get(oid_field) for f in features]
                if any(type(i) is not int for i in received) or set(received) != set(
                    chunk
                ):
                    raise ValueError("Feature IDs differ from the requested page")
                count += len(features)
            entry.update(
                status="downloaded" if count else "no_current_matches",
                feature_count=count,
            )
        except (OSError, ValueError) as exc:
            entry.update(
                status="unavailable_or_invalid",
                error_type=type(exc).__name__,
                error=str(exc),
            )
    (out / "source-ledger.json").write_text(json.dumps(ledger, indent=2) + "\n")
    return ledger


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="New directory for raw public snapshots and source ledger",
    )
    args = parser.parse_args()
    result = snapshot(args.out)
    print(
        json.dumps(
            {
                "scientific_acceptance": result["scientific_acceptance"],
                "sources": [
                    {k: s[k] for k in ("source_id", "status")}
                    for s in result["sources"]
                ],
            }
        )
    )
    if any(s["status"] == "unavailable_or_invalid" for s in result["sources"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
