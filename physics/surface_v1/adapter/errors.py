"""Errors for the surface_v1 research adapter.

These are research-layer failures. They do not change Rust exit codes or the
unlanded application contracts.
"""

from __future__ import annotations


class AdapterError(Exception):
    """Base error. ``code`` is a stable short token for tests and logs."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class ValidationError(AdapterError):
    def __init__(self, message: str, *, code: str = "validation") -> None:
        super().__init__(code, message)


class UnsupportedMapping(AdapterError):
    """A production mapping was requested that this layer refuses to invent."""

    def __init__(self, message: str) -> None:
        super().__init__("unsupported_mapping", message)


class BridgeError(AdapterError):
    """Subprocess or published-output failure.

    ``code`` is one of ``validation_failure``, ``numerical_failure``,
    ``timeout``, ``cancellation``, ``malformed_output``, or ``memory_budget``.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        returncode: int | None = None,
        stdout: str = "",
        stderr: str = "",
        work_dir: str | None = None,
    ) -> None:
        super().__init__(code, message)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.work_dir = work_dir
