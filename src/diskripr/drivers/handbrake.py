"""Driver for ``HandBrakeCLI`` — optional video re-encoding.

Exposes one method on :class:`HandBrakeDriver`:

- ``encode(title_index, input_path, output_path, encoder, quality, on_progress)``
    Build and execute a ``HandBrakeCLI`` command with the following fixed
    requirements:
    - Copy all audio tracks without re-encoding (``--all-audio --aencoder copy``).
    - Preserve all subtitle tracks without burning in
      (``--all-subtitles --subtitle-burned=none``).
    - Preserve chapter markers (``--markers``).
    - Optimize for streaming (``--optimize``).
    - Quality (RF value) is configurable.
    Stream progress output and invoke ``on_progress`` callbacks. Return an
    ``EncodeResult`` including before/after file sizes.

Raises ``ToolNotFound`` if ``HandBrakeCLI`` is not found on PATH. The pipeline
catches this to skip encoding gracefully rather than aborting.

On encoding failure, raise ``EncodeError`` so the pipeline can fall back to
the original MKV and log a warning.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from diskripr.drivers.base import BaseDriver, EncodeError, ToolError
from diskripr.models import EncodeResult
from diskripr.util.progress import ProgressCallback, ProgressEvent

log = logging.getLogger(__name__)

# Map config encoder names to HandBrakeCLI encoder identifiers.
_ENCODER_MAP: dict[str, str] = {
    "h264": "x264",
    "h265": "x265",
}

# HandBrakeCLI progress line pattern.
# Example: "Encoding: task 1 of 1, 45.67 % (150.23 fps, avg 148.90 fps, ETA 00h01m23s)"
_PROGRESS_RE = re.compile(r"Encoding: task \d+ of \d+,\s+([\d.]+)\s+%")


class HandBrakeDriver(BaseDriver):
    """Driver for ``HandBrakeCLI`` re-encoding.

    Inherits subprocess management and PID tracking from
    :class:`~diskripr.drivers.base.BaseDriver`.  ``active_pid`` is set while
    an encode is streaming so callers can cancel by sending a signal if needed.

    Usage::

        driver = HandBrakeDriver()
        if driver.is_available():
            result = driver.encode(
                title_index=0,
                input_path=Path("title_t00.mkv"),
                output_path=Path("title_t00_encoded.mkv"),
                encoder="h265",
                quality=22,
            )
    """

    binary = "HandBrakeCLI"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def encode(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        self,
        title_index: int,
        input_path: Path,
        output_path: Path,
        encoder: str,
        quality: int,
        on_progress: Optional[ProgressCallback] = None,
    ) -> EncodeResult:
        """Re-encode *input_path* to *output_path* using HandBrakeCLI.

        Fixed encoding options applied on every call:

        - All audio tracks are copied without re-encoding.
        - All subtitle tracks are preserved without burn-in.
        - Chapter markers are preserved.
        - Output is optimised for streaming (faststart).

        ``self.active_pid`` is set for the duration of the encode so callers
        can send a cancellation signal if required.

        Args:
            title_index:  The source title index carried through to the result.
            input_path:   Source MKV file produced by the rip stage.
            output_path:  Destination path for the encoded MKV.
            encoder:      Encoding format: ``"h264"`` or ``"h265"``.
            quality:      HandBrake RF quality value (lower = higher quality).
                          Typical values: 20 for H.264, 22 for H.265.
            on_progress:  Optional progress callback; called with stage
                          ``"encode"`` and a 0–100 current/total pair.

        Returns:
            :class:`~diskripr.models.EncodeResult` with ``success=True`` and
            both file sizes on completion, or ``success=False`` with an error
            message if no output file is found.

        Raises:
            ToolNotFound: ``HandBrakeCLI`` is not on PATH.
            EncodeError:  ``HandBrakeCLI`` exited with a non-zero return code.
        """
        self.require_available()

        original_size_bytes = (
            input_path.stat().st_size if input_path.exists() else None
        )

        args = self._build_args(input_path, output_path, encoder, quality)
        log.debug(
            "encode: title=%d  encoder=%s  quality=%d  in=%s  out=%s",
            title_index, encoder, quality, input_path, output_path,
        )

        try:
            for line in self.stream(args):
                log.debug("handbrake: %s", line)
                self._handle_progress_line(line, on_progress)
        except ToolError as exc:
            raise EncodeError(exc.command, exc.returncode, exc.stderr) from exc

        success = output_path.exists()
        encoded_size_bytes = output_path.stat().st_size if success else None

        return EncodeResult(
            title_index=title_index,
            output_path=output_path if success else None,
            success=success,
            error_message=None if success else "No output file found after encode",
            original_size_bytes=original_size_bytes,
            encoded_size_bytes=encoded_size_bytes,
        )

    # ------------------------------------------------------------------
    # Helpers (static — no driver state needed)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_args(
        input_path: Path,
        output_path: Path,
        encoder: str,
        quality: int,
    ) -> list[str]:
        """Build the ``HandBrakeCLI`` argument list for a single encode."""
        hb_encoder = _ENCODER_MAP.get(encoder, encoder)
        return [
            "HandBrakeCLI",
            "-i", str(input_path),
            "-o", str(output_path),
            "-e", hb_encoder,
            "-q", str(quality),
            "--all-audio",
            "--aencoder", "copy",
            "--all-subtitles",
            "--subtitle-burned=none",
            "--markers",
            "--optimize",
        ]

    @staticmethod
    def _parse_progress(line: str) -> Optional[float]:
        """Return the percentage complete from an ``Encoding:`` line, or ``None``.

        Parses lines of the form::

            Encoding: task 1 of 1, 45.67 % (150.23 fps, avg 148.90 fps, ETA 00h01m23s)
        """
        match = _PROGRESS_RE.match(line)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def _handle_progress_line(
        line: str,
        on_progress: Optional[ProgressCallback],
    ) -> None:
        """Parse *line* and invoke *on_progress* if it is an ``Encoding:`` line."""
        if on_progress is None:
            return
        match = _PROGRESS_RE.match(line)
        if not match:
            return
        try:
            pct = float(match.group(1))
        except ValueError:
            return
        on_progress(ProgressEvent(
            stage="encode",
            current=int(pct),
            total=100,
        ))
