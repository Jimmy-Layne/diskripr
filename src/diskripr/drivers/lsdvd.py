"""Driver for ``lsdvd`` — quick disc pre-check before MakeMKV scanning.

Exposes one method on :class:`LsdvdDriver`:

- ``read_disc(device)``
    Run ``lsdvd -x <device>`` and parse the disc title and a basic title list
    (index, duration, VTS number, TTN, audio stream count, cell count). Returns
    ``None`` on any failure.

    Encrypted discs commonly cause ``lsdvd`` to fail or return incomplete data.
    This failure is non-fatal — MakeMKV handles CSS decryption independently.
    The result is used only as a fast pre-check to surface the disc title
    before the slower MakeMKV scan begins.

Defines two result dataclasses local to this module (not shared across the
pipeline, so not in ``models.py``):

- ``LsdvdTitle`` — title index, duration, VTS number, TTN, audio stream count,
                   and cell count.
- ``LsdvdDisc``  — disc title, list of ``LsdvdTitle`` entries, and the VTS
                   number of the longest track (``main_vts``).

Raises ``ToolNotFound`` if ``lsdvd`` is not available; callers are expected to
catch this and skip the pre-check silently.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from diskripr.drivers.base import BaseDriver, ToolError

log = logging.getLogger(__name__)

# lsdvd -x output patterns.
# "Disc Title: SOME_DISC_TITLE"
_DISC_TITLE_RE = re.compile(r"^Disc Title:\s+(.+)$")
# "Title: 01, Length: 02:11:37.00 Chapters: 23, Cells: 13, Audio streams: 03, ..."
# Hours component may be single-digit on some lsdvd versions.
_TITLE_RE = re.compile(
    r"^Title:\s+(\d+),\s+Length:\s+(\d+):(\d{2}):(\d{2})\.\d+"
    r"\s+Chapters:\s+\d+,\s+Cells:\s+(\d+),\s+Audio streams:\s+(\d+)"
)
# "\tVTS: 01, TTN: 01, FPS: ..."  (indented line following each title header)
_VTS_TTN_RE = re.compile(r"^\s+VTS:\s+(\d+),\s+TTN:\s+(\d+),")
# "Longest track: 01"
_LONGEST_TRACK_RE = re.compile(r"^Longest track:\s+(\d+)$")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class LsdvdTitle:
    """Per-title info from a full lsdvd scan."""

    index: int
    duration: str           # Normalised HH:MM:SS
    vts_number: int
    ttn: int
    audio_stream_count: int
    cell_count: int


@dataclass
class LsdvdDisc:
    """Disc pre-check result returned by :meth:`LsdvdDriver.read_disc`."""

    disc_title: str
    titles: list[LsdvdTitle] = field(default_factory=list)
    main_vts: Optional[int] = None


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class LsdvdDriver(BaseDriver):
    """Driver for the ``lsdvd`` disc pre-check.

    ``read_disc`` is designed to be non-fatal: it returns ``None`` rather than
    raising whenever lsdvd is absent, exits non-zero (common with CSS-encrypted
    discs), or produces output that cannot be parsed.

    Usage::

        driver = LsdvdDriver()
        disc = driver.read_disc("/dev/sr0")
        if disc is not None:
            print(disc.disc_title)
    """

    binary = "lsdvd"

    def read_disc(self, device: str) -> Optional[LsdvdDisc]:
        """Run ``lsdvd -x <device>`` and return disc title and title list.

        Args:
            device: Block device path of the optical drive (e.g. ``/dev/sr0``).

        Returns:
            :class:`LsdvdDisc` on success, or ``None`` if lsdvd is unavailable,
            exits with an error, or returns output that contains no disc title.
        """
        if not self.is_available():
            log.debug("lsdvd not on PATH — skipping disc pre-check")
            return None

        try:
            result = self.run([self.binary, "-x", device], timeout=15)
        except ToolError as exc:
            log.debug(
                "lsdvd exited with code %d for %s — skipping disc pre-check",
                exc.returncode,
                device,
            )
            return None

        parsed = self._parse(result.stdout)
        if parsed is None:
            log.debug(
                "lsdvd output for %s contained no disc title — skipping pre-check",
                device,
            )
        return parsed

    # ------------------------------------------------------------------
    # Parsing helpers (static — no driver state needed)
    # ------------------------------------------------------------------

    @staticmethod
    def _parse(stdout: str) -> Optional[LsdvdDisc]:  # pylint: disable=too-many-locals,too-many-branches
        """Parse ``lsdvd -x`` stdout into a :class:`LsdvdDisc`.

        Uses a block-aware single-pass approach: when a title header line is
        matched, it is held as *pending* until the next indented VTS/TTN line
        completes it.  Titles with no following VTS/TTN line (e.g. truncated
        output) are committed with ``vts_number=0`` and ``ttn=0``.

        Returns ``None`` if no ``Disc Title:`` line is found.  Individual
        unparseable title lines are skipped rather than causing a failure.
        """
        disc_title: Optional[str] = None
        titles: list[LsdvdTitle] = []
        longest_track_index: Optional[int] = None
        pending: Optional[dict] = None

        for line in stdout.splitlines():
            disc_match = _DISC_TITLE_RE.match(line)
            if disc_match:
                disc_title = disc_match.group(1).strip()
                continue

            title_match = _TITLE_RE.match(line)
            if title_match:
                if pending is not None:
                    titles.append(_commit_pending(pending))
                try:
                    index = int(title_match.group(1))
                    hours = int(title_match.group(2))
                    minutes = title_match.group(3)
                    seconds = title_match.group(4)
                    cell_count = int(title_match.group(5))
                    audio_stream_count = int(title_match.group(6))
                    duration = f"{hours:02d}:{minutes}:{seconds}"
                    pending = {
                        "index": index,
                        "duration": duration,
                        "cell_count": cell_count,
                        "audio_stream_count": audio_stream_count,
                    }
                except ValueError:
                    log.debug("Skipping unparseable lsdvd title line: %r", line)
                    pending = None
                continue

            vts_match = _VTS_TTN_RE.match(line)
            if vts_match and pending is not None:
                try:
                    pending["vts_number"] = int(vts_match.group(1))
                    pending["ttn"] = int(vts_match.group(2))
                except ValueError:
                    pass
                titles.append(_commit_pending(pending))
                pending = None
                continue

            longest_match = _LONGEST_TRACK_RE.match(line)
            if longest_match:
                try:
                    longest_track_index = int(longest_match.group(1))
                except ValueError:
                    pass

        if pending is not None:
            titles.append(_commit_pending(pending))

        if disc_title is None:
            return None

        main_vts: Optional[int] = None
        if longest_track_index is not None:
            for title_obj in titles:
                if title_obj.index == longest_track_index:
                    main_vts = title_obj.vts_number
                    break

        return LsdvdDisc(disc_title=disc_title, titles=titles, main_vts=main_vts)


def _commit_pending(pending: dict) -> LsdvdTitle:
    """Build an :class:`LsdvdTitle` from a pending dict, defaulting VTS/TTN to 0."""
    return LsdvdTitle(
        index=pending["index"],
        duration=pending["duration"],
        vts_number=pending.get("vts_number", 0),
        ttn=pending.get("ttn", 0),
        audio_stream_count=pending["audio_stream_count"],
        cell_count=pending["cell_count"],
    )
