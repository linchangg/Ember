import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from ember.hazard.perimeter_sources import snapshot


def responder(url):
    query = parse_qs(urlsplit(url).query)
    if not urlsplit(url).path.endswith("/query"):
        result = {"geometryType": "esriGeometryPolygon", "objectIdField": "OBJECTID"}
    elif "returnIdsOnly" in query:
        result = {"objectIds": list(range(201))}
    else:
        ids = query["objectIds"][0].split(",")
        result = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": None,
                    "properties": {"OBJECTID": int(i)},
                }
                for i in ids
            ],
        }
    return json.dumps(result).encode()


def test_bounded_pagination_and_checksums_do_not_certify_truth(tmp_path):
    out = tmp_path / "sources"
    result = snapshot(out, responder)
    assert result["scientific_acceptance"] == "not_established"
    for source in result["sources"]:
        assert source["feature_count"] == 201
        assert len(source["artifacts"]) == 4
        assert source["truth_eligibility"] == "requires_independent_source_review"
        for artifact in source["artifacts"]:
            assert (
                hashlib.sha256((out / artifact["path"]).read_bytes()).hexdigest()
                == artifact["sha256"]
            )
    assert "2025-CALAC-009087" in result["sources"][1]["where"]
    with pytest.raises(FileExistsError):
        snapshot(out, responder)


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"error": {"code": 400}},
        {"objectIds": [1, 1]},
        {"objectIds": [1], "exceededTransferLimit": True},
    ],
)
def test_failed_or_truncated_response_is_not_success(tmp_path, bad):
    def fetch(url):
        return json.dumps(bad).encode() if "/query?" in url else responder(url)

    result = snapshot(tmp_path / "sources", fetch)
    assert all(s["status"] == "unavailable_or_invalid" for s in result["sources"])


def test_wrong_feature_identity_rejected(tmp_path):
    def fetch(url):
        data = json.loads(responder(url))
        for feature in data.get("features", []):
            feature["properties"]["OBJECTID"] = 9999
        return json.dumps(data).encode()

    result = snapshot(tmp_path / "sources", fetch)
    assert all(s["status"] == "unavailable_or_invalid" for s in result["sources"])


def test_empty_query_is_dated_search_not_truth(tmp_path):
    def fetch(url):
        return b'{"objectIds": null}' if "/query?" in url else responder(url)

    result = snapshot(tmp_path / "sources", fetch)
    assert all(
        s["status"] == "no_current_matches" and s["feature_count"] == 0
        for s in result["sources"]
    )


def test_partial_page_rejected(tmp_path):
    def fetch(url):
        if "objectIds=" in url:
            return b'{"type":"FeatureCollection","features":[]}'
        return responder(url)

    result = snapshot(tmp_path / "sources", fetch)
    assert all(s["status"] == "unavailable_or_invalid" for s in result["sources"])
