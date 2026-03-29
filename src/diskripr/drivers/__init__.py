"""Driver sub-package for diskripr.

Each external tool (makemkvcon, HandBrakeCLI, ffprobe, lsdvd) is wrapped in
its own module. Application code never calls ``subprocess`` directly — all
invocations go through a driver.

Re-exports the exception hierarchy used across all drivers:

- ``ToolError``     — A driver invocation returned a non-zero exit code.
                      Carries command, return code, and captured stderr.
- ``ToolNotFound``  — A required or optional binary was not found on PATH.
- ``RipError``      — A title-level rip failure reported by makemkvcon.
- ``EncodeError``   — A title-level encode failure reported by HandBrakeCLI.
"""

from diskripr.drivers.base import (
    EncodeError,
    RipError,
    ToolError,
    ToolNotFound,
)

__all__ = ["EncodeError", "RipError", "ToolError", "ToolNotFound"]
