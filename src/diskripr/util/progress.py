"""Progress reporting protocol for long-running pipeline operations.

Defines a ``ProgressCallback`` Protocol — a callable that accepts a single
``ProgressEvent`` argument — which drivers use to emit progress during rip and
encode operations. Keeping progress emission as a protocol means:

- The CLI can implement it with Click's progress bar.
- Tests can pass a no-op lambda or a list-collector for assertions.
- Library internals stay free of any presentation or I/O logic.

``ProgressEvent`` dataclass fields:

- ``stage``    — Name of the current pipeline stage (e.g. ``"rip"``, ``"encode"``).
- ``current``  — Current progress value (e.g. bytes written, frames encoded).
- ``total``    — Total expected value; 0 means indeterminate.
- ``message``  — Optional human-readable status string from the tool's output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class ProgressEvent:
    """A single progress update emitted by a driver during a long operation."""

    stage: str
    current: int
    total: int
    message: Optional[str] = None


class ProgressCallback(Protocol):  # pylint: disable=too-few-public-methods
    """Callable protocol accepted by drivers that emit progress events."""

    def __call__(self, event: ProgressEvent) -> None:
        ...
