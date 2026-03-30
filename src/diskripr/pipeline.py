"""Pipeline orchestrator for the diskripr workflow.

The primary interface is the :class:`Pipeline` class, which is instantiated
with a :class:`~diskripr.config.Config` and exposes each stage as a method.
Stage results are stored as public instance attributes so the caller can read
intermediate state or inject values between stages (e.g. a pre-built
:class:`~diskripr.models.Selection` from an interactive CLI prompt):

.. code-block:: python

    pipeline = Pipeline(config)
    pipeline.discover()            # sets pipeline.disc_info

    # For ask-mode: build selection interactively from pipeline.disc_info.titles
    # and assign it before calling rip:
    pipeline.selection = my_selection

    pipeline.rip()                 # sets pipeline.rip_results
    pipeline.encode()              # sets pipeline.encode_results
    pipeline.organize()            # sets pipeline.output_paths

Or run the full chain at once:

.. code-block:: python

    output_paths = Pipeline(config).run()

Stage state attributes:

- ``disc_info``      — set by :meth:`~Pipeline.discover`
- ``selection``      — set by :meth:`~Pipeline.run`, or injected externally
- ``rip_results``    — set by :meth:`~Pipeline.rip`
- ``encode_results`` — set by :meth:`~Pipeline.encode`
- ``output_paths``   — set by :meth:`~Pipeline.organize`

The pipeline only handles resolved ``rip_mode`` values (``"main"`` or
``"all"``) and ``encode_format`` values (``"h264"``, ``"h265"``, or
``"none"``).  The CLI is responsible for resolving ``"ask"`` interactively
and assigning the resulting :class:`~diskripr.models.Selection` to
``pipeline.selection`` before calling :meth:`~Pipeline.rip`.

Error propagation: if a title fails to rip it is excluded from subsequent
stages with a warning rather than a crash.  Stage-level exceptions surface as
typed errors (:exc:`~diskripr.drivers.base.RipError`,
:exc:`~diskripr.drivers.base.EncodeError`) so the caller can handle them
distinctly.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from diskripr.config import Config
from diskripr.drivers.base import EncodeError, RipError, ToolNotFound
from diskripr.drivers.ffprobe import FfprobeDriver
from diskripr.drivers.handbrake import HandBrakeDriver
from diskripr.drivers.lsdvd import LsdvdDriver
from diskripr.drivers.makemkv import MakeMKVDriver
from diskripr.models import (
    ClassifiedExtra,
    DiscInfo,
    EncodeResult,
    JellyfinExtraType,
    RipResult,
    Selection,
    Title,
)
from diskripr.util.filesystem import (
    build_extra_filename,
    build_jellyfin_tree,
    build_main_feature_filename,
    cleanup,
    eject_disc,
    format_size,
    make_temp_dir,
    safe_move,
    sanitize_filename,
    scan_existing_extras,
)
from diskripr.util.progress import ProgressCallback

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal decision logic (pure functions — no I/O, no external tools)
# ---------------------------------------------------------------------------

def _select(titles: list[Title], rip_mode: str) -> tuple[Title, list[Title]]:
    """Choose the main title and extras list based on *rip_mode*.

    Args:
        titles:    Title list already filtered by ``min_length``.
        rip_mode:  ``"main"`` or ``"all"``.  ``"ask"`` must be resolved by the
                   CLI before calling this function.

    Returns:
        ``(main, extras)`` — *main* is the longest title; *extras* contains
        all remaining titles when ``rip_mode="all"``, or an empty list when
        ``rip_mode="main"``.
    """
    main = max(titles, key=lambda title: title.duration_seconds)
    if rip_mode == "all":
        extras = [title for title in titles if title.index != main.index]
        return main, extras
    return main, []


def _classify(
    main: Title,
    extras: list[Title],
    extras_dir: Path,
) -> Selection:
    """Assign Jellyfin extra types and output filenames to each extra title.

    All extras are classified as ``"extra"`` (the generic Jellyfin extra type)
    in non-interactive pipeline mode.  Counters continue from the highest
    existing counter in *extras_dir* so multi-disc runs do not collide.

    Args:
        main:        The main feature title.
        extras:      Extra titles to classify.
        extras_dir:  The Jellyfin ``extras/`` directory (may not yet exist).

    Returns:
        :class:`~diskripr.models.Selection` with a fully-populated extras list.
    """
    counters = scan_existing_extras(extras_dir)
    classified: list[ClassifiedExtra] = []
    for extra in extras:
        extra_type: JellyfinExtraType = "extra"
        current = counters.get(extra_type, 0) + 1
        counters[extra_type] = current
        filename = build_extra_filename(extra_type, current)
        classified.append(
            ClassifiedExtra(
                title=extra,
                extra_type=extra_type,
                output_filename=filename,
            )
        )
    return Selection(main=main, extras=classified)


def _inspect(paths: list[Path]) -> None:
    """Run ffprobe stream inspection on *paths* and log the results.

    Silently skipped when ffprobe is unavailable or inspection fails for a
    file.  Never gates any pipeline step.
    """
    ffprobe = FfprobeDriver()
    if not ffprobe.is_available():
        log.debug("ffprobe not available; stream inspection skipped")
        return
    for path in paths:
        report = ffprobe.inspect(path)
        if report is not None:
            log.info(
                "Stream report for %s: %d video, %d audio, %d subtitle track(s)",
                path.name,
                len(report.video_tracks),
                len(report.audio_tracks),
                len(report.subtitle_tracks),
            )


# ---------------------------------------------------------------------------
# Pipeline class
# ---------------------------------------------------------------------------

class Pipeline:
    """Stateful orchestrator for the diskripr DVD ripping workflow.

    Instantiate with a :class:`~diskripr.config.Config`, then call stage
    methods in order.  Each method stores its output on the instance so
    subsequent stages can access it without passing arguments between calls.

    Stages:

    1. :meth:`discover` — detect the drive and scan titles.
    2. (selection) — built by :meth:`run`, or set externally on
       :attr:`Pipeline.selection` for ask-mode CLI workflows.
    3. :meth:`rip` — extract titles to the temp directory.
    4. :meth:`encode` — optional re-encoding with HandBrakeCLI.
    5. :meth:`organize` — move files into the Jellyfin tree.

    Use :meth:`run` to execute all stages in one call.
    """

    def __init__(self, config: Config) -> None:
        self.config = config
        self.disc_info: Optional[DiscInfo] = None
        self.selection: Optional[Selection] = None
        self.rip_results: list[RipResult] = []
        self.encode_results: list[EncodeResult] = []
        self.output_paths: list[Path] = []

    # ------------------------------------------------------------------
    # Stage 1: Discover
    # ------------------------------------------------------------------

    def discover(  # pylint: disable=unused-argument
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> DiscInfo:
        """Detect the disc drive and scan all available titles.

        Steps:

        1. Verify the configured block device exists.
        2. Run ``lsdvd`` for a quick disc title pre-check (non-fatal).
        3. Scan drives with MakeMKV; match the configured device path to a
           drive index.  Falls back to drive index 0 if no match is found.
        4. Scan all titles on the matched drive.
        5. Filter titles shorter than ``config.min_length``.
        6. Fail with a diagnostic message if no titles remain.

        Sets :attr:`Pipeline.disc_info` on the instance.

        Args:
            on_progress:  Reserved for future use; not used by this stage.

        Returns:
            The populated :class:`~diskripr.models.DiscInfo`.

        Raises:
            RuntimeError: Device not found, no accessible MakeMKV drive, or
                          no titles remain after ``min_length`` filtering.
        """
        if not Path(self.config.device).exists():
            raise RuntimeError(
                f"Device not found: {self.config.device!r}. "
                "Run 'lsblk' to list available block devices."
            )

        disc_title = ""
        lsdvd = LsdvdDriver()
        try:
            disc = lsdvd.read_disc(self.config.device)
            if disc is not None:
                disc_title = disc.disc_title
                log.info("lsdvd disc title: %s", disc_title)
            else:
                log.debug("lsdvd returned no disc info for %s", self.config.device)
        except ToolNotFound:
            log.debug("lsdvd not available; skipping pre-check")

        makemkv = MakeMKVDriver()
        drives = makemkv.scan_drives()

        drive_info = next(
            (drv for drv in drives if drv.device == self.config.device),
            None,
        )
        if drive_info is None:
            if not drives:
                raise RuntimeError(
                    f"No accessible drives found by MakeMKV for device "
                    f"{self.config.device!r}. "
                    "Ensure the disc is inserted and MakeMKV can see the drive."
                )
            fallback = next(
                (drv for drv in drives if drv.drive_index == 0),
                drives[0],
            )
            log.warning(
                "Device %r not matched in MakeMKV drive list; "
                "falling back to drive index %d",
                self.config.device,
                fallback.drive_index,
            )
            drive_info = fallback

        titles = makemkv.scan_titles(drive_info.drive_index)
        filtered = [
            title for title in titles
            if title.duration_seconds >= self.config.min_length
        ]
        if not filtered:
            raise RuntimeError(
                f"No titles found on {self.config.device!r} with duration "
                f">= {self.config.min_length} second(s). "
                "Try lowering --min-length or check the disc."
            )

        log.info(
            "Discovered %d title(s) on %s (drive index %d)",
            len(filtered),
            self.config.device,
            drive_info.drive_index,
        )
        self.disc_info = DiscInfo(
            drive=drive_info, disc_title=disc_title, titles=filtered
        )
        return self.disc_info

    # ------------------------------------------------------------------
    # Stage 2: Rip
    # ------------------------------------------------------------------

    def rip(
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> list[RipResult]:
        """Extract selected titles from disc to the temp directory.

        Requires :attr:`Pipeline.disc_info` and :attr:`Pipeline.selection` to be set (call
        :meth:`discover` first and ensure a :class:`~diskripr.models.Selection`
        has been assigned).

        Calls ``makemkvcon mkv`` for the main title and each classified extra.
        Per-title :exc:`~diskripr.drivers.base.RipError` failures are logged
        as warnings; remaining titles continue processing.

        Sets :attr:`Pipeline.rip_results` on the instance.

        Args:
            on_progress:  Optional progress callback forwarded to the driver.

        Returns:
            List of :class:`~diskripr.models.RipResult`, one per attempted
            title.

        Raises:
            RuntimeError: :attr:`Pipeline.disc_info` or :attr:`Pipeline.selection` is not set.
        """
        if self.disc_info is None:
            raise RuntimeError(
                "disc_info is not set. Call discover() before rip()."
            )
        if self.selection is None:
            raise RuntimeError(
                "selection is not set. Call run(), or call discover() and "
                "assign pipeline.selection before calling rip()."
            )

        temp_dir = make_temp_dir(self.config.temp_dir)
        makemkv = MakeMKVDriver()

        titles_to_rip: list[Title] = [self.selection.main] + [
            classified_extra.title for classified_extra in self.selection.extras
        ]

        results: list[RipResult] = []
        for title in titles_to_rip:
            try:
                result = makemkv.rip_title(
                    self.disc_info.drive.drive_index,
                    title.index,
                    temp_dir,
                    self.config.min_length,
                    on_progress,
                )
                results.append(result)
                if result.success:
                    log.info("Ripped title %d -> %s", title.index, result.output_path)
                else:
                    log.warning(
                        "Title %d rip reported failure: %s",
                        title.index,
                        result.error_message,
                    )
            except RipError as exc:
                log.warning("Title %d rip failed: %s", title.index, exc)
                results.append(
                    RipResult(
                        title_index=title.index,
                        output_path=None,
                        success=False,
                        error_message=str(exc),
                    )
                )

        self.rip_results = results
        return self.rip_results

    # ------------------------------------------------------------------
    # Stage 3: Encode (optional)
    # ------------------------------------------------------------------

    def encode(
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> list[EncodeResult]:
        """Re-encode ripped titles with HandBrakeCLI (optional stage).

        Skipped entirely when ``config.encode_format`` is ``"none"`` or when
        HandBrakeCLI is not installed.  Per-title
        :exc:`~diskripr.drivers.base.EncodeError` failures keep the original
        MKV for that title and log a warning.

        When ``config.keep_original`` is set, the original pre-encode MKV is
        moved to an ``originals/`` subdirectory of the temp dir after a
        successful encode, so :meth:`organize` can relocate it.

        Sets :attr:`Pipeline.encode_results` on the instance.

        Args:
            on_progress:  Optional progress callback forwarded to the driver.

        Returns:
            List of :class:`~diskripr.models.EncodeResult`.  An empty list
            signals that :meth:`organize` should use :attr:`Pipeline.rip_results`.
        """
        if self.config.encode_format == "none":
            log.debug("Encoding skipped (encode_format='none')")
            self.encode_results = []
            return self.encode_results

        handbrake = HandBrakeDriver()
        if not handbrake.is_available():
            log.warning(
                "HandBrakeCLI not found; encoding stage skipped. "
                "Install handbrake-cli to enable encoding."
            )
            self.encode_results = []
            return self.encode_results

        temp_dir = self.config.temp_dir / ".tmp"
        originals_dir = temp_dir / "originals"

        results: list[EncodeResult] = []
        for rip_result in self.rip_results:
            if not rip_result.success or rip_result.output_path is None:
                continue

            original_path = rip_result.output_path
            stem = original_path.stem
            encoded_path = temp_dir / f"{stem}_encoded.mkv"

            try:
                result = handbrake.encode(
                    title_index=rip_result.title_index,
                    input_path=original_path,
                    output_path=encoded_path,
                    encoder=self.config.encode_format,
                    quality=self.config.quality,
                    on_progress=on_progress,
                )
                results.append(result)
                if result.success:
                    log.info(
                        "Encoded title %d: %s -> %s (%s -> %s)",
                        rip_result.title_index,
                        original_path.name,
                        encoded_path.name,
                        format_size(result.original_size_bytes or 0),
                        format_size(result.encoded_size_bytes or 0),
                    )
                    if self.config.keep_original:
                        originals_dir.mkdir(parents=True, exist_ok=True)
                        safe_move(original_path, originals_dir / original_path.name)
                        log.debug(
                            "Preserved original: %s",
                            originals_dir / original_path.name,
                        )
                else:
                    log.warning(
                        "Title %d encode reported failure: %s; keeping original",
                        rip_result.title_index,
                        result.error_message,
                    )
            except EncodeError as exc:
                log.warning(
                    "Title %d encode error: %s; keeping original MKV",
                    rip_result.title_index,
                    exc,
                )
                results.append(
                    EncodeResult(
                        title_index=rip_result.title_index,
                        output_path=None,
                        success=False,
                        error_message=str(exc),
                    )
                )

        self.encode_results = results
        return self.encode_results

    # ------------------------------------------------------------------
    # Stage 4: Organize
    # ------------------------------------------------------------------

    def organize(self) -> list[Path]:  # pylint: disable=too-many-locals
        """Move ripped or encoded files into the Jellyfin directory structure.

        Builds the Jellyfin tree under ``config.output_dir``, resolves which
        results to use (encoded if available, ripped otherwise, with per-title
        fallback when encoding partially failed), moves the main feature and
        each classified extra, then cleans up the temp directory and
        optionally ejects the disc.

        For single-disc movies, logs a warning when the movie folder already
        contains MKV files.  For multi-disc movies (``config.disc_number`` is
        set), an existing folder is expected and no warning is emitted.

        When ``config.keep_original`` is set, pre-encode originals stored in
        ``<temp_dir>/originals/`` are relocated to ``<movie_dir>/originals/``.

        Sets :attr:`Pipeline.output_paths` on the instance.

        Returns:
            List of :class:`pathlib.Path` for each file placed in the
            Jellyfin library tree.

        Raises:
            RuntimeError: :attr:`Pipeline.selection` is not set.
        """
        if self.selection is None:
            raise RuntimeError(
                "selection is not set. Call run(), or assign pipeline.selection "
                "before calling organize()."
            )

        effective = self._effective_results()

        safe_name = sanitize_filename(self.config.movie_name)
        movie_dir_path = (
            self.config.output_dir / "Movies" / f"{safe_name} ({self.config.movie_year})"
        )
        existing_mkvs = (
            list(movie_dir_path.glob("*.mkv")) if movie_dir_path.exists() else []
        )

        movie_dir, extras_dir = build_jellyfin_tree(
            self.config.output_dir, self.config.movie_name, self.config.movie_year
        )

        if self.config.disc_number is None and existing_mkvs:
            log.warning(
                "Output directory already contains files for single-disc movie: %s. "
                "Files will be added alongside existing content.",
                movie_dir,
            )

        path_map: dict[int, Path] = {
            result.title_index: result.output_path
            for result in effective
            if result.success and result.output_path is not None
        }

        output_paths: list[Path] = []

        main_source = path_map.get(self.selection.main.index)
        if main_source is not None:
            main_dest = movie_dir / build_main_feature_filename(
                self.config.movie_name,
                self.config.movie_year,
                self.config.disc_number,
            )
            safe_move(main_source, main_dest)
            output_paths.append(main_dest)
            log.info("Organized main feature: %s", main_dest)
        else:
            log.warning(
                "Main title %d has no successful result; main feature not organized",
                self.selection.main.index,
            )

        for classified_extra in self.selection.extras:
            extra_source = path_map.get(classified_extra.title.index)
            if extra_source is None:
                log.warning(
                    "Extra title %d (%s) has no successful result; skipping",
                    classified_extra.title.index,
                    classified_extra.output_filename,
                )
                continue
            extra_dest = extras_dir / classified_extra.output_filename
            safe_move(extra_source, extra_dest)
            output_paths.append(extra_dest)
            log.info("Organized extra: %s", extra_dest)

        if self.config.keep_original:
            originals_src = self.config.temp_dir / ".tmp" / "originals"
            if originals_src.is_dir():
                originals_dest = movie_dir / "originals"
                originals_dest.mkdir(parents=True, exist_ok=True)
                for original_file in originals_src.iterdir():
                    safe_move(original_file, originals_dest / original_file.name)
                    log.debug(
                        "Moved original: %s", originals_dest / original_file.name
                    )

        cleanup(self.config.temp_dir / ".tmp")

        if self.config.eject_on_complete:
            eject_disc(self.config.device)

        self.output_paths = output_paths
        return self.output_paths

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------

    def run(
        self,
        on_progress: Optional[ProgressCallback] = None,
    ) -> list[Path]:
        """Run the full pipeline from disc discovery to Jellyfin organization.

        Chains all stages: discover → select → classify → rip → encode →
        organize, followed by a non-fatal stream inspection pass.

        Only handles resolved ``rip_mode`` values (``"main"`` or ``"all"``)
        and ``encode_format`` values (``"h264"``, ``"h265"``, or ``"none"``).
        The CLI must resolve ``"ask"`` interactively and assign the resulting
        :class:`~diskripr.models.Selection` to :attr:`Pipeline.selection` before
        calling :meth:`rip` if ask-mode is needed.

        Args:
            on_progress:  Optional progress callback for long-running stages.

        Returns:
            List of :class:`pathlib.Path` for every file placed in the
            Jellyfin library tree.

        Raises:
            RuntimeError: Device not found, no accessible drive, or no titles
                          survive ``min_length`` filtering.
        """
        self.discover(on_progress)

        safe_name = sanitize_filename(self.config.movie_name)
        extras_dir = (
            self.config.output_dir
            / "Movies"
            / f"{safe_name} ({self.config.movie_year})"
            / "extras"
        )
        main_title, extra_titles = _select(self.disc_info.titles, self.config.rip_mode)  # type: ignore[union-attr]
        self.selection = _classify(main_title, extra_titles, extras_dir)

        log.info(
            "Selected: main=%r, extras=%d",
            self.selection.main.name,
            len(self.selection.extras),
        )

        self.rip(on_progress)
        self.encode(on_progress)
        self.organize()
        _inspect(self.output_paths)
        return self.output_paths

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _effective_results(self) -> list[Union[RipResult, EncodeResult]]:
        """Resolve the final set of results to use for organization.

        Prefers encode results when available, falling back per-title to rip
        results when encoding was skipped or failed for that title.
        """
        if not self.encode_results:
            return list(self.rip_results)

        enc_by_idx = {
            enc.title_index: enc
            for enc in self.encode_results
            if enc.success
        }
        effective: list[Union[RipResult, EncodeResult]] = []
        for rip_res in self.rip_results:
            enc_res = enc_by_idx.get(rip_res.title_index)
            effective.append(enc_res if enc_res is not None else rip_res)
        return effective
