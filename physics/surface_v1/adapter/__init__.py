"""Research compatibility layer for the surface_v1 Rust engine.

This package converts documented array conventions and drives the existing
``fire-cli`` executable. It is not an application backend and it does not
implement ``HazardModel``.
"""

from __future__ import annotations

from adapter.errors import AdapterError, BridgeError, UnsupportedMapping, ValidationError

__all__ = [
    "AdapterError",
    "BridgeError",
    "UnsupportedMapping",
    "ValidationError",
]
