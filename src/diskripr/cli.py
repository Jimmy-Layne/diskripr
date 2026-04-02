"""Click-based command-line interface for diskripr.

Defines the top-level ``diskripr`` command group and three subcommands:

- ``rip``      — Full pipeline: discover → rip → encode → organize.
- ``scan``     — Discover stage only; inspects a disc without ripping.
- ``organize`` — Organize stage only; re-sorts already-ripped files from the
                 temp directory (reads location from ``DISKRIPR_TEMP_DIR`` or
                 ``<output_dir>/.tmp``).

This is the only module that writes to stdout or prompts the user. It builds a
``Config`` from Click parameters, wires up a progress callback backed by
Click's echo output, and delegates all work to ``pipeline``.

Typical multi-disc workflow::

    $ diskripr rip -n "Lawrence of Arabia" -y 1962 --disc 1
    # swap disc
    $ diskripr rip -n "Lawrence of Arabia" -y 1962 --disc 2
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import click

from diskripr.config import Config, ConfigError
from diskripr.drivers.base import check_available
from diskripr.models import (
    ClassifiedExtra,
    DiscInfo,
    JellyfinExtraType,
    RipResult,
    Selection,
    Title,
)
from diskripr.pipeline import Pipeline, _classify, _select  # noqa: WPS450
from diskripr.util.filesystem import (
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


def _prompt_extra_type(extra: Title) -> JellyfinExtraType:
    """Prompt the user to classify a single extra title."""
    size_str = format_size(extra.size_bytes)
    click.echo(f"  Classify: {extra.name!r}  ({extra.duration}, {size_str})")
    for num, key in enumerate(_EXTRA_TYPES, 1):
        click.echo(f"    [{num}] {_EXTRA_TYPE_LABELS[key]}")
    default_idx = _EXTRA_TYPES.index("extra") + 1
    choice = click.prompt(
        "  Type",
        type=click.IntRange(1, len(_EXTRA_TYPES)),
        default=default_idx,
    )
    return _EXTRA_TYPES[choice - 1]


def _classify_extras_interactive(
    main_title: Title,
    extra_titles: list[Title],
    extras_dir: Path,
) -> Selection:
    """Prompt the user to classify each extra; return a populated Selection."""
    counters: dict[str, int] = scan_existing_extras(extras_dir)
    classified: list[ClassifiedExtra] = []

    if extra_titles:
        click.echo("Classify extras:")

    for extra in extra_titles:
        extra_type = _prompt_extra_type(extra)
        counter = counters.get(extra_type, 0) + 1
        counters[extra_type] = counter
        filename = build_extra_filename(extra_type, counter)
        classified.append(
            ClassifiedExtra(
                title=extra,
                extra_type=extra_type,
                output_filename=filename,
            )
        )

    return Selection(main=main_title, extras=classified)


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
    extras_dir: Path,
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

    counters = scan_existing_extras(extras_dir)
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


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------


@click.group()
@click.version_option()
def cli() -> None:
    """diskripr — Rip DVDs to a Jellyfin-compatible library."""
    _configure_logging()


# ---------------------------------------------------------------------------
# diskripr scan
# ---------------------------------------------------------------------------


@cli.command("scan")
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
    default=30,
    show_default=True,
    help="Minimum title duration in seconds.",
)
def cmd_scan(device: str, min_length: int) -> None:
    """Scan the disc and list available titles without ripping."""
    _check_required_deps()
    if not check_available("lsdvd"):
        click.echo("Warning: 'lsdvd' not found — apt install lsdvd", err=True)

    cfg = Config(
        movie_name="_scan_",
        movie_year=2000,
        device=device,
        min_length=min_length,
    )
    pipeline = Pipeline(cfg)
    try:
        disc_info = pipeline.discover()
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc

    _display_disc(disc_info)


# ---------------------------------------------------------------------------
# diskripr rip
# ---------------------------------------------------------------------------


@cli.command("rip")
@_movie_options
@_keep_original_option
@_eject_option
@click.option(
    "--rip-mode",
    type=click.Choice(["main", "all", "ask"]),
    default="main",
    show_default=True,
    help="Title selection: main (longest), all, or ask (interactive).",
)
@click.option(
    "--encode",
    "encode_format",
    type=click.Choice(["none", "h264", "h265", "ask"]),
    default="none",
    show_default=True,
    help="Encoding format, or 'ask' to choose interactively.",
)
@click.option(
    "--quality",
    type=int,
    default=None,
    help="HandBrake RF quality (0–51). Defaults to 20 (h264) or 22 (h265).",
)
@click.option(
    "--min-length",
    type=int,
    default=30,
    show_default=True,
    help="Minimum title duration in seconds.",
)
def cmd_rip(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
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
    """Rip a DVD through the full pipeline into the Jellyfin library."""
    _check_required_deps()
    wants_handbrake = encode_format not in ("none", "ask")
    _warn_optional_deps(check_handbrake=wants_handbrake)

    if encode_format == "ask":
        encode_format = _prompt_encode_format()
        click.echo()

    resolved_encode: str = encode_format  # now "h264", "h265", or "none"
    resolved_rip_mode: str = rip_mode if rip_mode != "ask" else "main"

    cfg = Config.from_click_params(
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

    pipeline = Pipeline(cfg)
    progress = _ProgressReporter()

    try:
        disc_info = pipeline.discover(progress)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo()

    safe_name = sanitize_filename(movie_name)
    extras_dir = (
        output_dir / "Movies" / f"{safe_name} ({movie_year})" / "extras"
    )

    if rip_mode == "ask":
        main_title, extra_titles = _prompt_title_selection(disc_info)
        pipeline.selection = _classify_extras_interactive(
            main_title, extra_titles, extras_dir
        )
    else:
        main_title, extra_titles = _select(disc_info.titles, rip_mode)
        pipeline.selection = _classify(main_title, extra_titles, extras_dir)

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
# diskripr organize
# ---------------------------------------------------------------------------


@cli.command("organize")
@_movie_options
@_keep_original_option
@_eject_option
def cmd_organize(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    # pylint: disable=too-many-locals
    movie_name: str,
    movie_year: int,
    output_dir: Path,
    device: str,
    disc_number: Optional[int],
    keep_original: bool,
    eject_on_complete: bool,
) -> None:
    """Organize already-ripped MKVs from the temp directory into Jellyfin tree.

    Reads temp dir from DISKRIPR_TEMP_DIR or <output-dir>/.tmp/.tmp.
    The largest file is treated as the main feature; all others become extras.
    """
    cfg = Config.from_click_params(
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
    extras_dir = (
        output_dir / "Movies" / f"{safe_name} ({movie_year})" / "extras"
    )

    rip_results, selection = _build_organize_selection(actual_temp, extras_dir)

    pipeline = Pipeline(cfg)
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
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point registered in pyproject.toml."""
    cli()
