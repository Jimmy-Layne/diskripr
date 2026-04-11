"""Driver for pyparsedvd — IFO file parsing for per-cell duration signals.

Exposes one method on :class:`IfoDriver`:

- ``read_disc(video_ts_path)``
    Scan all ``VTS_XX_0.IFO`` files in a mounted ``VIDEO_TS/`` directory,
    parse each via ``pyparsedvd.load_vts_pgci``, and return a dict keyed by
    VTS index (1-based).  Returns ``None`` if the directory is inaccessible
    or contains no VTS IFO files.

    Failure is always non-fatal: any unreadable or malformed IFO is skipped
    with a debug log entry.  If all IFOs fail, ``None`` is returned.

Defines two result dataclasses local to this module:

- ``IfoVts``   — PGC summary for a single VTS: index, PGC count, and list
                 of :class:`IfoPgc`.
- ``IfoPgc``   — Summary of one program chain: total duration in seconds,
                 chapter count (``nb_program``), and per-cell durations.

Unlike other drivers, :class:`IfoDriver` reads files directly and invokes no
external subprocess.  ``binary`` is therefore ``None`` and the standard
``is_available`` / ``require_available`` path is not used.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from pyparsedvd.vts_ifo import PlaybackTime, load_vts_pgci

from diskripr.drivers.base import BaseDriver

log = logging.getLogger(__name__)

# Matches VTS_XX_0.IFO (case-insensitive for portability).
_VTS_IFO_RE = re.compile(r"^VTS_(\d+)_0\.IFO$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class IfoPgc:
    """Summary of one program chain from a VTS IFO file."""

    duration_seconds: int
    nb_program: int                   # chapter count within this PGC
    cell_durations: list[int] = field(default_factory=list)


@dataclass
class IfoVts:
    """Parsed PGC summary for a single VTS."""

    vts_index: int
    pgc_count: int
    pgcs: list[IfoPgc] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class IfoDriver(BaseDriver):
    """Driver for reading DVD IFO files via pyparsedvd.

    Unlike other drivers this class performs no subprocess invocations; it
    reads IFO binary files directly through pyparsedvd.  The ``binary``
    attribute is not used.

    Usage::

        driver = IfoDriver()
        vts_map = driver.read_disc(Path("/run/media/user/DISC/VIDEO_TS"))
        if vts_map is not None:
            vts1 = vts_map[1]
    """

    binary = None  # type: ignore[assignment]  # no external binary; reads files directly

    def is_available(self) -> bool:
        """Return ``True`` — IfoDriver reads files directly with no binary dependency."""
        return True

    def read_disc(self, video_ts_path: Path) -> Optional[dict[int, IfoVts]]:
        """Parse all ``VTS_XX_0.IFO`` files under *video_ts_path*.

        Scans for IFO files matching the ``VTS_XX_0.IFO`` naming convention,
        parses each via ``pyparsedvd.load_vts_pgci``, and returns a dict keyed
        by VTS index (1-based).

        Args:
            video_ts_path: Path to an accessible ``VIDEO_TS/`` directory.

        Returns:
            ``dict[int, IfoVts]`` keyed by VTS index on success, or ``None``
            if the directory does not exist, is not a directory, or contains
            no parseable VTS IFO files.
        """
        if not video_ts_path.is_dir():
            log.debug("IfoDriver: %s is not an accessible directory", video_ts_path)
            return None

        ifo_files = sorted(video_ts_path.glob("VTS_*_0.IFO"))
        if not ifo_files:
            log.debug("IfoDriver: no VTS IFO files found in %s", video_ts_path)
            return None

        result: dict[int, IfoVts] = {}
        for ifo_path in ifo_files:
            vts_index = _parse_vts_index(ifo_path.name)
            if vts_index is None:
                continue
            ifo_vts = _read_vts(ifo_path, vts_index)
            if ifo_vts is not None:
                result[vts_index] = ifo_vts

        return result if result else None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _playtime_to_seconds(time: PlaybackTime) -> int:
    """Convert a :class:`~pyparsedvd.vts_ifo.PlaybackTime` to integer seconds.

    Frames are discarded; only hours, minutes, and seconds contribute.
    """
    return time.hours * 3600 + time.minutes * 60 + time.seconds


def _parse_vts_index(filename: str) -> Optional[int]:
    """Return the VTS index from a ``VTS_XX_0.IFO`` filename, or ``None``."""
    match = _VTS_IFO_RE.match(filename)
    if match:
        return int(match.group(1))
    return None


def _read_vts(ifo_path: Path, vts_index: int) -> Optional[IfoVts]:
    """Parse a single VTS IFO file and return an :class:`IfoVts`, or ``None``."""
    try:
        with open(ifo_path, "rb") as ifo_file:
            vtspgci = load_vts_pgci(ifo_file)
    except Exception as exc:  # pylint: disable=broad-except
        log.debug("IfoDriver: failed to parse %s: %s", ifo_path, exc)
        return None

    pgcs = [
        IfoPgc(
            duration_seconds=_playtime_to_seconds(pgc.duration),
            nb_program=pgc.nb_program,
            cell_durations=[_playtime_to_seconds(cell) for cell in pgc.playback_times],
        )
        for pgc in vtspgci.program_chains
    ]

    return IfoVts(
        vts_index=vts_index,
        pgc_count=vtspgci.nb_program_chains,
        pgcs=pgcs,
    )
