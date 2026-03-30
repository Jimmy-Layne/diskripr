"""Base driver class and subprocess utilities for the diskripr driver layer.

Defines the ``BaseDriver`` abstract base that every tool-specific driver
subclasses.  Subclasses set the ``binary`` class variable to the name of the
external binary they wrap and call ``self.run()`` / ``self.stream()`` instead
of invoking ``subprocess`` directly.

``BaseDriver`` provides:

- ``is_available()``      — Return ``True`` if ``self.binary`` is on PATH.
- ``require_available()`` — Raise ``ToolNotFound`` if the binary is absent.
- ``run(args, ...)``      — Run a command to completion. Tracks ``active_pid``
                            while the subprocess runs. Raises ``ToolError``
                            on a non-zero return code.
- ``stream(args, ...)``   — Run a command and yield stdout lines in real time.
                            Tracks ``active_pid`` for the duration of the
                            stream; clears it in a ``finally`` block so the
                            PID is always released even if the caller breaks
                            out of the loop early.

``active_pid`` is ``None`` when no subprocess is running and holds the integer
OS process ID while one is active.  Callers that need to cancel a long-running
operation (e.g. a rip) can read this attribute and send a signal.

Module-level utilities kept for convenience:

- ``check_available(binary)`` — PATH check without a driver instance.

Exception hierarchy:

- ``ToolError``    — Non-zero exit code; carries command, returncode, stderr.
- ``ToolNotFound`` — Binary not found on PATH.
- ``RipError``     — Title-level rip failure (subclass of ``ToolError``).
- ``EncodeError``  — Title-level encode failure (subclass of ``ToolError``).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections.abc import Iterator, Sequence
from typing import ClassVar, Optional

log = logging.getLogger(__name__)

#: Default timeout in seconds for non-streaming subprocess invocations.
DEFAULT_TIMEOUT: int = 60


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------

class ToolError(Exception):
    """A driver invocation returned a non-zero exit code.

    Attributes:
        command:    The full argument list that was executed.
        returncode: The process exit code.
        stderr:     Captured stderr output (may be empty for streaming calls).
    """

    def __init__(self, command: list[str], returncode: int, stderr: str) -> None:
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        binary = command[0] if command else "<unknown>"
        detail = stderr.strip() or "(no stderr output)"
        super().__init__(
            f"Command {binary!r} exited with code {returncode}: {detail}"
        )


class ToolNotFound(Exception):
    """A required or optional binary was not found on PATH.

    Attributes:
        binary: The binary name that was searched for.
    """

    def __init__(self, binary: str) -> None:
        self.binary = binary
        super().__init__(
            f"Tool {binary!r} not found on PATH. "
            f"Check that it is installed and accessible."
        )


class RipError(ToolError):
    """A title-level rip failure reported by makemkvcon."""


class EncodeError(ToolError):
    """A title-level encode failure reported by HandBrakeCLI."""


# ---------------------------------------------------------------------------
# Module-level utility
# ---------------------------------------------------------------------------

def check_available(binary: str) -> bool:
    """Return ``True`` if *binary* is found on PATH, ``False`` otherwise.

    Uses ``shutil.which`` so the result matches what ``subprocess`` would do.
    Useful for checking optional tools (ffprobe, HandBrakeCLI) without
    constructing a driver instance.
    """
    return shutil.which(binary) is not None


# ---------------------------------------------------------------------------
# Base driver
# ---------------------------------------------------------------------------

class BaseDriver:
    """Base class for all diskripr tool drivers.

    Subclasses must set the ``binary`` class variable to the name of the
    external tool they wrap::

        class MakeMKVDriver(BaseDriver):
            binary = "makemkvcon"

    Instance attributes:

    - ``active_pid`` (``Optional[int]``) — OS PID of the currently running
      subprocess, or ``None`` when idle.  Set before the first byte of output
      arrives and cleared in a ``finally`` block after the process exits (or
      after the stream generator is closed).
    """

    binary: ClassVar[str]

    def __init__(self) -> None:
        self.active_pid: Optional[int] = None

    # ------------------------------------------------------------------
    # Availability helpers
    # ------------------------------------------------------------------

    def is_available(self) -> bool:
        """Return ``True`` if ``self.binary`` is found on PATH."""
        return check_available(self.binary)

    def require_available(self) -> None:
        """Raise :class:`ToolNotFound` if ``self.binary`` is not on PATH."""
        if not self.is_available():
            raise ToolNotFound(self.binary)

    # ------------------------------------------------------------------
    # Subprocess helpers
    # ------------------------------------------------------------------

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
        """Run *args* to completion and return the ``CompletedProcess`` result.

        Sets ``self.active_pid`` for the duration of the call and clears it
        on exit (including on timeout or exception).  Captures stdout and
        stderr.  Logs the invocation at DEBUG level.

        Args:
            args:    Full command and argument list.
            timeout: Seconds before the subprocess is killed on timeout.
                     Defaults to :data:`diskripr.drivers.base.DEFAULT_TIMEOUT`.

        Raises:
            ToolError: The process exited with a non-zero return code.
            subprocess.TimeoutExpired: The process did not finish in time.
        """
        command = list(args)
        log.debug("%s.run: %s", self.__class__.__name__, command)
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        ) as proc:
            self.active_pid = proc.pid
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                raise
            finally:
                self.active_pid = None
        if proc.returncode != 0:
            raise ToolError(command, proc.returncode, stderr or "")
        return subprocess.CompletedProcess(command, proc.returncode, stdout, stderr)

    def stream(
        self,
        args: Sequence[str],
        *,
        timeout: int | None = None,
    ) -> Iterator[str]:
        """Run *args* via Popen and yield stdout lines as they arrive.

        Sets ``self.active_pid`` when the process starts and clears it in a
        ``finally`` block so the PID is always released — even when the caller
        breaks out of the iteration loop early or the generator is garbage
        collected.

        stderr is merged into stdout so that tool error messages appear
        alongside progress output (appropriate for MakeMKV and HandBrake,
        which write all meaningful output to stdout).

        Each yielded string is a single line with the trailing newline
        stripped.  After the stream is exhausted the process exit code is
        checked and :class:`ToolError` is raised if it is non-zero.

        Args:
            args:    Full command and argument list.
            timeout: Seconds to wait for the process to exit *after* the
                     output stream ends.  ``None`` means wait indefinitely.

        Raises:
            ToolError: The process exited with a non-zero return code.
            subprocess.TimeoutExpired: The process did not exit within
                                       *timeout* after its stream closed.

        Example::

            for line in self.stream(["makemkvcon", "mkv", ...]):
                if line.startswith("PRGV:"):
                    ...
        """
        command = list(args)
        log.debug("%s.stream: %s", self.__class__.__name__, command)
        with subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as proc:
            self.active_pid = proc.pid
            try:
                assert proc.stdout is not None  # guaranteed by stdout=PIPE
                for line in proc.stdout:
                    yield line.rstrip("\n")
                proc.wait(timeout=timeout)
                if proc.returncode != 0:
                    raise ToolError(command, proc.returncode, "")
            finally:
                self.active_pid = None
