"""Filesystem and path helpers for the diskripr organize stage.

This module is the single authoritative location for Jellyfin naming
conventions. Any decision about directory layout, filename format, or extra
type routing lives here and is documented here.

All operations use ``pathlib.Path``. Functions:

- ``sanitize_filename(name)``
    Strip characters that are illegal on common filesystems before embedding
    user-supplied names into path components.

- ``build_jellyfin_tree(base, movie_name, year)``
    Create and return the Jellyfin directory structure under ``base/movies/``:
    ``<Movie Name> (<Year>)/`` and all eight extra-type subdirectories.
    Returns ``(movie_dir, extras_type_dirs)`` where *extras_type_dirs* is a
    ``dict[JellyfinExtraType, Path]``.

- ``build_main_feature_filename(movie_name, year, disc_number=None)``
    Return the correct MKV filename for the main feature:
    - Single-disc: ``<Movie Name> (<Year>).mkv``
    - Multi-disc:  ``<Movie Name> (<Year>) - Part<N>.mkv``

- ``build_extra_filename(extra_type, counter, title_name=None)``
    Return the Jellyfin-compatible filename for a classified extra title.
    When *title_name* is provided and non-generic the sanitized name is used
    directly as the stem.  When absent or generic the fallback format is
    ``<Type Label> <counter>.mkv`` (the subdirectory encodes the type).

- ``scan_existing_extras(movie_dir)``
    Inspect all eight type subdirectories under *movie_dir* and return the
    highest counter per Jellyfin extra type.  Types with no files are absent
    from the returned dict (treat a missing key as counter zero).

- ``build_tv_tree(base, show_name, season_number)``
    Create and return the Jellyfin TV directory structure under
    ``base/Shows/<Show Name>/Season NN/`` and all eight extra-type subdirs.
    Returns ``(season_dir, extras_type_dirs)``.

- ``build_episode_filename(show_name, season_number, episode_number, episode_title=None)``
    Return the Jellyfin-compatible filename for a TV episode.
    Format: ``<Show Name> S<SS>E<EE>.mkv`` or
    ``<Show Name> S<SS>E<EE> - <Episode Title>.mkv``.

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

**Jellyfin type-subdirectory convention**

Jellyfin resolves extra type from the subdirectory name.  This module uses
the type-subdirectory convention exclusively because it yields cleaner display
names — the filename stem is shown directly to the user with no suffix noise.

+------------------------+-----------------------+---------------------------------------+
| Jellyfin extra type    | Subdirectory name     | Example file                          |
+========================+=======================+=======================================+
| Behind the Scenes      | ``behind the scenes`` | ``The Making of Rosencrantz.mkv``     |
| Deleted Scene          | ``deleted scenes``    | ``The Library.mkv``                   |
| Featurette             | ``featurettes``       | ``Original Theatrical Trailer.mkv``   |
| Interview              | ``interviews``        | ``Cast Interview.mkv``                |
| Scene                  | ``scenes``            | ``Opening Sequence.mkv``              |
| Short                  | ``shorts``            | ``Short Film.mkv``                    |
| Trailer                | ``trailers``          | ``Theatrical Trailer.mkv``            |
| Generic extra          | ``extras``            | ``Extra 1.mkv``                       |
+------------------------+-----------------------+---------------------------------------+

Directory names are Jellyfin-specified strings and must not be changed.
Filename stems are free-form — they become the display name in the Jellyfin UI.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from diskripr.models import JellyfinExtraType
from diskripr.util.heuristics import is_generic_title_name

log = logging.getLogger(__name__)

# Matches illegal characters on Windows/Linux/macOS filesystems.
_ILLEGAL_CHARS_RE = re.compile(r'[:/\\?*"<>|]')

# Matches counter-based extra filenames: "<Type Label> <N>.mkv"
# Used by scan_existing_extras to find the highest counter per type.
_EXTRA_COUNTER_RE = re.compile(r"^.+\s+(\d+)\.mkv$", re.IGNORECASE)

# Human-readable labels for each Jellyfin extra type, used as the descriptive
# prefix in counter-fallback extra filenames.
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

# Jellyfin subdirectory name for each extra type.  These strings are mandated
# by Jellyfin and must not be changed.
_EXTRA_TYPE_SUBDIR: dict[str, str] = {
    "behindthescenes": "behind the scenes",
    "deletedscene": "deleted scenes",
    "featurette": "featurettes",
    "interview": "interviews",
    "scene": "scenes",
    "short": "shorts",
    "trailer": "trailers",
    "extra": "extras",
}

_ALL_EXTRA_TYPES: tuple[JellyfinExtraType, ...] = (
    "behindthescenes",
    "deletedscene",
    "featurette",
    "interview",
    "scene",
    "short",
    "trailer",
    "extra",
)

_SIZE_UNITS = ["B", "KB", "MB", "GB", "TB"]


def sanitize_filename(name: str) -> str:
    """Strip characters that are illegal on common filesystems from *name*.

    Removes ``:``, ``/``, ``\\``, ``?``, ``*``, ``"``, ``<``, ``>``, ``|``,
    then strips leading/trailing whitespace and collapses repeated spaces.
    """
    cleaned = _ILLEGAL_CHARS_RE.sub("", name)
    return " ".join(cleaned.split())


def _create_extra_type_dirs(parent: Path) -> dict[JellyfinExtraType, Path]:
    """Create all eight Jellyfin extra-type subdirectories under *parent*.

    Returns a dict mapping each :data:`~diskripr.models.JellyfinExtraType`
    to its absolute ``Path``.  Directories are created with ``exist_ok=True``.
    """
    dirs: dict[JellyfinExtraType, Path] = {}
    for extra_type in _ALL_EXTRA_TYPES:
        subdir = parent / _EXTRA_TYPE_SUBDIR[extra_type]
        subdir.mkdir(parents=True, exist_ok=True)
        dirs[extra_type] = subdir
    return dirs


def build_jellyfin_tree(
    base: Path,
    movie_name: str,
    year: int,
) -> tuple[Path, dict[JellyfinExtraType, Path]]:
    """Create and return the Jellyfin directory structure under ``base/movies/``.

    All eight Jellyfin extra-type subdirectories are created under the movie
    folder.

    :param base: Root of the output library (e.g. ``/srv/media``).
    :param movie_name: Human-readable movie title; illegal filesystem characters
        are stripped before use.
    :param year: Release year, appended in parentheses to the movie folder name.
    :returns: ``(movie_dir, extras_type_dirs)`` — *movie_dir* is the movie
        folder ``Path``; *extras_type_dirs* maps each
        :data:`~diskripr.models.JellyfinExtraType` to its subdirectory
        ``Path``.
    """
    movie_dir = base / "movies" / f"{sanitize_filename(movie_name)} ({year})"
    movie_dir.mkdir(parents=True, exist_ok=True)
    extras_type_dirs = _create_extra_type_dirs(movie_dir)
    return movie_dir, extras_type_dirs


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


def build_extra_filename(
    extra_type: JellyfinExtraType,
    counter: int,
    title_name: Optional[str] = None,
) -> str:
    """Return the Jellyfin-compatible filename for a classified extra title.

    When *title_name* is provided and is not a generic MakeMKV fallback name
    (``Title_NN`` / ``tNN``), the sanitized name is used directly as the stem.
    This produces cleaner Jellyfin display names such as
    ``The Making of Rosencrantz.mkv``.

    When *title_name* is absent or generic, falls back to the counter format:
    ``<Type Label> <counter>.mkv`` (e.g. ``Behind the Scenes 1.mkv``).
    The subdirectory already encodes the type so no ``-<type>`` suffix is
    needed.

    Examples::

        build_extra_filename("behindthescenes", 1)
        # "Behind the Scenes 1.mkv"

        build_extra_filename("deletedscene", 3, "The Library")
        # "The Library.mkv"

        build_extra_filename("featurette", 2, "Title_01")
        # "Featurette 2.mkv"  (generic name → counter fallback)
    """
    if title_name and not is_generic_title_name(title_name):
        return f"{sanitize_filename(title_name)}.mkv"
    label = _EXTRA_TYPE_LABEL[extra_type]
    return f"{label} {counter}.mkv"


def scan_existing_extras(movie_dir: Path) -> dict[JellyfinExtraType, int]:
    """Return the highest file counter per Jellyfin extra type.

    Scans all eight type subdirectories under *movie_dir* (e.g.
    ``behind the scenes/``, ``deleted scenes/``).  For each subdirectory,
    filenames matching the counter-based pattern ``<label> <N>.mkv`` are
    inspected and the highest ``N`` is recorded.

    Types with no existing counter-based files are absent from the returned
    dict.  Callers should treat a missing key as counter zero.

    :param movie_dir: The movie folder (not a type subdir).
    :returns: Mapping of extra type → highest existing counter.
    """
    counters: dict[JellyfinExtraType, int] = {}
    for extra_type in _ALL_EXTRA_TYPES:
        subdir = movie_dir / _EXTRA_TYPE_SUBDIR[extra_type]
        if not subdir.is_dir():
            continue
        for path in subdir.iterdir():
            match = _EXTRA_COUNTER_RE.match(path.name)
            if match:
                counter_value = int(match.group(1))
                existing = counters.get(extra_type, 0)
                counters[extra_type] = max(existing, counter_value)
    return counters


def build_tv_tree(
    base: Path,
    show_name: str,
    season_number: int,
) -> tuple[Path, dict[JellyfinExtraType, Path]]:
    """Create and return the Jellyfin TV directory structure.

    Creates ``<base>/Shows/<Show Name>/Season NN/`` and all eight Jellyfin
    extra-type subdirectories within the season folder.  Season zero is
    rendered as ``Season 00``.

    :param base: Root of the output library.
    :param show_name: Human-readable show title; illegal characters are stripped.
    :param season_number: Season number (0 = specials / ``Season 00``).
    :returns: ``(season_dir, extras_type_dirs)`` — *season_dir* is the season
        folder ``Path``; *extras_type_dirs* maps each
        :data:`~diskripr.models.JellyfinExtraType` to its subdirectory.
    """
    season_dir = (
        base
        / "Shows"
        / sanitize_filename(show_name)
        / f"Season {season_number:02d}"
    )
    season_dir.mkdir(parents=True, exist_ok=True)
    extras_type_dirs = _create_extra_type_dirs(season_dir)
    return season_dir, extras_type_dirs


def build_episode_filename(
    show_name: str,
    season_number: int,
    episode_number: int,
    episode_title: Optional[str] = None,
) -> str:
    """Return the Jellyfin-compatible filename for a TV episode.

    Format when no title is provided::

        <Show Name> S<SS>E<EE>.mkv

    Format with episode title::

        <Show Name> S<SS>E<EE> - <Episode Title>.mkv

    Season and episode numbers are zero-padded to two digits.  Illegal
    characters in *show_name* and *episode_title* are sanitized before use.

    Examples::

        build_episode_filename("The Wire", 1, 3)
        # "The Wire S01E03.mkv"

        build_episode_filename("The Wire", 1, 3, "The Buys")
        # "The Wire S01E03 - The Buys.mkv"
    """
    safe_show = sanitize_filename(show_name)
    code = f"S{season_number:02d}E{episode_number:02d}"
    if episode_title:
        safe_title = sanitize_filename(episode_title)
        return f"{safe_show} {code} - {safe_title}.mkv"
    return f"{safe_show} {code}.mkv"


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
