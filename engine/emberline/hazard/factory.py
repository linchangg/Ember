"""One integration point for Cody's runtime; no frontend or contract changes."""

from .bridge import BackendOptions
from .model import RustHazardModel


def create_hazard_model(*, backend="rust", options: BackendOptions | None = None):
    if backend != "rust":
        raise ValueError(f"unknown HazardModel backend {backend!r}")
    return RustHazardModel(options)
