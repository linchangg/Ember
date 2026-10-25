"""Opt-in research likelihood; not registered in the production filter.

Haley (2022), §§2.3.2–2.3.3, equations 2.3.8–2.3.16:
https://arxiv.org/html/2204.00686v1
Parameters must be estimated on calibration incidents, not copied across sensors.
"""

from __future__ import annotations

import numpy as np
from scipy.special import expit


def gaussian_footprint(xy, center, covariance):
    """Discrete, normalized location uncertainty; coordinates/covariance use metres."""
    xy = np.asarray(xy, dtype=float)
    center = np.asarray(center, dtype=float)
    covariance = np.asarray(covariance, dtype=float)
    if (
        xy.ndim != 3
        or xy.shape[-1] != 2
        or center.shape != (2,)
        or covariance.shape != (2, 2)
        or not np.isfinite(xy).all()
        or not np.isfinite(center).all()
        or not np.isfinite(covariance).all()
        or not np.allclose(covariance, covariance.T)
    ):
        raise ValueError("invalid footprint geometry")
    try:
        chol = np.linalg.cholesky(covariance)
    except np.linalg.LinAlgError as exc:
        raise ValueError("covariance must be positive definite") from exc
    delta = np.linalg.solve(chol, (xy - center).reshape(-1, 2).T).T
    log_kernel = -0.5 * np.sum(delta**2, axis=1)
    kernel = np.exp(log_kernel - log_kernel.max()).reshape(xy.shape[:2])
    return kernel / kernel.sum()


def heat_detection_probability(arrivals, observed_min, spatial, *, decay_min, a, b):
    """Return P(detection|member) using expit(a*heat-b), averaged over location.

    Arrivals: (members, north-first rows, columns), minutes since model reference;
    +inf is unreached. Spatial weights sum to one, not active area in hectares.
    This is an alternative observation model, not a drop-in reuse of A0/pmax values.
    """
    arrivals = np.asarray(arrivals, dtype=float)
    spatial = np.asarray(spatial, dtype=float)
    if (
        arrivals.ndim != 3
        or min(arrivals.shape) == 0
        or spatial.shape != arrivals.shape[1:]
        or np.isnan(arrivals).any()
        or np.isneginf(arrivals).any()
        or not np.isfinite(spatial).all()
        or (spatial < 0).any()
        or not np.isclose(spatial.sum(), 1)
        or not np.isfinite([observed_min, decay_min, a, b]).all()
        or decay_min <= 0
        or a <= 0
    ):
        raise ValueError("invalid heat likelihood inputs")
    spatial = spatial / spatial.sum()
    age = observed_min - arrivals
    heat = np.exp(-np.maximum(age, 0) / decay_min)
    heat = np.where(age >= 0, heat, 0)
    return np.sum(expit(a * heat - b) * spatial, axis=(1, 2))
