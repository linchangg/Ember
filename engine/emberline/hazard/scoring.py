"""Independent raw-arrival scores. This module does not run the solver."""

from __future__ import annotations

import math

import numpy as np

from .experiment_io import ConfigError


DIFFERENCE_DIRECTIONS = {
    "brier": "negative_is_better",
    "dice": "positive_is_better",
    "iou": "positive_is_better",
    "false_positive_ha": "negative_is_better",
    "false_negative_ha": "negative_is_better",
}
RELIABILITY_BINS = 10
RELIABILITY_NOTE = (
    "Correlated pixels in one synthetic incident do not establish real-world calibration."
)
Q50_INDEX = 2


def absolute_target_min(issue_min: object, lead_min: object) -> int:
    """Absolute truth time is the issue plus the forecast lead, in minutes."""
    issue = _whole_minutes(issue_min, "issue_min")
    lead = _whole_minutes(lead_min, "lead_min")
    if lead < 0:
        raise ConfigError("lead_min must be nonnegative")
    return issue + lead


def weighted_burn_probability(
    arrivals_abs_min: np.ndarray, weights: np.ndarray, target_min: float
) -> np.ndarray:
    """Weighted probability that each cell has burned by an absolute minute."""
    arrivals = np.asarray(arrivals_abs_min)
    member_weights = np.asarray(weights)
    if arrivals.ndim != 3 or arrivals.dtype.kind != "f":
        raise ConfigError("arrivals must be a floating-point array shaped (members, height, width)")
    if np.isnan(arrivals).any() or np.isneginf(arrivals).any():
        raise ConfigError("NaN and negative infinity are invalid raw arrivals")
    if isinstance(target_min, bool) or not np.isfinite(target_min):
        raise ConfigError("target time must be finite")
    members = arrivals.shape[0]
    if member_weights.dtype.kind != "f" or member_weights.shape != (members,):
        raise ConfigError("weights must be floating point and match the ensemble")
    if not np.isfinite(member_weights).all() or np.any(member_weights < 0):
        raise ConfigError("weights must be finite and nonnegative")
    if not np.isclose(float(member_weights.sum()), 1.0, rtol=0.0, atol=1e-8):
        raise ConfigError("weights must be normalized")
    burned = arrivals <= float(target_min)
    return np.sum(member_weights[:, None, None] * burned, axis=0)


def predicted_burned(probability: np.ndarray, threshold: float = 0.5) -> np.ndarray:
    field = _probability_field(probability)
    if threshold != 0.5:
        raise ConfigError("the burned threshold is 0.5")
    return field >= threshold


def declared_valid(truth_arrivals: np.ndarray) -> np.ndarray:
    """Finite arrivals and +inf are usable. NaN is not declared valid."""
    truth = _truth_field(truth_arrivals)
    if np.isnan(truth).any() or np.isneginf(truth).any():
        return ~(np.isnan(truth) | np.isneginf(truth))
    return np.ones(truth.shape, dtype=bool)


def truth_burned_at(
    truth_arrivals: np.ndarray, target_min: float, valid: np.ndarray
) -> np.ndarray:
    """Burned truth at T. NaN inside the declared mask is rejected, not treated as unburned."""
    truth = _truth_field(truth_arrivals)
    mask = _boolean_mask(valid, truth.shape, "valid mask")
    if np.any(mask & (np.isnan(truth) | np.isneginf(truth))):
        raise ConfigError("NaN is not observed unburned truth")
    if isinstance(target_min, bool) or not np.isfinite(target_min):
        raise ConfigError("target time must be finite")
    burned = np.zeros(truth.shape, dtype=bool)
    usable = mask & np.isfinite(truth)
    burned[usable] = truth[usable] <= float(target_min)
    return burned


