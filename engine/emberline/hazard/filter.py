"""Deterministic numerical primitives for the tempered particle filter."""

from __future__ import annotations
import numpy as np


def normalize(log_weights):
    log_weights = np.asarray(log_weights, dtype=np.float64)
    if not np.isfinite(log_weights).all():
        raise ValueError("nonfinite log weights")
    exp = np.exp(log_weights - log_weights.max())
    return exp / exp.sum()


def ess(weights):
    return float(1 / np.sum(np.asarray(weights, dtype=np.float64) ** 2))


def temper(logw, ll, target_fraction):
    target = target_fraction * len(logw)
    if not 0 < target_fraction <= 1:
        raise ValueError("ESS target must be in (0,1]")
    if ess(normalize(logw + ll)) >= target:
        return normalize(logw + ll), 1.0
    lo, hi = 0.0, 1.0
    for _ in range(20):
        mid = (lo + hi) / 2
        if ess(normalize(logw + mid * ll)) >= target:
            lo = mid
        else:
            hi = mid
    return normalize(logw + lo * ll), lo


def systematic_resample(weights, rng):
    cumulative = np.cumsum(weights)
    cumulative[-1] = 1.0
    points = (rng.random() + np.arange(len(weights))) / len(weights)
    return np.searchsorted(cumulative, points, side="right")


def liu_west(parameters, weights, parents, rng, a):
    """Rows: log(ROS), log(wind scale), angle radians, log(dead moisture), log(urban ROS)."""
    if not 0 < a <= 1:
        raise ValueError("jitter_a must be in (0,1]")
    values = parameters.copy()
    # Unwrap around the weighted circular mean before estimating local angle covariance.
    angle = np.arctan2(
        np.sum(weights * np.sin(values[:, 2])), np.sum(weights * np.cos(values[:, 2]))
    )
    values[:, 2] = angle + (values[:, 2] - angle + np.pi) % (2 * np.pi) - np.pi
    mean = np.sum(weights[:, None] * values, axis=0)
    centered = values - mean
    covariance = (centered * weights[:, None]).T @ centered
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    noise = (
        rng.standard_normal((len(parents), values.shape[1]))
        @ (eigenvectors * np.sqrt(np.maximum(eigenvalues, 0) * (1 - a * a))).T
    )
    result = a * values[parents] + (1 - a) * mean + noise
    result[:, 2] = (result[:, 2] + np.pi) % (2 * np.pi) - np.pi
    result[:, 3] = np.clip(result[:, 3], np.log(0.02), np.log(0.12))
    return result
