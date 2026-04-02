"""Driver for ``makemkvcon`` — disc scanning and title ripping.

This is the most complex driver due to MakeMKV's ad-hoc line-oriented output
format. Exposes three methods on :class:`MakeMKVDriver`:

- ``scan_drives()``
    Run ``makemkvcon -r info disc:9999`` to discover all connected drives.
    Parse the output and return a list of ``DriveInfo`` objects. Used to match
    the user-specified device path (e.g. ``/dev/sr0``) to the correct MakeMKV
    drive index.

- ``scan_titles(drive_index)``
    Run ``makemkvcon -r info disc:<N>`` for a specific drive. Parse TINFO
    output lines to extract per-title metadata (name, duration, size, chapter
    count, stream summary) and return a list of ``Title`` dataclasses. Applies
    heuristic type tagging: the longest title is ``"main"``; others are
    classified as ``"feature-length"``, ``"extra"``, or ``"short"`` by
    duration thresholds.

- ``rip_title(drive_index, title_index, output_dir, min_length, on_progress)``
    Run ``makemkvcon mkv disc:<N> <title> <output_dir> --minlength=<N>``.
    Stream stdout, parse ``PRGV:`` lines for percentage progress callbacks and
    ``MSG:`` lines for status and error messages. Return a ``RipResult``. Raise
    ``RipError`` on unrecoverable failure.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from pathlib import Path
from typing import Optional

from diskripr.drivers.base import BaseDriver, RipError, ToolError
from diskripr.models import DriveInfo, RipResult, Title, TitleType
from diskripr.util.progress import ProgressCallback, ProgressEvent

log = logging.getLogger(__name__)

# Duration thresholds for heuristic title-type classification (seconds).
_FEATURE_LENGTH_MIN_SEC = 45 * 60   # >= 45 min → "feature-length"
_EXTRA_MIN_SEC = 10 * 60            # >= 10 min → "extra"; else → "short"

# TINFO attribute IDs (AP_ItemAttributeId values from MakeMKV robot format).
_ATTR_NAME = 2
_ATTR_CHAPTER_COUNT = 8
_ATTR_DURATION = 9
_ATTR_SIZE_BYTES = 11
_ATTR_STREAM_SUMMARY = 27

# MakeMKV progress counter maximum (PRGV third field).
_PRGV_MAX = 65536

# MSG codes at or above this threshold indicate a rip error.
_MSG_ERROR_CODE_MIN = 5000

# Normalised duration pattern: allow single-digit hours from MakeMKV output.
_DURATION_RE = re.compile(r"^(\d+):(\d{2}):(\d{2})$")


def _duration_to_seconds(duration: str) -> int:
    """Convert a normalised HH:MM:SS string to total seconds."""
    match = _DURATION_RE.match(duration)
    if not match:
        return 0
    hours, minutes, seconds = (int(part) for part in match.groups())
    return hours * 3600 + minutes * 60 + seconds


def _classify_title_type(duration_seconds: int, is_longest: bool) -> TitleType:
    """Return the heuristic title type for a single title."""
    if is_longest:
        return "main"
    if duration_seconds >= _FEATURE_LENGTH_MIN_SEC:
        return "feature-length"
    if duration_seconds >= _EXTRA_MIN_SEC:
        return "extra"
    return "short"


class MakeMKVDriver(BaseDriver):
    """Driver for ``makemkvcon`` operations.

    Inherits subprocess management and PID tracking from
    :class:`~diskripr.drivers.base.BaseDriver`.  The ``active_pid`` attribute
    is set while a rip is streaming so callers can cancel the process by
    sending a signal if required.

    Usage::

        driver = MakeMKVDriver()
        drives = driver.scan_drives()
        titles = driver.scan_titles(drives[0].drive_index)
        result = driver.rip_title(drives[0].drive_index, titles[0].index, output_dir)
    """

    binary = "makemkvcon"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan_drives(self) -> list[DriveInfo]:
        """Discover all connected optical drives with an accessible disc.

        Runs ``makemkvcon -r info disc:9999`` and parses ``DRV:`` lines.
        Only drives where the accessibility flag equals ``2`` (disc present
        and readable) are returned.

        Returns:
            List of :class:`~diskripr.models.DriveInfo` objects, one per
            accessible drive.  Empty if no suitable drive is found.

        Raises:
            ToolNotFound: ``makemkvcon`` is not on PATH.
            ToolError: ``makemkvcon`` exited with a non-zero return code.
        """
        self.require_available()
        log.debug("Scanning for accessible optical drives via makemkvcon")
        result = self.run([self.binary, "-r", "info", "disc:9999"], timeout=30)
        drives = []
        for line in result.stdout.splitlines():
            if not line.startswith("DRV:"):
                continue
            # DRV:<index>,<flags1>,<flags2>,<flags3>,"<drive_name>","<disc_name>","<device>"
            try:
                fields = self._parse_csv_line(line[4:])
            except Exception:  # pylint: disable=broad-except
                log.debug("Skipping unparseable DRV line: %r", line)
                continue
            if len(fields) < 7:
                continue
            try:
                drive_index = int(fields[0])
                accessible_flag = int(fields[1])
            except ValueError:
                log.debug("Skipping DRV line with non-integer index/flags: %r", line)
                continue
            device = fields[6].strip()
            # Accessibility flag 2 = drive present with a readable disc.
            if accessible_flag == 2 and device:
                drives.append(DriveInfo(device=device, drive_index=drive_index))
        log.info("Found %d accessible drive(s): %s", len(drives), [drv.device for drv in drives])
        return drives

    def scan_titles(self, drive_index: int) -> list[Title]:
        """Scan a specific drive and return all titles with metadata.

        Runs ``makemkvcon -r info disc:<drive_index>`` and collects ``TINFO:``
        lines keyed by title index and attribute ID.  After all attributes are
        gathered, :class:`~diskripr.models.Title` objects are created and
        heuristic type tags are assigned.

        Duration normalisation: MakeMKV may omit the leading zero on the hours
        component (e.g. ``2:11:37`` rather than ``02:11:37``); both forms are
        accepted and stored in the canonical ``HH:MM:SS`` format required by
        :class:`~diskripr.models.Title`.

        Args:
            drive_index: The MakeMKV drive index to scan.

        Returns:
            List of :class:`~diskripr.models.Title` objects sorted by title
            index.  The longest title is tagged ``"main"``; others are
            classified by duration threshold.

        Raises:
            ToolNotFound: ``makemkvcon`` is not on PATH.
            ToolError: ``makemkvcon`` exited with a non-zero return code.
        """
        self.require_available()
        log.info("Scanning titles on drive index %d (this may take a moment)", drive_index)
        result = self.run(
            [self.binary, "-r", "info", f"disc:{drive_index}"],
            timeout=300,
        )

        title_attrs = self._collect_tinfo(result.stdout)

        # Resolve canonical durations; drop titles with unparseable durations.
        intermediate: list[tuple[int, dict[int, str], str, int]] = []
        for title_id in sorted(title_attrs):
            attrs = title_attrs[title_id]
            dur_match = _DURATION_RE.match(attrs.get(_ATTR_DURATION, ""))
            if not dur_match:
                log.debug("Skipping title %d — unparseable duration", title_id)
                continue
            hours, minutes, seconds = dur_match.groups()
            normalised = f"{int(hours):02d}:{minutes}:{seconds}"
            intermediate.append(
                (title_id, attrs, normalised, _duration_to_seconds(normalised))
            )

        if not intermediate:
            log.warning("Drive %d: no titles with parseable durations found", drive_index)
            return []

        log.info(
            "Drive %d: found %d title(s) from %d raw TINFO entries",
            drive_index,
            len(intermediate),
            len(title_attrs),
        )
        max_duration_sec = max(dur for _, _, _, dur in intermediate)
        return [
            self._build_title(tid, attrs, dur, norm, dur == max_duration_sec)
            for tid, attrs, norm, dur in intermediate
        ]

    def rip_title(
        self,
        drive_index: int,
        title_index: int,
        output_dir: Path,
        min_length: int = 0,
        on_progress: Optional[ProgressCallback] = None,
    ) -> RipResult:
        """Rip a single title from a disc to *output_dir*.

        Streams ``makemkvcon mkv disc:<drive_index> <title_index> <output_dir>
        --minlength=<min_length>`` and parses the output line by line:

        - ``PRGV:`` lines drive :class:`~diskripr.util.progress.ProgressEvent`
          callbacks (stage ``"rip"``).
        - ``MSG:`` lines are logged; those with error codes ≥ 5000 set the
          error message on the returned :class:`~diskripr.models.RipResult`.
        - ``TSAV:`` lines record the output filename created by MakeMKV.

        ``self.active_pid`` is set for the duration of the rip stream so
        callers can send a cancellation signal if needed.

        If no ``TSAV:`` line is emitted, the driver falls back to the first
        ``*.mkv`` file found in *output_dir*.

        Args:
            drive_index:  MakeMKV drive index (from :meth:`scan_drives`).
            title_index:  Title index to rip (from :meth:`scan_titles`).
            output_dir:   Destination directory; created if absent.
            min_length:   Minimum title duration in seconds passed to
                          ``--minlength``; ``0`` disables the filter.
            on_progress:  Optional progress callback.

        Returns:
            :class:`~diskripr.models.RipResult` with ``success=True`` on a
            clean rip, or ``success=False`` with an ``error_message`` on a
            recoverable failure.

        Raises:
            ToolNotFound: ``makemkvcon`` is not on PATH.
            RipError: ``makemkvcon`` exited with a non-zero return code.
        """
        self.require_available()
        output_dir.mkdir(parents=True, exist_ok=True)
        log.info(
            "Ripping title %d from drive index %d -> %s",
            title_index,
            drive_index,
            output_dir,
        )

        args = [
            self.binary, "mkv",
            f"disc:{drive_index}",
            str(title_index),
            str(output_dir),
            f"--minlength={min_length}",
            "-r",
        ]

        last_message: Optional[str] = None
        output_filename: Optional[str] = None
        error_message: Optional[str] = None

        try:
            for line in self.stream(args):
                if line.startswith("PRGV:"):
                    self._handle_prgv(line, last_message, on_progress)
                elif line.startswith("MSG:"):
                    last_message, error_message = self._handle_msg(
                        line, last_message, error_message
                    )
                elif line.startswith("TSAV:"):
                    output_filename = self._handle_tsav(line)
        except ToolError as exc:
            raise RipError(exc.command, exc.returncode, exc.stderr) from exc

        if error_message is not None:
            return RipResult(
                title_index=title_index,
                output_path=None,
                success=False,
                error_message=error_message,
            )

        output_path = self._resolve_output_path(output_dir, output_filename)
        return RipResult(
            title_index=title_index,
            output_path=output_path,
            success=output_path is not None,
            error_message=(
                None if output_path is not None else "No output MKV found after rip"
            ),
        )

    # ------------------------------------------------------------------
    # Parsing helpers (static — no driver state needed)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_csv_line(payload: str) -> list[str]:
        """Parse a CSV-formatted payload as emitted in MakeMKV robot-mode lines."""
        reader = csv.reader(io.StringIO(payload))
        return next(reader)

    @staticmethod
    def _collect_tinfo(stdout: str) -> dict[int, dict[int, str]]:
        """Parse all TINFO lines from *stdout* into a nested attribute dict.

        Returns ``title_attrs[title_id][attr_id] = value``.  Unparseable lines
        are skipped with a debug log.
        """
        title_attrs: dict[int, dict[int, str]] = {}
        for line in stdout.splitlines():
            if not line.startswith("TINFO:"):
                continue
            # TINFO:<title_id>,<attr_id>,<type_id>,"<value>"
            try:
                reader = csv.reader(io.StringIO(line[6:]))
                fields = next(reader)
            except Exception:  # pylint: disable=broad-except
                log.debug("Skipping unparseable TINFO line: %r", line)
                continue
            if len(fields) < 4:
                continue
            try:
                title_id = int(fields[0])
                attr_id = int(fields[1])
            except ValueError:
                log.debug("Skipping TINFO line with non-integer ids: %r", line)
                continue
            title_attrs.setdefault(title_id, {})[attr_id] = fields[3]
        return title_attrs

    @staticmethod
    def _build_title(
        title_id: int,
        attrs: dict[int, str],
        duration_sec: int,
        normalised_duration: str,
        is_longest: bool,
    ) -> Title:
        """Construct a single :class:`~diskripr.models.Title` from raw attributes."""
        name = attrs.get(_ATTR_NAME, f"Title_{title_id:02d}")

        chapter_count_str = attrs.get(_ATTR_CHAPTER_COUNT, "0")
        try:
            chapter_count = int(chapter_count_str)
        except ValueError:
            chapter_count = 0

        size_bytes_str = attrs.get(_ATTR_SIZE_BYTES, "0").replace(" ", "")
        try:
            size_bytes = int(size_bytes_str)
        except ValueError:
            size_bytes = 0

        return Title(
            index=title_id,
            name=name,
            duration=normalised_duration,
            size_bytes=size_bytes,
            chapter_count=chapter_count,
            stream_summary=attrs.get(_ATTR_STREAM_SUMMARY, ""),
            title_type=_classify_title_type(duration_sec, is_longest),
        )

    @staticmethod
    def _handle_prgv(
        line: str,
        last_message: Optional[str],
        on_progress: Optional[ProgressCallback],
    ) -> None:
        """Parse a PRGV: line and invoke *on_progress* if provided."""
        if on_progress is None:
            return
        # PRGV:<current>,<total>,<max>
        parts = line[5:].split(",")
        if len(parts) < 3:
            return
        try:
            total = int(parts[1])
            prgv_max = int(parts[2]) or _PRGV_MAX
        except ValueError:
            return
        on_progress(ProgressEvent(
            stage="rip",
            current=total,
            total=prgv_max,
            message=last_message,
        ))

    @staticmethod
    def _handle_msg(
        line: str,
        last_message: Optional[str],
        error_message: Optional[str],
    ) -> tuple[Optional[str], Optional[str]]:
        """Parse a MSG: line; return updated ``(last_message, error_message)``."""
        # MSG:<code>,<flags>,<param_count>,"<message>","<format>"[,params...]
        try:
            reader = csv.reader(io.StringIO(line[4:]))
            fields = next(reader)
        except Exception:  # pylint: disable=broad-except
            return last_message, error_message
        if len(fields) < 4:
            return last_message, error_message
        try:
            code = int(fields[0])
        except ValueError:
            return last_message, error_message
        message_text = fields[3]
        if code >= _MSG_ERROR_CODE_MIN:
            log.warning("makemkvcon error (code %d): %s", code, message_text)
            error_message = message_text
        elif message_text:
            log.info("makemkvcon: %s", message_text)
        return message_text, error_message

    @staticmethod
    def _handle_tsav(line: str) -> Optional[str]:
        """Parse a TSAV: line and return the saved filename, or ``None``."""
        # TSAV:<title_index>,"<filename>"
        try:
            reader = csv.reader(io.StringIO(line[5:]))
            fields = next(reader)
            if len(fields) >= 2:
                return fields[1].strip()
        except Exception:  # pylint: disable=broad-except
            pass
        return None

    @staticmethod
    def _resolve_output_path(
        output_dir: Path,
        output_filename: Optional[str],
    ) -> Optional[Path]:
        """Locate the ripped MKV: prefer the TSAV filename, fall back to glob."""
        if output_filename:
            candidate = output_dir / output_filename
            if candidate.exists():
                return candidate
        mkv_files = sorted(output_dir.glob("*.mkv"))
        return mkv_files[0] if mkv_files else None