def score_masks(
    predicted: np.ndarray, truth: np.ndarray, valid: np.ndarray, cell_m: float
) -> dict[str, object]:
    mask = _boolean_mask(valid, np.asarray(predicted).shape, "valid mask")
    pred = _boolean_mask(predicted, mask.shape, "predicted mask") & mask
    observed = _boolean_mask(truth, mask.shape, "truth mask") & mask
    intersection = int(np.sum(pred & observed))
    predicted_count = int(np.sum(pred))
    truth_count = int(np.sum(observed))
    union = predicted_count + truth_count - intersection
    both_empty = predicted_count == 0 and truth_count == 0
    area = _cell_hectares(cell_m)
    return {
        "dice": 1.0 if both_empty else float(2 * intersection / (predicted_count + truth_count)),
        "iou": 1.0 if both_empty else float(intersection / union),
        "false_positive_ha": float(int(np.sum(pred & ~observed)) * area),
        "false_negative_ha": float(int(np.sum(~pred & observed)) * area),
        "both_empty": both_empty,
        "valid_cells": int(np.sum(mask)),
    }


def brier_score(
    probability: np.ndarray, truth: np.ndarray, valid: np.ndarray
) -> tuple[float | None, str | None]:
    field = _probability_field(probability)
    observed = _boolean_mask(truth, field.shape, "truth mask")
    mask = _boolean_mask(valid, field.shape, "valid mask")
    if not np.any(mask):
        return None, "no valid truth cells"
    error = field[mask] - observed[mask].astype(np.float64)
    return float(np.mean(error * error)), None


def reliability_table(
    probability: np.ndarray, truth: np.ndarray, valid: np.ndarray
) -> dict[str, object]:
    field = _probability_field(probability)
    observed = _boolean_mask(truth, field.shape, "truth mask")
    mask = _boolean_mask(valid, field.shape, "valid mask")
    bins = []
    for index in range(RELIABILITY_BINS):
        lower = index / RELIABILITY_BINS
        upper = (index + 1) / RELIABILITY_BINS
        chosen = mask & (_bin_index(field) == index)
        count = int(np.sum(chosen))
        bins.append(
            {
                "bin": index,
                "lower_inclusive": lower,
                "upper": upper,
                "upper_inclusive": index == RELIABILITY_BINS - 1,
                "count": count,
                "mean_probability": None if count == 0 else float(np.mean(field[chosen])),
                "observed_fraction": None
                if count == 0
                else float(np.mean(observed[chosen].astype(np.float64))),
            }
        )
    return {
        "bin_count": RELIABILITY_BINS,
        "note": RELIABILITY_NOTE,
        "bins": bins,
    }


def rounded_quantile_burned(t_q: np.ndarray, lead_min: object) -> np.ndarray:
    """q50 contour in lead minutes. NaN is beyond the horizon, not a burned cell."""
    field = np.asarray(t_q)
    lead = _whole_minutes(lead_min, "lead_min")
    if lead < 0:
        raise ConfigError("lead_min must be nonnegative")
    if field.ndim != 3 or field.shape[0] != 5 or field.dtype.kind != "f":
        raise ConfigError("quantile field must be floating point with shape (5, height, width)")
    plane = field[Q50_INDEX]
    return np.isfinite(plane) & (plane <= lead)


def assert_same_grid(prediction: dict[str, object], truth: dict[str, object]) -> None:
    keys = ("crs", "x0", "y0", "cell_m", "height", "width", "t_ref_s")
    for key in keys:
        if key not in prediction or key not in truth or not _grid_value_equal(
            prediction[key], truth[key]
        ):
            raise ConfigError("prediction grid does not match the truth grid")


def assert_single_lead(rows: list[dict[str, object]]) -> None:
    leads = {row["lead_min"] for row in rows}
    if len(leads) != 1:
        raise ConfigError("different lead times cannot be mixed into an unlabeled aggregate")


