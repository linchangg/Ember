"""Unconditional weighted arrival CDF and frontend-compatible encoding."""

from __future__ import annotations
import numpy as np


def summarize_arrivals(arrivals, weights, issue_min, horizon_min, quantiles):
    arrivals = np.asarray(arrivals, dtype=np.float32)
    weights = np.asarray(weights, dtype=np.float64)
    if arrivals.ndim != 3 or weights.shape != (arrivals.shape[0],):
        raise ValueError("expected arrivals (N,H,W) and weights (N,)")
    if np.isnan(arrivals).any() or np.isneginf(arrivals).any():
        raise ValueError("arrival times must be finite or +inf")
    if not np.isfinite(weights).all() or (weights < 0).any() or weights.sum() <= 0:
        raise ValueError("invalid ensemble weights")
    weights = weights / weights.sum()
    order = np.argsort(arrivals, axis=0, kind="stable")
    times = np.take_along_axis(arrivals, order, axis=0)
    cumulative = np.cumsum(weights[order], axis=0)
    cumulative[-1] = 1.0
    result = np.full((len(quantiles), *arrivals.shape[1:]), np.nan, dtype=np.float32)
    for k, q in enumerate(quantiles):
        if not 0 < q <= 1:
            raise ValueError("quantiles must be in (0,1]")
        rank = np.argmax(cumulative >= q, axis=0)
        absolute = np.take_along_axis(times, rank[None], axis=0)[0]
        finite = np.isfinite(absolute)
        lead = np.maximum(absolute - issue_min, 0.0)
        binned = np.ceil(lead / 2.0) * 2.0
        result[k] = np.where(finite & (binned <= horizon_min), binned, np.nan)
    burned = np.sum(weights[:, None, None] * (arrivals <= issue_min), axis=0).astype(
        np.float32
    )
    return result, burned


def encode_arrival(t_q):
    """spec/00 §4.2: north-first uint8, zero burned, 255 unreached."""
    values = np.asarray(t_q)
    encoded = np.full(values.shape, 255, dtype=np.uint8)
    finite = np.isfinite(values)
    encoded[finite] = np.clip(
        np.ceil(np.maximum(values[finite], 0) / 2), 0, 254
    ).astype(np.uint8)
    return encoded


def region_arrivals(arrivals, index, n_regions):
    index = np.asarray(index)
    if (
        index.shape != arrivals.shape[1:]
        or index.dtype.kind not in "iu"
        or n_regions < 0
    ):
        raise ValueError("invalid region index")
    if (index < -1).any() or (index >= n_regions).any():
        raise ValueError("region ids outside [-1,n_regions)")
    result = np.full((arrivals.shape[0], n_regions), np.inf, dtype=np.float32)
    for region in range(n_regions):
        cells = index == region
        if cells.any():
            result[:, region] = arrivals[:, cells].min(axis=1)
    return result
