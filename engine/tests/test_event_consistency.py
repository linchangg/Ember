from types import SimpleNamespace
import numpy as np
import pytest
from emberline.hazard.event_consistency import consistency, evaluate


def test_member_weights_and_infinite_arrivals_retained():
    a = np.array([[[0.0, np.inf]], [[5.0, 10.0]]])
    r = consistency(a, np.array([0.25, 0.75]), np.array([[True, True]]), 6)
    assert r["weighted_fraction_of_burned_footprint_centers"] == 0.5
    assert r["member_mass_with_any_burned_center"] == 1


def test_already_assimilated_or_outside_horizon_is_not_test_truth():
    model = SimpleNamespace(
        land=SimpleNamespace(grid=SimpleNamespace(t_ref_s=0)),
        _last_issue=10,
        cfg=SimpleNamespace(horizon_min=360),
        _seen={"known": True},
    )
    events = [
        SimpleNamespace(obs_id="known", hot=True, observed_sim_ms=700000),
        SimpleNamespace(obs_id="far", hot=True, observed_sim_ms=999999999),
    ]
    r = evaluate(model, events)
    assert not r["evaluated_events"] and len(r["excluded_events"]) == 2
    assert r["scientific_acceptance"] == "not_established"


def test_unknown_support_is_not_negative_evidence():
    with pytest.raises(ValueError, match="support"):
        consistency(np.ones((1, 1, 1)), np.ones(1), np.zeros((1, 1), bool), 1)
