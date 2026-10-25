"""Observation likelihood registry from spec/02 §7, evaluated at observation time."""

from __future__ import annotations
import numpy as np


def residence_minutes(fuel):
    fuel = np.asarray(fuel)
    return np.where(
        fuel == 91,
        180,
        np.where(
            (fuel >= 101) & (fuel <= 109),
            15,
            np.where((fuel >= 121) & (fuel <= 149), 45, 90),
        ),
    ).astype(np.float32)


class SatelliteLikelihood:
    @staticmethod
    def loglik(arrivals, observation, prepared, tau, cell_area_ha, observed_min):
        active = (arrivals <= observed_min) & (arrivals >= observed_min - tau)
        area = np.sum(active * prepared["spatial"], axis=(1, 2)) * cell_area_ha
        pmax, a0 = (0.85, 1.0) if observation.kind == "goes_pixel" else (0.95, 0.05)
        pd = pmax * (-np.expm1(-area / a0))
        sensor = 0.002 + (1 - 0.002) * pd
        return trusted_log(sensor if observation.hot else 1 - sensor, observation.trust)


class GroundReportLikelihood:
    @staticmethod
    def loglik(arrivals, observation, prepared, tau, cell_area_ha, observed_min):
        q = np.max((arrivals <= observed_min + 10) * prepared["spatial"], axis=(1, 2))
        sensor = 0.05 + 0.95 * q if observation.hot else 1 - q
        return trusted_log(sensor, observation.trust)


class CameraLikelihood:
    @staticmethod
    def loglik(arrivals, observation, prepared, tau, cell_area_ha, observed_min):
        active = (arrivals <= observed_min) & (arrivals >= observed_min - tau)
        q = np.max(active * prepared["spatial"], axis=(1, 2))
        sensor = 0.05 + 0.95 * q if observation.hot else 1 - q
        return trusted_log(sensor, observation.trust)


def trusted_log(sensor, trust):
    if not np.isfinite(trust) or not 0 <= trust <= 1:
        raise ValueError("observation trust must be in [0,1]")
    return np.log(np.clip((1 - trust) + trust * sensor, 1e-300, 1))


LIKELIHOODS = {
    "goes_pixel": SatelliteLikelihood,
    "viirs_pixel": SatelliteLikelihood,
    "thermal_footprint": SatelliteLikelihood,
    "ground_report": GroundReportLikelihood,
    "camera_bearing": CameraLikelihood,
}
