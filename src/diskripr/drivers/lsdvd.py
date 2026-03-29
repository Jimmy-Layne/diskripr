"""Driver for ``lsdvd`` — quick disc pre-check before MakeMKV scanning.

Exposes one method on :class:`LsdvdDriver`:

- ``read_disc(device)``
    Run ``lsdvd -x <device>`` and parse the disc title and a basic title list
    (index, duration). Returns ``None`` on any failure.

    Encrypted discs commonly cause ``lsdvd`` to fail or return incomplete data.
    This failure is non-fatal — MakeMKV handles CSS decryption independently.
    The result is used only as a fast pre-check to surface the disc title
    before the slower MakeMKV scan begins.

Defines two result dataclasses local to this module (not shared across the
pipeline, so not in ``models.py``):

- ``LsdvdTitle`` — title index and duration string (``HH:MM:SS``).
- ``LsdvdDisc``  — disc title plus the list of ``LsdvdTitle`` entries.

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
# "Title: 01, Length: 02:11:37.00 Chapters: 23, ..."
# Hours component may be single-digit on some lsdvd versions.
_TITLE_RE = re.compile(r"^Title:\s+(\d+),\s+Length:\s+(\d+):(\d{2}):(\d{2})\.\d+")


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class LsdvdTitle:
    """Basic per-title info from a quick lsdvd scan."""

    index: int
    duration: str   # Normalised HH:MM:SS


@dataclass
class LsdvdDisc:
    """Disc pre-check result returned by :meth:`LsdvdDriver.read_disc`."""

    disc_title: str
    titles: list[LsdvdTitle] = field(default_factory=list)


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
        """Run ``lsdvd -x <device>`` and return disc title and basic title list.

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
    def _parse(stdout: str) -> Optional[LsdvdDisc]:
        """Parse ``lsdvd -x`` stdout into a :class:`LsdvdDisc`.

        Returns ``None`` if no ``Disc Title:`` line is found.  Individual
        unparseable title lines are skipped rather than causing a failure.
        """
        disc_title: Optional[str] = None
        titles: list[LsdvdTitle] = []

        for line in stdout.splitlines():
            disc_match = _DISC_TITLE_RE.match(line)
            if disc_match:
                disc_title = disc_match.group(1).strip()
                continue

            title_match = _TITLE_RE.match(line)
            if title_match:
                try:
                    index = int(title_match.group(1))
                    hours = int(title_match.group(2))
                    minutes = title_match.group(3)
                    seconds = title_match.group(4)
                    duration = f"{hours:02d}:{minutes}:{seconds}"
                    titles.append(LsdvdTitle(index=index, duration=duration))
                except ValueError:
                    log.debug("Skipping unparseable lsdvd title line: %r", line)

        if disc_title is None:
            return None

        return LsdvdDisc(disc_title=disc_title, titles=titles)
