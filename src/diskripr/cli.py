# pylint: disable=too-many-lines
"""Click-based command-line interface for diskripr.

Provides two command groups, each with three subcommands:

- ``diskripr movie rip``      — full movie pipeline (discover → select → rip → encode → organize)
- ``diskripr movie scan``     — disc scan only; lists titles without ripping
- ``diskripr movie organize`` — organize stage only; re-sorts already-ripped files

- ``diskripr show rip``       — full show pipeline (discover → cluster → rip → encode → organize)
- ``diskripr show scan``      — disc scan only; prints episode cluster guess
- ``diskripr show organize``  — organize stage only for an already-ripped season disc

This is the only module that writes to stdout or prompts the user. It builds a
``MovieConfig`` or ``ShowConfig`` from Click parameters, wires up a progress
callback backed by Click's echo output, and delegates all work to ``pipeline``.

Typical multi-disc movie workflow::

    $ diskripr movie rip -n "Lawrence of Arabia" -y 1962 --disc 1
    # swap disc
    $ diskripr movie rip -n "Lawrence of Arabia" -y 1962 --disc 2

Typical TV season workflow::

    $ diskripr show rip --show "Breaking Bad" --season 1 --start-episode 1
    # swap disc
    $ diskripr show rip --show "Breaking Bad" --season 1 --start-episode 5
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import click

from diskripr.config import ConfigError, MovieConfig, ShowConfig
from diskripr.drivers.base import check_available
from diskripr.models import (
    ClassifiedExtra,
    DiscInfo,
    EpisodeEntry,
    JellyfinExtraType,
    RipResult,
    Selection,
    ShowSelection,
    Title,
)
from diskripr.pipeline import MoviePipeline, ShowPipeline, _classify, _select  # noqa: WPS450
from diskripr.util.heuristics import TitleSignals, classify_extra, cluster_episodes
from diskripr.util.jellyfin_filesystem import (
    build_extra_filename,
    format_size,
    sanitize_filename,
    scan_existing_extras,
)
from diskripr.util.progress import ProgressEvent

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------

_VALID_LOG_LEVELS = {"debug", "info", "warning", "error", "critical"}


def _configure_logging() -> None:
    """Configure the diskripr package logger from DISKRIPR_LOG_LEVEL.

    Reads the ``DISKRIPR_LOG_LEVEL`` environment variable (case-insensitive).
    Defaults to ``"info"`` when the variable is unset or contains an
    unrecognised level name.  Log records are written to stderr.
    """
    level_name = os.environ.get("DISKRIPR_LOG_LEVEL", "info").lower()
    if level_name not in _VALID_LOG_LEVELS:
        level_name = "info"
    level = getattr(logging, level_name.upper())
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    pkg_logger = logging.getLogger("diskripr")
    pkg_logger.setLevel(level)
    pkg_logger.addHandler(handler)
    pkg_logger.propagate = False


# ---------------------------------------------------------------------------
# Dependency metadata
# ---------------------------------------------------------------------------

_REQUIRED_TOOLS: dict[str, str] = {
    "makemkvcon": (
        "sudo add-apt-repository ppa:heyarje/makemkv-beta && "
        "sudo apt install makemkv-bin"
    ),
}

_OPTIONAL_TOOLS: dict[str, str] = {
    "lsdvd": "apt install lsdvd",
    "HandBrakeCLI": "apt install handbrake-cli",
    "ffprobe": "apt install ffmpeg",
}

_EXTRA_TYPES: list[JellyfinExtraType] = [
    "behindthescenes",
    "deletedscene",
    "featurette",
    "interview",
    "scene",
    "short",
    "trailer",
    "extra",
]

_EXTRA_TYPE_LABELS: dict[JellyfinExtraType, str] = {
    "behindthescenes": "Behind the Scenes",
    "deletedscene": "Deleted Scene",
    "featurette": "Featurette",
    "interview": "Interview",
    "scene": "Scene",
    "short": "Short",
    "trailer": "Trailer",
    "extra": "Extra",
}

# ---------------------------------------------------------------------------
# Dependency checks
# ---------------------------------------------------------------------------


def _check_required_deps() -> None:
    """Abort with an install hint if a required binary is missing."""
    for binary, install_hint in _REQUIRED_TOOLS.items():
        if not check_available(binary):
            raise click.ClickException(
                f"{binary!r} not found on PATH.\n"
                f"Install with: {install_hint}"
            )


def _warn_optional_deps(*, check_handbrake: bool = False) -> None:
    """Warn about missing optional tools that affect this invocation."""
    for binary in ("lsdvd", "ffprobe"):
        if not check_available(binary):
            click.echo(
                f"Warning: {binary!r} not found — {_OPTIONAL_TOOLS[binary]}",
                err=True,
            )
    if check_handbrake and not check_available("HandBrakeCLI"):
        click.echo(
            "Warning: 'HandBrakeCLI' not found — "
            f"{_OPTIONAL_TOOLS['HandBrakeCLI']}",
            err=True,
        )


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


class _ProgressReporter:  # pylint: disable=too-few-public-methods
    """ProgressCallback backed by click.echo with in-place line updates."""

    def __init__(self) -> None:
        self._last_stage: str = ""

    def __call__(self, event: ProgressEvent) -> None:
        if event.stage != self._last_stage:
            if self._last_stage:
                click.echo()
            self._last_stage = event.stage

        if event.total > 0:
            pct = int(event.current * 100 / event.total)
            prefix = f"  [{event.stage}] {pct:3d}%"
        else:
            prefix = f"  [{event.stage}]"

        msg = prefix
        if event.message:
            msg += f"  {event.message[:60]}"

        click.echo(f"\r{msg:<72}", nl=False)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _display_disc(disc_info: DiscInfo) -> None:
    """Print a formatted title table to stdout."""
    disc_label = disc_info.disc_title or "(unknown)"
    click.echo(f"Disc:   {disc_label}")
    click.echo(
        f"Device: {disc_info.drive.device}  "
        f"(drive index {disc_info.drive.drive_index})"
    )
    click.echo()
    click.echo(f"  {'#':>3}  {'Name':<38}  {'Duration':>8}  {'Size':>8}  Type")
    click.echo(f"  {'-'*3}  {'-'*38}  {'-'*8}  {'-'*8}  {'-'*12}")
    for title in disc_info.titles:
        size_str = format_size(title.size_bytes)
        name_col = title.name[:38]
        click.echo(
            f"  {title.index:>3}  {name_col:<38}  {title.duration:>8}  "
            f"{size_str:>8}  {title.title_type}"
        )
    click.echo()


def _display_show_cluster(
    episodes: list[Title],
    extra_titles: list[Title],
    signals_map: dict[int, TitleSignals],
) -> None:
    """Print the episode cluster guess (without season/episode numbers) to stdout."""
    total_ep = len(episodes)
    click.echo("Episode cluster guess:")
    for idx, title in enumerate(episodes):
        sig = signals_map.get(title.index, TitleSignals(title=title))
        vts_str = f"VTS {sig.vts_number}" if sig.vts_number is not None else "VTS ?"
        ep_label = f"episode [{idx + 1} of {total_ep}]"
        click.echo(
            f"  Title {title.index} | {title.duration} | "
            f"{title.chapter_count} chapter(s) | {vts_str} | "
            f"{title.name!r:<30}  →  {ep_label}"
        )
    if extra_titles:
        click.echo()
        click.echo("Extras:")
        for title in extra_titles:
            sig = signals_map.get(title.index, TitleSignals(title=title))
            vts_str = f"VTS {sig.vts_number}" if sig.vts_number is not None else "VTS ?"
            result = classify_extra(sig)
            click.echo(
                f"  Title {title.index} | {title.duration} | "
                f"{title.chapter_count} chapter(s) | {vts_str} | "
                f"{title.name!r:<30}  →  extra: {result.extra_type} "
                f"[{result.confidence}]"
            )
    click.echo()


def _display_show_selection(
    selection: ShowSelection,
    signals_map: dict[int, TitleSignals],
) -> None:
    """Print a proposed ShowSelection (ask mode) to stdout for confirmation."""
    click.echo("Proposed episode selection:")
    for entry in selection.episodes:
        title = entry.title
        sig = signals_map.get(title.index, TitleSignals(title=title))
        vts_str = f"VTS {sig.vts_number}" if sig.vts_number is not None else "VTS ?"
        ep_label = f"S{entry.season_number:02d}E{entry.episode_number:02d}"
        click.echo(
            f"  Title {title.index} | {title.duration} | "
            f"{title.chapter_count} chapter(s) | {vts_str} | "
            f"{title.name!r:<30}  →  {ep_label}"
        )
    if selection.extras:
        click.echo()
        click.echo("Extras:")
        for classified in selection.extras:
            title = classified.title
            sig = signals_map.get(title.index, TitleSignals(title=title))
            vts_str = f"VTS {sig.vts_number}" if sig.vts_number is not None else "VTS ?"
            result = classify_extra(sig)
            click.echo(
                f"  Title {title.index} | {title.duration} | "
                f"{title.chapter_count} chapter(s) | {vts_str} | "
                f"{title.name!r:<30}  →  extra: {classified.extra_type} "
                f"[{result.confidence}]"
            )
    click.echo()


# ---------------------------------------------------------------------------
# Interactive prompts
# ---------------------------------------------------------------------------


def _prompt_title_selection(
    disc_info: DiscInfo,
) -> tuple[Title, list[Title]]:
    """Present the title list and return ``(main_title, extra_titles)``."""
    _display_disc(disc_info)
    titles = disc_info.titles
    longest = max(titles, key=lambda tit: tit.duration_seconds)

    click.echo("Select titles to rip:")
    click.echo("  Enter comma-separated indices, 'all', or Enter for main only.")
    click.echo(f"  Default: {longest.index} — {longest.name[:40]}")
    click.echo()

    raw = click.prompt("Selection", default="", show_default=False).strip().lower()

    if raw in ("", "main"):
        return longest, []

    if raw == "all":
        return longest, [tit for tit in titles if tit.index != longest.index]

    chosen_indices: set[int] = set()
    for part in raw.split(","):
        token = part.strip()
        if not token.isdigit():
            raise click.BadParameter(f"Invalid index: {token!r}")
        chosen_indices.add(int(token))

    chosen = [tit for tit in titles if tit.index in chosen_indices]
    if not chosen:
        raise click.UsageError("No valid titles selected.")

    main_title = max(chosen, key=lambda tit: tit.duration_seconds)
    extra_titles = [tit for tit in chosen if tit.index != main_title.index]
    return main_title, extra_titles


def _prompt_encode_format() -> str:
    """Prompt the user to select an encoding format; return resolved value."""
    click.echo("Select encoding format:")
    click.echo("  [1] h264  (x264, RF 20 default)")
    click.echo("  [2] h265  (x265, RF 22 default)")
    click.echo("  [3] none  (keep original MKV, no re-encoding)")
    click.echo()
    choice = click.prompt("Choice", type=click.IntRange(1, 3), default=3)
    return ["h264", "h265", "none"][choice - 1]


def _classify_extras_interactive(  # pylint: disable=too-many-locals
    main_title: Title,
    extra_titles: list[Title],
    movie_dir: Path,
    signals_map: Optional[dict[int, TitleSignals]] = None,
) -> Selection:
    """Prompt user to classify each extra, showing the heuristic suggestion first.

    Shows title metadata and the heuristic type guess (with confidence) for
    each extra, then allows the user to accept or override both the type and
    the display name.
    """
    if signals_map is None:
        signals_map = {}
    counters: dict[str, int] = scan_existing_extras(movie_dir)
    classified: list[ClassifiedExtra] = []

    if extra_titles:
        click.echo("Classify extras:")

    for extra in extra_titles:
        sig = signals_map.get(extra.index, TitleSignals(title=extra))
        result = classify_extra(sig)

        vts_str = f"VTS {sig.vts_number}" if sig.vts_number is not None else "VTS ?"
        click.echo(
            f"  Title {extra.index} | {extra.duration} | "
            f"{extra.chapter_count} chapter(s) | {vts_str} | {extra.name!r}"
        )
        click.echo(
            f"    Suggested type: {result.extra_type}  "
            f"[confidence: {result.confidence}]"
        )

        types_str = "/".join(_EXTRA_TYPES)
        raw_type = click.prompt(
            f"    Type (Enter to accept, or: {types_str})",
            default="",
            show_default=False,
        ).strip().lower()
        if raw_type in _EXTRA_TYPES:
            extra_type: JellyfinExtraType = raw_type  # type: ignore[assignment]
        else:
            extra_type = result.extra_type

        default_name = result.display_name or ""
        raw_name = click.prompt(
            "    Display name",
            default=default_name,
            show_default=bool(default_name),
        ).strip()
        title_name: Optional[str] = raw_name if raw_name else None

        counter = counters.get(extra_type, 0) + 1
        counters[extra_type] = counter
        filename = build_extra_filename(extra_type, counter, title_name=title_name)
        classified.append(
            ClassifiedExtra(
                title=extra,
                extra_type=extra_type,
                output_filename=filename,
            )
        )

    return Selection(main=main_title, extras=classified)


# ---------------------------------------------------------------------------
# Scan-to-JSON helpers
# ---------------------------------------------------------------------------


def _build_scan_hint(
    episodes: list[Title],
    signals_map: dict[int, TitleSignals],
) -> str:
    """Build a human-readable hint string summarising the episode cluster."""
    if not episodes:
        return "No episode candidates detected"

    vts_counts: dict[int, int] = {}
    for title in episodes:
        sig = signals_map.get(title.index)
        if sig is not None and sig.vts_number is not None:
            vts_counts[sig.vts_number] = vts_counts.get(sig.vts_number, 0) + 1

    majority_vts: Optional[int] = None
    if vts_counts:
        majority_vts = max(vts_counts, key=lambda key: vts_counts[key])

    durations = sorted(title.duration_seconds for title in episodes)
    median_dur = durations[len(durations) // 2]
    minutes = median_dur // 60

    hint = f"Detected {len(episodes)} episode candidate(s)"
    if majority_vts is not None:
        hint += f" on VTS {majority_vts}"
    hint += f" (~{minutes} min each)"
    return hint


def _write_scan_json(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    output_path: Path,
    job_type: str,
    device: str,
    append: bool,
    scan_hint: Optional[str] = None,
) -> None:
    """Write (or append) a single-job scan envelope to *output_path*.

    The job contains null metadata fields as placeholders for the user to fill
    in before passing the file to ``diskripr queue run``.

    Args:
        output_path: Destination JSON file path.
        job_type:    ``"movie"`` or ``"show"``.
        device:      Block device used during the scan (stored in options).
        append:      When ``True`` and the file already exists, append the new
                     job to its ``jobs`` array rather than overwriting.
        scan_hint:   Optional show scan hint string; written as ``_scan_hint``
                     on the job object when provided.

    Raises:
        :class:`click.ClickException`: The target file exists but is not a
            valid job envelope (missing ``jobs`` array).
    """
    job: dict = {
        "id": str(uuid.uuid4()),
        "type": job_type,
    }
    if job_type == "movie":
        job["movie"] = {"name": None, "year": None}
    else:
        job["show"] = {"name": None, "season": None, "start_episode": None}
        if scan_hint is not None:
            job["_scan_hint"] = scan_hint
    job["options"] = {"device": device}

    if append and output_path.exists():
        with output_path.open("r", encoding="utf-8") as file_handle:
            try:
                envelope = json.load(file_handle)
            except json.JSONDecodeError as exc:
                raise click.ClickException(
                    f"{output_path} exists but could not be parsed as JSON: {exc}"
                ) from exc
        if not isinstance(envelope, dict) or "jobs" not in envelope:
            raise click.ClickException(
                f"{output_path} exists but is not a valid job file "
                "(missing 'jobs' array)"
            )
        envelope["jobs"].append(job)
    else:
        envelope = {"version": "1.0", "jobs": [job]}

    with output_path.open("w", encoding="utf-8") as file_handle:
        json.dump(envelope, file_handle, indent=2)
    click.echo(f"Scan result written to {output_path}")


# ---------------------------------------------------------------------------
# Organize helpers
# ---------------------------------------------------------------------------


_SYNTHETIC_DURATION = "00:00:01"


def _make_synthetic_title(index: int, path: Path) -> Title:
    """Return a placeholder Title for a file found in the temp directory."""
    return Title(
        index=index,
        name=path.stem,
        duration=_SYNTHETIC_DURATION,
        size_bytes=path.stat().st_size,
        chapter_count=0,
        stream_summary="",
        title_type="main" if index == 0 else "extra",
    )


def _build_organize_selection(
    temp_dir: Path,
    movie_dir: Path,
) -> tuple[list[RipResult], Selection]:
    """Scan *temp_dir* for MKV files and build synthetic rip results + selection.

    Largest file by size is the main feature; all others become extras.
    """
    mkv_files = sorted(
        temp_dir.glob("*.mkv"),
        key=lambda pth: pth.stat().st_size,
        reverse=True,
    )
    if not mkv_files:
        raise click.ClickException(
            f"No MKV files found in temp directory: {temp_dir}\n"
            "Set DISKRIPR_TEMP_DIR to the directory containing the ripped files."
        )

    rip_results: list[RipResult] = []
    titles: list[Title] = []
    for idx, mkv_path in enumerate(mkv_files):
        rip_results.append(
            RipResult(title_index=idx, output_path=mkv_path, success=True)
        )
        titles.append(_make_synthetic_title(idx, mkv_path))

    counters = scan_existing_extras(movie_dir)
    classified: list[ClassifiedExtra] = []
    for extra_title in titles[1:]:
        extra_type: JellyfinExtraType = "extra"
        counter = counters.get(extra_type, 0) + 1
        counters[extra_type] = counter
        filename = build_extra_filename(extra_type, counter)
        classified.append(
            ClassifiedExtra(
                title=extra_title,
                extra_type=extra_type,
                output_filename=filename,
            )
        )

    return rip_results, Selection(main=titles[0], extras=classified)


def _build_show_organize_selection(
    temp_dir: Path,
    config: ShowConfig,
) -> tuple[list[RipResult], ShowSelection]:
    """Scan *temp_dir* for MKV files and build synthetic show rip results + selection.

    Files are sorted by name (MakeMKV typically names them by title index) and
    assigned as episodes starting from ``config.start_episode``.
    """
    mkv_files = sorted(temp_dir.glob("*.mkv"), key=lambda pth: pth.name)
    if not mkv_files:
        raise click.ClickException(
            f"No MKV files found in temp directory: {temp_dir}\n"
            "Set DISKRIPR_TEMP_DIR to the directory containing the ripped files."
        )

    rip_results: list[RipResult] = []
    episode_entries: list[EpisodeEntry] = []
    for idx, mkv_path in enumerate(mkv_files):
        rip_results.append(
            RipResult(title_index=idx, output_path=mkv_path, success=True)
        )
        title = _make_synthetic_title(idx, mkv_path)
        episode_entries.append(
            EpisodeEntry(
                title=title,
                season_number=config.season_number,
                episode_number=config.start_episode + idx,
            )
        )

    return rip_results, ShowSelection(episodes=episode_entries, extras=[])


# ---------------------------------------------------------------------------
# Shared option decorators
# ---------------------------------------------------------------------------


def _movie_options(func):  # type: ignore[no-untyped-def]
    """Attach movie identity options to a Click command."""
    func = click.option(
        "--disc",
        "disc_number",
        type=int,
        default=None,
        metavar="N",
        help="Disc number for multi-disc movies (1, 2, …).",
    )(func)
    func = click.option(
        "-d",
        "--device",
        default="/dev/sr0",
        show_default=True,
        help="Optical drive block device.",
    )(func)
    func = click.option(
        "-o",
        "--output-dir",
        type=click.Path(path_type=Path),
        default="dvd_output",
        show_default=True,
        help="Base output directory.",
    )(func)
    func = click.option(
        "-y",
        "--year",
        "movie_year",
        type=int,
        required=True,
        help="Movie release year.",
    )(func)
    func = click.option(
        "-n",
        "--name",
        "movie_name",
        required=True,
        help="Movie title for Jellyfin naming.",
    )(func)
    return func


def _show_options(func):  # type: ignore[no-untyped-def]
    """Attach show identity options (name, season, start-episode) to a Click command."""
    func = click.option(
        "--disc",
        "disc_number",
        type=int,
        default=None,
        metavar="N",
        help="Disc number for multi-disc seasons (1, 2, …).",
    )(func)
    func = click.option(
        "-d",
        "--device",
        default="/dev/sr0",
        show_default=True,
        help="Optical drive block device.",
    )(func)
    func = click.option(
        "-o",
        "--output-dir",
        type=click.Path(path_type=Path),
        default="dvd_output",
        show_default=True,
        help="Base output directory.",
    )(func)
    func = click.option(
        "--start-episode",
        type=int,
        required=True,
        help="Episode number of the first title on this disc (≥ 1).",
    )(func)
    func = click.option(
        "--season",
        "season_number",
        type=int,
        required=True,
        help="Season number (0 = Jellyfin specials).",
    )(func)
    func = click.option(
        "--show",
        "show_name",
        required=True,
        help="Series title for Jellyfin naming.",
    )(func)
    return func


def _eject_option(func):  # type: ignore[no-untyped-def]
    return click.option(
        "--eject/--no-eject",
        "eject_on_complete",
        default=True,
        show_default=True,
        help="Eject disc when finished.",
    )(func)


def _keep_original_option(func):  # type: ignore[no-untyped-def]
    return click.option(
        "--keep-original",
        is_flag=True,
        default=False,
        help="Preserve pre-encode MKVs in an originals/ subdirectory.",
    )(func)


def _rip_encode_options(func):  # type: ignore[no-untyped-def]
    """Attach rip-mode, encode-format, quality, and min-length options."""
    func = click.option(
        "--min-length",
        type=int,
        default=10,
        show_default=True,
        help="Minimum title duration in seconds.",
    )(func)
    func = click.option(
        "--quality",
        type=int,
        default=None,
        help="HandBrake RF quality (0–51). Defaults to 20 (h264) or 22 (h265).",
    )(func)
    func = click.option(
        "--encode",
        "encode_format",
        type=click.Choice(["none", "h264", "h265", "ask"]),
        default="none",
        show_default=True,
        help="Encoding format, or 'ask' to choose interactively.",
    )(func)
    func = click.option(
        "--rip-mode",
        type=click.Choice(["main", "all", "ask"]),
        default="main",
        show_default=True,
        help="Title selection: main (longest), all, or ask (interactive).",
    )(func)
    return func


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option()
def cli() -> None:
    """diskripr — Rip DVDs to a Jellyfin-compatible library."""
    _configure_logging()


# ---------------------------------------------------------------------------
# diskripr movie
# ---------------------------------------------------------------------------


@cli.group("movie")
def movie_group() -> None:
    """Commands for ripping movie DVDs."""


# ---------------------------------------------------------------------------
# diskripr movie scan
# ---------------------------------------------------------------------------


@movie_group.command("scan")
@click.option(
    "-d",
    "--device",
    default="/dev/sr0",
    show_default=True,
    help="Optical drive block device.",
)
@click.option(
    "--min-length",
    type=int,
    default=10,
    show_default=True,
    help="Minimum title duration in seconds.",
)
@click.option(
    "--output-json",
    "output_json",
    type=click.Path(path_type=Path),
    default=None,
    metavar="PATH",
    help="Write scan result as a partial job-file entry to PATH.",
)
@click.option(
    "--append",
    is_flag=True,
    default=False,
    help="Append the new job to an existing job file rather than overwriting.",
)
def cmd_movie_scan(
    device: str,
    min_length: int,
    output_json: Optional[Path],
    append: bool,
) -> None:
    """Scan the disc and list available titles without ripping."""
    _check_required_deps()
    if not check_available("lsdvd"):
        click.echo("Warning: 'lsdvd' not found — apt install lsdvd", err=True)

    cfg = MovieConfig(
        movie_name="_scan_",
        movie_year=2000,
        device=device,
        min_length=min_length,
    )
    pipeline = MoviePipeline(cfg)
    try:
        disc_info = pipeline.discover()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    _display_disc(disc_info)

    if output_json is not None:
        _write_scan_json(output_json, "movie", device, append)


# ---------------------------------------------------------------------------
# diskripr movie rip
# ---------------------------------------------------------------------------


@movie_group.command("rip")
@_movie_options
@_keep_original_option
@_eject_option
@_rip_encode_options
def cmd_movie_rip(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    movie_name: str,
    movie_year: int,
    output_dir: Path,
    device: str,
    disc_number: Optional[int],
    keep_original: bool,
    eject_on_complete: bool,
    rip_mode: str,
    encode_format: str,
    quality: Optional[int],
    min_length: int,
) -> None:
    """Rip a movie DVD through the full pipeline into the Jellyfin library."""
    _check_required_deps()
    wants_handbrake = encode_format not in ("none", "ask")
    _warn_optional_deps(check_handbrake=wants_handbrake)

    if encode_format == "ask":
        encode_format = _prompt_encode_format()
        click.echo()

    resolved_encode: str = encode_format  # now "h264", "h265", or "none"
    resolved_rip_mode: str = rip_mode if rip_mode != "ask" else "main"

    cfg = MovieConfig.from_click_params(
        movie_name=movie_name,
        movie_year=movie_year,
        output_dir=output_dir,
        device=device,
        disc_number=disc_number,
        rip_mode=resolved_rip_mode,  # type: ignore[arg-type]
        encode_format=resolved_encode,  # type: ignore[arg-type]
        quality=quality,
        min_length=min_length,
        keep_original=keep_original,
        eject_on_complete=eject_on_complete,
    )
    try:
        cfg.validate()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    pipeline = MoviePipeline(cfg)
    progress = _ProgressReporter()

    try:
        disc_info = pipeline.discover(progress)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo()

    safe_name = sanitize_filename(movie_name)
    movie_dir = output_dir / "movies" / f"{safe_name} ({movie_year})"

    if rip_mode == "ask":
        signals_map = pipeline._build_signals_map(disc_info.titles)  # pylint: disable=protected-access
        main_title, extra_titles = _prompt_title_selection(disc_info)
        pipeline.selection = _classify_extras_interactive(
            main_title, extra_titles, movie_dir, signals_map
        )
    else:
        main_title, extra_titles = _select(disc_info.titles, rip_mode)
        pipeline.selection = _classify(main_title, extra_titles, movie_dir)

    click.echo(
        f"Selected: main={pipeline.selection.main.name!r}, "
        f"extras={len(pipeline.selection.extras)}"
    )
    click.echo()

    try:
        pipeline.rip(progress)
    except Exception as exc:  # pylint: disable=broad-except
        raise click.ClickException(f"Rip stage failed: {exc}") from exc
    click.echo()

    pipeline.encode(progress)
    if pipeline.encode_results:
        click.echo()

    try:
        output_paths = pipeline.organize()
    except Exception as exc:  # pylint: disable=broad-except
        raise click.ClickException(f"Organize stage failed: {exc}") from exc

    click.echo("Done. Output files:")
    for out_path in output_paths:
        click.echo(f"  {out_path}")


# ---------------------------------------------------------------------------
# diskripr movie organize
# ---------------------------------------------------------------------------


@movie_group.command("organize")
@_movie_options
@_keep_original_option
@_eject_option
def cmd_movie_organize(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    movie_name: str,
    movie_year: int,
    output_dir: Path,
    device: str,
    disc_number: Optional[int],
    keep_original: bool,
    eject_on_complete: bool,
) -> None:
    """Organize already-ripped MKVs from the temp directory into Jellyfin tree.

    Reads temp dir from DISKRIPR_TEMP_DIR or <output-dir>/.tmp.
    The largest file is treated as the main feature; all others become extras.
    """
    cfg = MovieConfig.from_click_params(
        movie_name=movie_name,
        movie_year=movie_year,
        output_dir=output_dir,
        device=device,
        disc_number=disc_number,
        keep_original=keep_original,
        eject_on_complete=eject_on_complete,
    )

    # pipeline.rip() writes to config.temp_dir / ".tmp"
    candidate_temp = cfg.temp_dir / ".tmp"
    actual_temp = candidate_temp if candidate_temp.exists() else cfg.temp_dir

    safe_name = sanitize_filename(movie_name)
    movie_dir = output_dir / "movies" / f"{safe_name} ({movie_year})"

    rip_results, selection = _build_organize_selection(actual_temp, movie_dir)

    pipeline = MoviePipeline(cfg)
    pipeline.rip_results = rip_results
    pipeline.selection = selection

    try:
        output_paths = pipeline.organize()
    except Exception as exc:  # pylint: disable=broad-except
        raise click.ClickException(f"Organize failed: {exc}") from exc

    click.echo("Done. Output files:")
    for out_path in output_paths:
        click.echo(f"  {out_path}")


# ---------------------------------------------------------------------------
# diskripr show
# ---------------------------------------------------------------------------


@cli.group("show")
def show_group() -> None:
    """Commands for ripping TV season DVDs."""


# ---------------------------------------------------------------------------
# diskripr show scan
# ---------------------------------------------------------------------------


@show_group.command("scan")
@click.option(
    "--show",
    "show_name",
    default=None,
    help="Series name (optional; used only for display/context).",
)
@click.option(
    "-d",
    "--device",
    default="/dev/sr0",
    show_default=True,
    help="Optical drive block device.",
)
@click.option(
    "--min-length",
    type=int,
    default=10,
    show_default=True,
    help="Minimum title duration in seconds.",
)
@click.option(
    "--output-json",
    "output_json",
    type=click.Path(path_type=Path),
    default=None,
    metavar="PATH",
    help="Write scan result as a partial job-file entry to PATH.",
)
@click.option(
    "--append",
    is_flag=True,
    default=False,
    help="Append the new job to an existing job file rather than overwriting.",
)
def cmd_show_scan(
    show_name: Optional[str],
    device: str,
    min_length: int,
    output_json: Optional[Path],
    append: bool,
) -> None:
    """Scan the disc and print an episode cluster guess without ripping."""
    _check_required_deps()
    if not check_available("lsdvd"):
        click.echo("Warning: 'lsdvd' not found — apt install lsdvd", err=True)

    cfg = ShowConfig(
        show_name=show_name or "_scan_",
        season_number=1,
        start_episode=1,
        device=device,
        min_length=min_length,
    )
    pipeline = ShowPipeline(cfg)
    try:
        disc_info = pipeline.discover()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    _display_disc(disc_info)

    signals_map = pipeline._build_signals_map(disc_info.titles)  # pylint: disable=protected-access
    episodes, extra_titles = cluster_episodes(disc_info.titles, signals_map)
    _display_show_cluster(episodes, extra_titles, signals_map)

    if output_json is not None:
        hint = _build_scan_hint(episodes, signals_map)
        _write_scan_json(output_json, "show", device, append, scan_hint=hint)


# ---------------------------------------------------------------------------
# diskripr show rip
# ---------------------------------------------------------------------------


@show_group.command("rip")
@_show_options
@_keep_original_option
@_eject_option
@_rip_encode_options
def cmd_show_rip(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    show_name: str,
    season_number: int,
    start_episode: int,
    output_dir: Path,
    device: str,
    disc_number: Optional[int],
    keep_original: bool,
    eject_on_complete: bool,
    rip_mode: str,
    encode_format: str,
    quality: Optional[int],
    min_length: int,
) -> None:
    """Rip a TV season disc through the full pipeline into the Jellyfin library."""
    _check_required_deps()
    wants_handbrake = encode_format not in ("none", "ask")
    _warn_optional_deps(check_handbrake=wants_handbrake)

    if encode_format == "ask":
        encode_format = _prompt_encode_format()
        click.echo()

    resolved_encode: str = encode_format
    resolved_rip_mode: str = rip_mode if rip_mode != "ask" else "all"

    cfg = ShowConfig(
        show_name=show_name,
        season_number=season_number,
        start_episode=start_episode,
        output_dir=output_dir,
        device=device,
        disc_number=disc_number,
        rip_mode=resolved_rip_mode,  # type: ignore[arg-type]
        encode_format=resolved_encode,  # type: ignore[arg-type]
        quality=quality,
        min_length=min_length,
        keep_original=keep_original,
        eject_on_complete=eject_on_complete,
    )
    try:
        cfg.validate()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    pipeline = ShowPipeline(cfg)
    progress = _ProgressReporter()

    if rip_mode == "ask":
        try:
            pipeline.discover(progress)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc
        click.echo()

        signals_map = pipeline._build_signals_map(pipeline.disc_info.titles)  # pylint: disable=protected-access
        pipeline.selection = pipeline._build_show_selection(signals_map)  # pylint: disable=protected-access
        _display_show_selection(pipeline.selection, signals_map)

        if not click.confirm("Proceed with this selection?", default=True):
            raise click.ClickException("Aborted.")

        try:
            pipeline.rip(progress)
        except Exception as exc:  # pylint: disable=broad-except
            raise click.ClickException(f"Rip stage failed: {exc}") from exc
        click.echo()

        pipeline.encode(progress)
        if pipeline.encode_results:
            click.echo()

        try:
            output_paths = pipeline.organize()
        except Exception as exc:  # pylint: disable=broad-except
            raise click.ClickException(f"Organize stage failed: {exc}") from exc
    else:
        try:
            output_paths = pipeline.run(progress)
        except RuntimeError as exc:
            raise click.ClickException(str(exc)) from exc

    click.echo("Done. Output files:")
    for out_path in output_paths:
        click.echo(f"  {out_path}")


# ---------------------------------------------------------------------------
# diskripr show organize
# ---------------------------------------------------------------------------


@show_group.command("organize")
@_show_options
@_keep_original_option
@_eject_option
def cmd_show_organize(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    show_name: str,
    season_number: int,
    start_episode: int,
    output_dir: Path,
    device: str,
    disc_number: Optional[int],
    keep_original: bool,
    eject_on_complete: bool,
) -> None:
    """Organize already-ripped MKVs from the temp directory into Jellyfin TV tree.

    Reads temp dir from DISKRIPR_TEMP_DIR or <output-dir>/.tmp.
    Files are sorted by name and assigned as episodes starting from
    ``--start-episode``.
    """
    cfg = ShowConfig(
        show_name=show_name,
        season_number=season_number,
        start_episode=start_episode,
        output_dir=output_dir,
        device=device,
        disc_number=disc_number,
        keep_original=keep_original,
        eject_on_complete=eject_on_complete,
    )

    candidate_temp = cfg.temp_dir / ".tmp"
    actual_temp = candidate_temp if candidate_temp.exists() else cfg.temp_dir

    rip_results, selection = _build_show_organize_selection(actual_temp, cfg)

    pipeline = ShowPipeline(cfg)
    pipeline.rip_results = rip_results
    pipeline.selection = selection

    try:
        output_paths = pipeline.organize()
    except Exception as exc:  # pylint: disable=broad-except
        raise click.ClickException(f"Organize failed: {exc}") from exc

    click.echo("Done. Output files:")
    for out_path in output_paths:
        click.echo(f"  {out_path}")


# ---------------------------------------------------------------------------
# diskripr queue
# ---------------------------------------------------------------------------


@cli.group("queue")
def queue_group() -> None:
    """Commands for running a batch job queue from a JSON job file."""


# ---------------------------------------------------------------------------
# diskripr queue check
# ---------------------------------------------------------------------------


@queue_group.command("check")
@click.option(
    "--file",
    "job_file_path",
    type=click.Path(path_type=Path, exists=False),
    required=True,
    metavar="PATH",
    help="Path to the JSON job file to validate.",
)
def cmd_queue_check(job_file_path: Path) -> None:
    """Validate a job file and print a one-line summary per job.

    Exits with status 0 when the file is valid, non-zero otherwise.
    Does not start any rip or require any disc to be present.
    """
    from diskripr.queue import validate_job_file  # pylint: disable=import-outside-toplevel
    from diskripr.schema import JobFile, MovieJob  # pylint: disable=import-outside-toplevel

    errors = validate_job_file(job_file_path)
    if errors:
        for err in errors:
            click.echo(err, err=True)
        raise SystemExit(1)

    # File is valid — parse and print per-job summary.
    import json as _json  # pylint: disable=import-outside-toplevel
    raw = _json.loads(job_file_path.read_text(encoding="utf-8"))
    job_file = JobFile.model_validate(raw)

    total = len(job_file.jobs)
    click.echo(f"Job file OK — {total} job(s):")
    for idx, job in enumerate(job_file.jobs):
        id_str = f"  id={job.id}" if job.id else ""
        if isinstance(job, MovieJob):
            title_str = f"{job.movie.name} ({job.movie.year})"
            type_str = "movie"
        else:
            title_str = f"{job.show.name} S{job.show.season:02d} ep{job.show.start_episode}"
            type_str = "show"
        click.echo(f"  [{idx}] {type_str}: {title_str}{id_str}")


# ---------------------------------------------------------------------------
# diskripr queue run
# ---------------------------------------------------------------------------


@queue_group.command("run")
@click.option(
    "--file",
    "job_file_path",
    type=click.Path(path_type=Path, exists=False),
    required=True,
    metavar="PATH",
    help="Path to the JSON job file to execute.",
)
@click.option(
    "-d",
    "--device",
    default=None,
    help="Global default optical drive block device (overrides built-in /dev/sr0).",
)
@click.option(
    "-o",
    "--output-dir",
    "output_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Global default output directory.",
)
@click.option(
    "--rip-mode",
    type=click.Choice(["main", "all", "ask"]),
    default=None,
    help="Global default title selection mode.",
)
@click.option(
    "--encode",
    "encode_format",
    type=click.Choice(["none", "h264", "h265"]),
    default=None,
    help="Global default encoding format.",
)
@click.option(
    "--quality",
    type=int,
    default=None,
    help="Global default HandBrake RF quality (0–51).",
)
@click.option(
    "--min-length",
    type=int,
    default=None,
    help="Global default minimum title duration in seconds.",
)
@click.option(
    "--keep-original",
    is_flag=True,
    default=False,
    help="Preserve pre-encode MKVs for all jobs.",
)
@click.option(
    "--eject/--no-eject",
    "eject_on_complete",
    default=None,
    help="Global default disc ejection behaviour after each job.",
)
def cmd_queue_run(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    job_file_path: Path,
    device: Optional[str],
    output_dir: Optional[Path],
    rip_mode: Optional[str],
    encode_format: Optional[str],
    quality: Optional[int],
    min_length: Optional[int],
    keep_original: bool,
    eject_on_complete: Optional[bool],
) -> None:
    """Execute all jobs in a JSON job file sequentially.

    Validates the file before starting any rip — exits non-zero and prints all
    errors if the file is invalid.  Warns when any job uses rip_mode='ask'
    (unattended execution with ask mode requires operator presence).

    Flags supplied here act as global defaults for all jobs; a value set in a
    job's own ``options`` object always takes precedence.
    """
    from diskripr.queue import QueueRunner, validate_job_file  # pylint: disable=import-outside-toplevel
    from diskripr.schema import JobFile, MovieJob  # pylint: disable=import-outside-toplevel

    errors = validate_job_file(job_file_path)
    if errors:
        for err in errors:
            click.echo(err, err=True)
        raise SystemExit(1)

    import json as _json  # pylint: disable=import-outside-toplevel
    raw = _json.loads(job_file_path.read_text(encoding="utf-8"))
    job_file = JobFile.model_validate(raw)

    # Warn if any job's effective rip_mode would be 'ask' (checking job-level
    # only; full resolution happens inside QueueRunner, which also warns).
    for job in job_file.jobs:
        job_rip = job.options.rip_mode if job.options else None
        effective_rip = job_rip or rip_mode
        if effective_rip == "ask":
            click.echo(
                "Warning: one or more jobs use rip_mode='ask' — "
                "unattended queue run will require operator interaction.",
                err=True,
            )
            break

    # Build global_overrides: only include options the user explicitly supplied.
    global_overrides: dict = {}
    if device is not None:
        global_overrides["device"] = device
    if output_dir is not None:
        global_overrides["output_dir"] = str(output_dir)
    if rip_mode is not None:
        global_overrides["rip_mode"] = rip_mode
    if encode_format is not None:
        global_overrides["encode_format"] = encode_format
    if quality is not None:
        global_overrides["quality"] = quality
    if min_length is not None:
        global_overrides["min_length"] = min_length
    if keep_original:
        global_overrides["keep_original"] = True
    if eject_on_complete is not None:
        global_overrides["eject_on_complete"] = eject_on_complete

    total = len(job_file.jobs)
    click.echo(f"Starting queue: {total} job(s) from {job_file_path}")
    for idx, job in enumerate(job_file.jobs):
        if isinstance(job, MovieJob):
            title_str = f"{job.movie.name} ({job.movie.year})"
        else:
            title_str = f"{job.show.name} S{job.show.season:02d} ep{job.show.start_episode}"
        click.echo(f"  [{idx}] {title_str}")
    click.echo()

    runner = QueueRunner()
    try:
        runner.run(job_file, global_overrides)
    except TimeoutError as exc:
        raise click.ClickException(f"Disc swap timed out: {exc}") from exc
    except Exception as exc:  # pylint: disable=broad-except
        raise click.ClickException(str(exc)) from exc

    click.echo("Queue complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point registered in pyproject.toml."""
    cli()
