"""Filesystem and path helpers for the diskripr organize stage.

All operations use ``pathlib.Path``. Functions:

- ``sanitize_filename(name)``
    Strip characters that are illegal on common filesystems before embedding
    user-supplied names into path components.

- ``build_jellyfin_tree(base, movie_name, year)``
    Create and return the Jellyfin directory structure under ``base/Movies/``:
    ``<Movie Name> (<Year>)/`` and its ``extras/`` subdirectory.

- ``build_main_feature_filename(movie_name, year, disc_number=None)``
    Return the correct MKV filename for the main feature:
    - Single-disc: ``<Movie Name> (<Year>).mkv``
    - Multi-disc:  ``<Movie Name> (<Year>) - Part<N>.mkv``

- ``build_extra_filename(extra_type, counter)``
    Return the Jellyfin-compatible filename for a classified extra title.
    Format: ``<Type Label> <counter>-<extra_type>.mkv``
    Example: ``Behind the Scenes 1-behindthescenes.mkv``

- ``scan_existing_extras(extras_dir)``
    Inspect an existing ``extras/`` folder and return the current highest
    counter per Jellyfin extra type (e.g. ``{"behindthescenes": 2}``). Used
    by multi-disc runs to continue numbering without filename collisions.

- ``safe_move(src, dest)``
    Move ``src`` to ``dest`` with overwrite protection. Raises if ``dest``
    already exists and overwrite is not explicitly requested. Logs the
    operation.

- ``make_temp_dir(base)``
    Create and return ``base/.tmp/``, creating parent directories as needed.

- ``cleanup(temp_dir)``
    Remove the temporary working directory and all its contents.

- ``eject_disc(device)``
    Eject the disc at *device* via the ``eject`` shell command. Non-fatal.

- ``format_size(num_bytes)``
    Return a human-readable file size string (e.g. ``"4.7 GB"``).
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from diskripr.models import JellyfinExtraType

log = logging.getLogger(__name__)

# Matches illegal characters on Windows/Linux/macOS filesystems.
_ILLEGAL_CHARS_RE = re.compile(r'[:/\\?*"<>|]')

# Matches filenames produced by build_extra_filename, e.g.
# "Behind the Scenes 2-behindthescenes.mkv".  Counter is mandatory (always
# written) so \d+ is used rather than \d*.
_EXTRA_FILENAME_RE = re.compile(
    r"^.+\s+(?P<counter>\d+)"
    r"-(?P<type>behindthescenes|deletedscene|featurette|interview|scene|short|trailer|extra)"
    r"\.mkv$",
    re.IGNORECASE,
)

# Human-readable labels for each Jellyfin extra type, used as the descriptive
# prefix in extra filenames.
_EXTRA_TYPE_LABEL: dict[str, str] = {
    "behindthescenes": "Behind the Scenes",
    "deletedscene": "Deleted Scene",
    "featurette": "Featurette",
    "interview": "Interview",
    "scene": "Scene",
    "short": "Short",
    "trailer": "Trailer",
    "extra": "Extra",
}

_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def sanitize_filename(name: str) -> str:
    """Strip characters that are illegal on common filesystems from *name*.

    Removes ``:``, ``/``, ``\\``, ``?``, ``*``, ``"``, ``<``, ``>``, ``|``,
    then strips leading/trailing whitespace and collapses repeated spaces.
    """
    cleaned = _ILLEGAL_CHARS_RE.sub("", name)
    return " ".join(cleaned.split())


def build_jellyfin_tree(base: Path, movie_name: str, year: int) -> tuple[Path, Path]:
    """Create and return the Jellyfin directory structure under ``base/Movies/``.

    Returns a ``(movie_dir, extras_dir)`` tuple. Both directories are created
    if they do not already exist.
    """
    movie_dir = base / "movies" / f"{sanitize_filename(movie_name)} ({year})"
    extras_dir = movie_dir / "extras"
    extras_dir.mkdir(parents=True, exist_ok=True)
    return movie_dir, extras_dir


def build_main_feature_filename(
    movie_name: str, year: int, disc_number: Optional[int] = None
) -> str:
    """Return the correct MKV filename for the main feature.

    Single-disc: ``<Movie Name> (<Year>).mkv``
    Multi-disc:  ``<Movie Name> (<Year>) - Part<N>.mkv``
    """
    safe_name = sanitize_filename(movie_name)
    if disc_number is not None:
        return f"{safe_name} ({year}) - Part{disc_number}.mkv"
    return f"{safe_name} ({year}).mkv"


def build_extra_filename(extra_type: JellyfinExtraType, counter: int) -> str:
    """Return the Jellyfin-compatible filename for a classified extra title.

    Format: ``<Type Label> <counter>-<extra_type>.mkv``

    Examples::

        build_extra_filename("behindthescenes", 1)  # "Behind the Scenes 1-behindthescenes.mkv"
        build_extra_filename("deletedscene", 3)     # "Deleted Scene 3-deletedscene.mkv"
    """
    label = _EXTRA_TYPE_LABEL[extra_type]
    return f"{label} {counter}-{extra_type}.mkv"


def scan_existing_extras(extras_dir: Path) -> dict[str, int]:
    """Return the highest file counter per Jellyfin extra type in *extras_dir*.

    Inspects ``.mkv`` filenames of the form ``<title>-<type>[N].mkv`` and
    returns a mapping such as ``{"behindthescenes": 2, "featurette": 1}``.
    Types with no existing files are absent from the returned dict (callers
    should treat a missing key as counter zero).
    """
    counters: dict[str, int] = {}
    if not extras_dir.is_dir():
        return counters
    for path in extras_dir.iterdir():
        match = _EXTRA_FILENAME_RE.match(path.name)
        if match:
            extra_type = match.group("type").lower()
            raw_counter = match.group("counter")
            counter = int(raw_counter) if raw_counter else 1
            counters[extra_type] = max(counters.get(extra_type, 0), counter)
    return counters


def safe_move(src: Path, dest: Path) -> None:
    """Move *src* to *dest* with overwrite protection.

    Raises ``FileExistsError`` if *dest* already exists. Logs the operation.
    """
    if dest.exists():
        raise FileExistsError(
            f"Destination already exists and overwrite was not requested: {dest}"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), dest)
    log.debug("Moved %s -> %s", src, dest)


def make_temp_dir(base: Path) -> Path:
    """Create and return ``base/.tmp/``, creating parent directories as needed."""
    temp_dir = base / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    return temp_dir


def cleanup(temp_dir: Path) -> None:
    """Remove the temporary working directory and all its contents."""
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
        log.debug("Removed temp dir %s", temp_dir)


def eject_disc(device: str) -> None:
    """Eject the disc at *device* using the ``eject`` shell command.

    Non-fatal: logs a warning if ``eject`` is unavailable or the command fails
    so that the pipeline can complete without raising.
    """
    try:
        subprocess.run(["eject", device], check=True, capture_output=True)
        log.debug("Ejected disc at %s", device)
    except FileNotFoundError:
        log.warning("'eject' command not found; disc not ejected from %s", device)
    except subprocess.CalledProcessError as exc:
        log.warning("Could not eject %s: %s", device, exc)


def format_size(num_bytes: int) -> str:
    """Return a human-readable file size string (e.g. ``"4.7 GB"``)."""
    value = float(num_bytes)
    for unit in _SIZE_UNITS[:-1]:
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} {_SIZE_UNITS[-1]}"