def score_raw_arrival_field(
    *,
    arrivals: np.ndarray,
    weights: np.ndarray,
    truth: np.ndarray,
    valid: np.ndarray | None,
    issue_min: object,
    lead_min: object,
    cell_m: float,
    persistence: bool,
    prediction_grid: dict[str, object] | None = None,
    truth_grid: dict[str, object] | None = None,
    t_q: np.ndarray | None = None,
) -> dict[str, object]:
    if prediction_grid is not None or truth_grid is not None:
        if prediction_grid is None or truth_grid is None:
            raise ConfigError("prediction grid does not match the truth grid")
        assert_same_grid(prediction_grid, truth_grid)
    target = absolute_target_min(issue_min, lead_min)
    probability_time = absolute_target_min(issue_min, 0) if persistence else target
    probability = weighted_burn_probability(arrivals, weights, probability_time)
    mask = declared_valid(truth) if valid is None else _boolean_mask(
        valid, _truth_field(truth).shape, "valid mask"
    )
    observed = truth_burned_at(truth, target, mask)
    if probability.shape != observed.shape:
        raise ConfigError("prediction grid does not match the truth grid")
    brier, brier_reason = brier_score(probability, observed, mask)
    masks = score_masks(predicted_burned(probability), observed, mask, cell_m)
    row: dict[str, object] = {
        "issue_min": _whole_minutes(issue_min, "issue_min"),
        "lead_min": _whole_minutes(lead_min, "lead_min"),
        "target_min": target,
        "probability_time_min": probability_time,
        "probability_source": "frozen_issue_probability" if persistence else "raw_weighted_arrivals",
        "probability_sum": float(np.sum(probability)),
        "brier": brier,
        "brier_reason": brier_reason,
        "reliability": reliability_table(probability, observed, mask),
        **masks,
    }
    row["rounded_quantile_q50"] = _quantile_score(t_q, lead_min, observed, mask, cell_m)
    return row


def _quantile_score(t_q, lead_min, observed, mask, cell_m) -> dict[str, object]:
    label = "rounded_quantile_q50"
    definition = "np.isfinite(t_q[2]) & (t_q[2] <= lead)"
    if t_q is None:
        return {
            "label": label,
            "definition": definition,
            "dice": None,
            "iou": None,
            "reason": "quantile field was not stored; raw arrivals remain the primary score",
        }
    contour = rounded_quantile_burned(t_q, lead_min)
    if contour.shape != observed.shape:
        raise ConfigError("prediction grid does not match the truth grid")
    masks = score_masks(contour, observed, mask, cell_m)
    return {
        "label": label,
        "definition": definition,
        "dice": masks["dice"],
        "iou": masks["iou"],
        "reason": None,
    }


def _bin_index(probability: np.ndarray) -> np.ndarray:
    # A tiny offset keeps exact tenth boundaries in the upper bin despite binary rounding.
    tenths = np.floor(np.asarray(probability, dtype=np.float64) * RELIABILITY_BINS + 1e-12)
    return np.minimum(tenths, RELIABILITY_BINS - 1).astype(int)


def _probability_field(probability: np.ndarray) -> np.ndarray:
    field = np.asarray(probability)
    if field.ndim != 2 or field.dtype.kind != "f":
        raise ConfigError("probability must be a floating-point grid")
    if not np.isfinite(field).all() or np.any(field < -1e-8) or np.any(field > 1 + 1e-8):
        raise ConfigError("probability must be within [0, 1]")
    return np.clip(field, 0.0, 1.0)


def _truth_field(truth_arrivals: np.ndarray) -> np.ndarray:
    truth = np.asarray(truth_arrivals)
    if truth.ndim == 3:
        if truth.shape[0] != 1:
            raise ConfigError("truth arrivals must be one field")
        truth = truth[0]
    if truth.ndim != 2 or truth.dtype.kind != "f":
        raise ConfigError("truth arrivals must be a floating-point grid")
    return truth


def _boolean_mask(mask: np.ndarray, shape: tuple[int, ...], name: str) -> np.ndarray:
    array = np.asarray(mask)
    if array.shape != shape:
        raise ConfigError(f"{name} does not match the grid")
    if array.dtype == np.bool_:
        return array
    if array.dtype.kind not in "iu" or not np.isin(array, (0, 1)).all():
        raise ConfigError(f"{name} must be boolean")
    return array.astype(bool)


def _cell_hectares(cell_m: float) -> float:
    if isinstance(cell_m, bool) or not isinstance(cell_m, (int, float)) or not math.isfinite(cell_m):
        raise ConfigError("cell size must be finite and positive")
    if cell_m <= 0:
        raise ConfigError("cell size must be finite and positive")
    return float(cell_m) ** 2 / 10000.0


def _whole_minutes(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ConfigError(f"{name} must be an integer number of minutes")
    return int(value)


def _grid_value_equal(left: object, right: object) -> bool:
    if isinstance(left, str) or isinstance(right, str):
        return left == right
    if isinstance(left, bool) or isinstance(right, bool):
        return False
    try:
        return float(left) == float(right)
    except (TypeError, ValueError):
        return False
