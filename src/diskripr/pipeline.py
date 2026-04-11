# pylint: disable=too-many-lines,duplicate-code
"""Pipeline orchestrators for the diskripr DVD ripping workflow.

This module exposes three classes that share a common abstract base:

- :class:`BasePipeline` — shared ``discover()``, ``rip()``, and ``encode()``
  stages plus the ``_build_signals_map()`` helper that assembles
  :class:`~diskripr.util.heuristics.TitleSignals` from lsdvd and IFO data.

- :class:`MoviePipeline` — extends ``BasePipeline`` with movie-specific title
  selection (longest title = main feature), heuristic extra classification, and
  Jellyfin movie directory organization.

- :class:`ShowPipeline` — extends ``BasePipeline`` with episode clustering via
  :func:`~diskripr.util.heuristics.cluster_episodes`, sequential episode
  numbering, and Jellyfin TV directory organization.

Stage results are stored as public instance attributes so the caller can read
intermediate state or inject values between stages (e.g. a pre-built
:class:`~diskripr.models.Selection` from an interactive CLI prompt):

.. code-block:: python

    pipeline = MoviePipeline(config)
    pipeline.discover()            # sets pipeline.disc_info

    # For ask-mode: build selection interactively from pipeline.disc_info.titles
    # and assign it before calling rip:
    pipeline.selection = my_selection

    pipeline.rip()                 # sets pipeline.rip_results
    pipeline.encode()              # sets pipeline.encode_results
    pipeline.organize()            # sets pipeline.output_paths

Or run the full chain at once:

.. code-block:: python

    output_paths = MoviePipeline(config).run()

Base stage state attributes (all pipelines):

- ``disc_info``      — set by :meth:`~BasePipeline.discover`
- ``lsdvd_disc``     — lsdvd result stored during :meth:`~BasePipeline.discover`
- ``ifo_map``        — IFO parse result stored during :meth:`~BasePipeline.discover`
- ``rip_results``    — set by :meth:`~BasePipeline.rip`
- ``encode_results`` — set by :meth:`~BasePipeline.encode`

Each subclass also stores:

- ``selection``      — movie :class:`~diskripr.models.Selection` or show
                       :class:`~diskripr.models.ShowSelection`; set by
                       :meth:`~MoviePipeline.run` / :meth:`~ShowPipeline.run`,
                       or injected externally.
- ``output_paths``   — set by :meth:`~MoviePipeline.organize` /
                       :meth:`~ShowPipeline.organize`

The pipelines only handle resolved ``rip_mode`` values (``"main"`` or
``"all"``) and ``encode_format`` values (``"h264"``, ``"h265"``, or
``"none"``).  The CLI is responsible for resolving ``"ask"`` interactively.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Union

from diskripr.config import BaseConfig, MovieConfig, ShowConfig
from diskripr.drivers.base import EncodeError, RipError, ToolNotFound
from diskripr.drivers.ffprobe import FfprobeDriver
from diskripr.drivers.handbrake import HandBrakeDriver
from diskripr.drivers.ifo import IfoDriver, IfoVts
from diskripr.drivers.lsdvd import LsdvdDisc, LsdvdDriver, LsdvdTitle
from diskripr.drivers.makemkv import MakeMKVDriver
from diskripr.models import (
    ClassifiedExtra,
    DiscInfo,
    EncodeResult,
    EpisodeEntry,
    JellyfinExtraType,
    RipResult,
    Selection,
    ShowSelection,
    Title,
)
from diskripr.util.heuristics import (
    TitleSignals,
    classify_extra,
    cluster_episodes,
)
from diskripr.util.jellyfin_filesystem import (
    build_episode_filename,
    build_extra_filename,
    build_jellyfin_tree,
    build_main_feature_filename,
    build_tv_tree,
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

# Maximum duration difference (seconds) for matching a MakeMKV title to its
# corresponding lsdvd title or IFO PGC.
_DURATION_MATCH_TOLERANCE = 5


# ---------------------------------------------------------------------------
# Module-level pure helpers (no I/O, no external tools)
# ---------------------------------------------------------------------------

def _parse_duration_seconds(duration: str) -> int:
    """Parse a ``HH:MM:SS`` duration string to total seconds.

    Args:
        duration: Duration string in ``HH:MM:SS`` format.

    Returns:
        Total duration in integer seconds.
    """
    parts = duration.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])


def _find_video_ts_path(device: str) -> Optional[Path]:
    """Inspect ``/proc/mounts`` to locate the VIDEO_TS directory for *device*.

    Reads ``/proc/mounts`` line by line; the first entry whose device column
    matches *device* is checked for a ``VIDEO_TS/`` subdirectory.

    Args:
        device: Block device path (e.g. ``"/dev/sr0"``).

    Returns:
        :class:`~pathlib.Path` to the ``VIDEO_TS`` directory on success,
        or ``None`` if the device is not mounted or has no ``VIDEO_TS`` dir.
    """
    try:
        mounts_text = Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("Could not read /proc/mounts: %s", exc)
        return None

    for line in mounts_text.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == device:
            video_ts = Path(parts[1]) / "VIDEO_TS"
            if video_ts.is_dir():
                return video_ts
    return None


def _assemble_signals(
    title: Title,
    lsdvd_title: Optional[LsdvdTitle],
    ifo_vts: Optional[IfoVts],
    reference_vts: Optional[int],
) -> TitleSignals:
    """Assemble a :class:`~diskripr.util.heuristics.TitleSignals` for *title*.

    Joins MakeMKV ``Title``, ``LsdvdTitle``, and ``IfoVts`` data into the
    unified signal bundle used by heuristic classification.  All three sources
    are optional — missing data leaves the corresponding signal fields as
    ``None``.

    For ``cell_durations``, the PGC in *ifo_vts* whose total duration is
    nearest to ``title.duration_seconds`` (within
    :data:`_DURATION_MATCH_TOLERANCE` seconds) is selected.

    Args:
        title:         MakeMKV title metadata.
        lsdvd_title:   Matching lsdvd title, or ``None`` if unavailable.
        ifo_vts:       IFO VTS for this title's VTS, or ``None``.
        reference_vts: VTS number of the primary content (from lsdvd
                       ``main_vts``), or ``None``.

    Returns:
        Populated :class:`~diskripr.util.heuristics.TitleSignals`.
    """
    signals = TitleSignals(
        title=title,
        segment_count=title.segment_count,
        segments_map=title.segments_map,
        reference_vts=reference_vts,
    )

    if lsdvd_title is not None:
        signals.vts_number = lsdvd_title.vts_number
        signals.ttn = lsdvd_title.ttn
        signals.audio_stream_count = lsdvd_title.audio_stream_count
        signals.cell_count = lsdvd_title.cell_count

    if ifo_vts is not None:
        signals.pgc_count_in_vts = ifo_vts.pgc_count
        best_pgc = None
        best_diff: float = float("inf")
        for pgc in ifo_vts.pgcs:
            diff = abs(pgc.duration_seconds - title.duration_seconds)
            if diff < best_diff:
                best_diff = diff
                best_pgc = pgc
        if best_pgc is not None and best_diff <= _DURATION_MATCH_TOLERANCE:
            signals.cell_durations = list(best_pgc.cell_durations)

    return signals


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
    movie_dir: Path,
    signals_map: Optional[dict[int, TitleSignals]] = None,
) -> Selection:
    """Assign Jellyfin extra types and output filenames to each extra title.

    Uses :func:`~diskripr.util.heuristics.classify_extra` on the signals for
    each extra title when *signals_map* is provided.  When *signals_map* is
    ``None`` or a title has no entry, heuristics fall through to rule 13
    (fallback ``"extra"`` type with low confidence).

    Counters continue from the highest existing counter in *movie_dir* so
    multi-disc runs do not collide.

    Args:
        main:         The main feature title.
        extras:       Extra titles to classify.
        movie_dir:    The Jellyfin movie directory (used to scan existing extras).
        signals_map:  Optional per-title signal bundles; ``None`` means no
                      enriched signal data is available.

    Returns:
        :class:`~diskripr.models.Selection` with a fully-populated extras list.
    """
    if signals_map is None:
        signals_map = {}
    counters = scan_existing_extras(movie_dir)
    classified: list[ClassifiedExtra] = []
    for extra in extras:
        signals = signals_map.get(extra.index, TitleSignals(title=extra))
        result = classify_extra(signals)
        extra_type = result.extra_type
        current = counters.get(extra_type, 0) + 1
        counters[extra_type] = current
        filename = build_extra_filename(
            extra_type, current, title_name=result.display_name
        )
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
# BasePipeline
# ---------------------------------------------------------------------------

class BasePipeline:
    """Abstract base for shared disc discovery, ripping, and encoding stages.

    Instantiate a concrete subclass (:class:`MoviePipeline` or
    :class:`ShowPipeline`) rather than this class directly.
    """

    def __init__(self, config: BaseConfig) -> None:
        self.config = config
        self.disc_info: Optional[DiscInfo] = None
        self.lsdvd_disc: Optional[LsdvdDisc] = None
        self.ifo_map: Optional[dict[int, IfoVts]] = None
        self.rip_results: list[RipResult] = []
        self.encode_results: list[EncodeResult] = []

    def _titles_for_rip(self) -> list[Title]:
        """Return ordered list of titles to pass to the rip stage.

        Subclasses must override this method.  Raise ``RuntimeError`` when the
        required ``selection`` attribute has not yet been set.
        """
        raise NotImplementedError  # pragma: no cover

    def _build_signals_map(
        self,
        titles: list[Title],
    ) -> dict[int, TitleSignals]:
        """Build :class:`~diskripr.util.heuristics.TitleSignals` for each title.

        Matches each MakeMKV title to the closest lsdvd title by duration
        (within :data:`_DURATION_MATCH_TOLERANCE` seconds, exclusive — each
        lsdvd title is used at most once), then looks up the IFO VTS for the
        matched lsdvd VTS number.  Unmatched titles receive signals with all
        optional fields as ``None``.

        Args:
            titles: MakeMKV titles to enrich (typically from
                :attr:`disc_info.titles`).

        Returns:
            Dict mapping title index to :class:`TitleSignals`.
        """
        lsdvd_titles: list[LsdvdTitle] = (
            list(self.lsdvd_disc.titles) if self.lsdvd_disc is not None else []
        )
        reference_vts: Optional[int] = (
            self.lsdvd_disc.main_vts if self.lsdvd_disc is not None else None
        )

        signals_map: dict[int, TitleSignals] = {}
        used_lsdvd_indices: set[int] = set()

        for title in titles:
            best_lt: Optional[LsdvdTitle] = None
            best_diff: float = float("inf")
            best_idx: int = -1
            for idx, lt_candidate in enumerate(lsdvd_titles):
                if idx in used_lsdvd_indices:
                    continue
                diff = abs(
                    _parse_duration_seconds(lt_candidate.duration)
                    - title.duration_seconds
                )
                if diff < best_diff:
                    best_diff = diff
                    best_lt = lt_candidate
                    best_idx = idx

            lsdvd_title: Optional[LsdvdTitle] = None
            if best_lt is not None and best_diff <= _DURATION_MATCH_TOLERANCE:
                used_lsdvd_indices.add(best_idx)
                lsdvd_title = best_lt

            ifo_vts: Optional[IfoVts] = None
            if self.ifo_map is not None and lsdvd_title is not None:
                ifo_vts = self.ifo_map.get(lsdvd_title.vts_number)

            signals_map[title.index] = _assemble_signals(
                title, lsdvd_title, ifo_vts, reference_vts
            )

        return signals_map

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
        2. Run ``lsdvd`` for a quick disc title pre-check (non-fatal); store
           the full :class:`~diskripr.drivers.lsdvd.LsdvdDisc` on
           :attr:`lsdvd_disc` for signal assembly.
        3. Scan drives with MakeMKV; match the configured device path to a
           drive index.  Falls back to drive index 0 if no match is found.
        4. Scan all titles on the matched drive.
        5. Filter titles shorter than ``config.min_length``.
        6. Attempt VIDEO_TS mount lookup via ``/proc/mounts`` and run
           :class:`~diskripr.drivers.ifo.IfoDriver`; store result on
           :attr:`ifo_map` (``None`` when unavailable).
        7. Fail with a diagnostic message if no titles remain after filtering.

        Sets :attr:`disc_info`, :attr:`lsdvd_disc`, and :attr:`ifo_map`.

        Args:
            on_progress:  Reserved for future use; not used by this stage.

        Returns:
            The populated :class:`~diskripr.models.DiscInfo`.

        Raises:
            RuntimeError: Device not found, no accessible MakeMKV drive, or
                          no titles remain after ``min_length`` filtering.
        """
        log.info(
            "Discover: device=%s  min_length=%ds",
            self.config.device,
            self.config.min_length,
        )
        if not Path(self.config.device).exists():
            raise RuntimeError(
                f"Device not found: {self.config.device!r}. "
                "Run 'lsblk' to list available block devices."
            )

        disc_title = ""
        lsdvd = LsdvdDriver()
        try:
            lsdvd_disc = lsdvd.read_disc(self.config.device)
            if lsdvd_disc is not None:
                disc_title = lsdvd_disc.disc_title
                log.info("lsdvd disc title: %s", disc_title)
                self.lsdvd_disc = lsdvd_disc
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
        log.info(
            "Title filter: %d of %d title(s) meet min_length=%ds",
            len(filtered),
            len(titles),
            self.config.min_length,
        )
        if not filtered:
            raise RuntimeError(
                f"No titles found on {self.config.device!r} with duration "
                f">= {self.config.min_length} second(s). "
                "Try lowering --min-length or check the disc."
            )

        # Attempt IFO parse via /proc/mounts VIDEO_TS lookup.
        video_ts_path = _find_video_ts_path(self.config.device)
        if video_ts_path is not None:
            self.ifo_map = IfoDriver().read_disc(video_ts_path)
            if self.ifo_map is not None:
                log.debug(
                    "IfoDriver: read %d VTS entries from %s",
                    len(self.ifo_map),
                    video_ts_path,
                )
            else:
                log.debug("IfoDriver: no VTS data read from %s", video_ts_path)
        else:
            log.warning(
                "Could not locate VIDEO_TS mount for device %s; "
                "IFO cell-duration signals will be unavailable",
                self.config.device,
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

        Requires :attr:`disc_info` to be set (call :meth:`discover` first)
        and the subclass ``selection`` to be set so that
        :meth:`_titles_for_rip` can return the list of titles to extract.

        Calls ``makemkvcon mkv`` for each title returned by
        :meth:`_titles_for_rip`.  Per-title
        :exc:`~diskripr.drivers.base.RipError` failures are logged as
        warnings; remaining titles continue processing.

        Sets :attr:`rip_results` on the instance.

        Args:
            on_progress:  Optional progress callback forwarded to the driver.

        Returns:
            List of :class:`~diskripr.models.RipResult`, one per attempted
            title.

        Raises:
            RuntimeError: :attr:`disc_info` is not set, or the subclass
                          selection has not been assigned.
        """
        if self.disc_info is None:
            raise RuntimeError(
                "disc_info is not set. Call discover() before rip()."
            )

        titles_to_rip = self._titles_for_rip()

        log.info(
            "Rip: %d title(s) selected -> %s",
            len(titles_to_rip),
            self.config.temp_dir,
        )
        temp_dir = make_temp_dir(self.config.temp_dir)
        makemkv = MakeMKVDriver()

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
        successful encode, so the ``organize()`` stage can relocate it.

        Sets :attr:`encode_results` on the instance.

        Args:
            on_progress:  Optional progress callback forwarded to the driver.

        Returns:
            List of :class:`~diskripr.models.EncodeResult`.  An empty list
            signals that the ``organize()`` stage should use
            :attr:`rip_results`.
        """
        log.info("Encode: format=%s", self.config.encode_format)
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


# ---------------------------------------------------------------------------
# MoviePipeline
# ---------------------------------------------------------------------------

class MoviePipeline(BasePipeline):
    """Pipeline for ripping a single DVD movie into a Jellyfin movie library.

    Extends :class:`BasePipeline` with:

    - Movie-specific title selection (longest title = main feature).
    - Heuristic extra type classification via
      :func:`~diskripr.util.heuristics.classify_extra`.
    - Jellyfin movie directory organisation.

    The :attr:`selection` attribute holds the :class:`~diskripr.models.Selection`
    built during :meth:`run` (or injected externally for ask-mode CLI flows).
    """

    def __init__(self, config: MovieConfig) -> None:
        super().__init__(config)
        self.config: MovieConfig = config
        self.selection: Optional[Selection] = None
        self.output_paths: list[Path] = []

    def _titles_for_rip(self) -> list[Title]:
        if self.selection is None:
            raise RuntimeError(
                "selection is not set. Call run(), or call discover() and "
                "assign pipeline.selection before calling rip()."
            )
        return [self.selection.main] + [
            classified_extra.title
            for classified_extra in self.selection.extras
        ]

    # ------------------------------------------------------------------
    # Stage 4: Organize
    # ------------------------------------------------------------------

    def organize(self) -> list[Path]:  # pylint: disable=too-many-locals
        """Move ripped or encoded files into the Jellyfin movie directory tree.

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

        Sets :attr:`output_paths` on the instance.

        Returns:
            List of :class:`pathlib.Path` for each file placed in the
            Jellyfin library tree.

        Raises:
            RuntimeError: :attr:`selection` is not set.
        """
        if self.selection is None:
            raise RuntimeError(
                "selection is not set. Call run(), or assign pipeline.selection "
                "before calling organize()."
            )

        log.info(
            "Organize: %d successful rip result(s) -> %s",
            sum(1 for res in self.rip_results if res.success),
            self.config.output_dir,
        )
        effective = self._effective_results()

        movie_dir, extras_type_dirs = build_jellyfin_tree(
            self.config.output_dir, self.config.movie_name, self.config.movie_year
        )
        existing_mkvs = list(movie_dir.glob("*.mkv"))

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
            extra_dest = (
                extras_type_dirs[classified_extra.extra_type]
                / classified_extra.output_filename
            )
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
        """Run the full movie pipeline from disc discovery to Jellyfin organization.

        Chains all stages: discover → build signals → select → classify → rip
        → encode → organize, followed by a non-fatal stream inspection pass.

        Only handles resolved ``rip_mode`` values (``"main"`` or ``"all"``).
        The CLI must resolve ``"ask"`` interactively and assign the resulting
        :class:`~diskripr.models.Selection` to :attr:`selection` before
        calling :meth:`rip` if ask-mode is needed.

        Args:
            on_progress:  Optional progress callback for long-running stages.

        Returns:
            List of :class:`pathlib.Path` for every file placed in the
            Jellyfin library tree.
        """
        self.discover(on_progress)

        safe_name = sanitize_filename(self.config.movie_name)
        movie_dir = (
            self.config.output_dir
            / "movies"
            / f"{safe_name} ({self.config.movie_year})"
        )

        signals_map = self._build_signals_map(self.disc_info.titles)  # type: ignore[union-attr]
        main_title, extra_titles = _select(
            self.disc_info.titles, self.config.rip_mode  # type: ignore[union-attr]
        )
        self.selection = _classify(main_title, extra_titles, movie_dir, signals_map)

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


# ---------------------------------------------------------------------------
# ShowPipeline
# ---------------------------------------------------------------------------

class ShowPipeline(BasePipeline):
    """Pipeline for ripping a TV season disc into a Jellyfin TV library.

    Extends :class:`BasePipeline` with:

    - Episode clustering via
      :func:`~diskripr.util.heuristics.cluster_episodes`.
    - Sequential episode numbering from ``config.start_episode``.
    - Heuristic extra classification for non-episode titles.
    - Jellyfin TV directory organisation via
      :func:`~diskripr.util.jellyfin_filesystem.build_tv_tree`.

    The :attr:`selection` attribute holds the
    :class:`~diskripr.models.ShowSelection` built during :meth:`run` (or
    injected externally for ask-mode CLI flows).
    """

    def __init__(self, config: ShowConfig) -> None:
        super().__init__(config)
        self.config: ShowConfig = config
        self.selection: Optional[ShowSelection] = None
        self.output_paths: list[Path] = []

    def _titles_for_rip(self) -> list[Title]:
        if self.selection is None:
            raise RuntimeError(
                "selection is not set. Call run(), or call discover() and "
                "assign pipeline.selection before calling rip()."
            )
        episode_titles = [entry.title for entry in self.selection.episodes]
        extra_titles = [
            classified.title for classified in self.selection.extras
        ]
        return episode_titles + extra_titles

    def _build_show_selection(
        self,
        signals_map: dict[int, TitleSignals],
    ) -> ShowSelection:
        """Cluster titles into episodes and extras, then classify each.

        Uses :func:`~diskripr.util.heuristics.cluster_episodes` to partition
        titles by duration.  Episodes are numbered from
        ``config.start_episode``; extras are classified via
        :func:`~diskripr.util.heuristics.classify_extra`.

        Args:
            signals_map: Per-title signal bundles from :meth:`_build_signals_map`.

        Returns:
            Populated :class:`~diskripr.models.ShowSelection`.
        """
        assert self.disc_info is not None
        episodes_raw, extra_titles = cluster_episodes(
            self.disc_info.titles, signals_map
        )

        episode_entries: list[EpisodeEntry] = [
            EpisodeEntry(
                title=title,
                season_number=self.config.season_number,
                episode_number=self.config.start_episode + idx,
            )
            for idx, title in enumerate(episodes_raw)
        ]

        counters: dict[JellyfinExtraType, int] = {}
        classified_extras: list[ClassifiedExtra] = []
        for extra in extra_titles:
            sig = signals_map.get(extra.index, TitleSignals(title=extra))
            result = classify_extra(sig)
            extra_type = result.extra_type
            counter = counters.get(extra_type, 0) + 1
            counters[extra_type] = counter
            filename = build_extra_filename(
                extra_type, counter, title_name=result.display_name
            )
            classified_extras.append(
                ClassifiedExtra(
                    title=extra,
                    extra_type=extra_type,
                    output_filename=filename,
                )
            )

        return ShowSelection(episodes=episode_entries, extras=classified_extras)

    # ------------------------------------------------------------------
    # Stage 4: Organize
    # ------------------------------------------------------------------

    def organize(self) -> list[Path]:  # pylint: disable=too-many-locals
        """Move ripped or encoded files into the Jellyfin TV directory tree.

        Builds the season directory tree under ``config.output_dir`` via
        :func:`~diskripr.util.jellyfin_filesystem.build_tv_tree`.  Each
        episode is placed in the season directory with a Jellyfin-compatible
        filename; extras are routed to their type subdirectory.

        Sets :attr:`output_paths` on the instance.

        Returns:
            List of :class:`pathlib.Path` for each file placed in the
            Jellyfin library tree.

        Raises:
            RuntimeError: :attr:`selection` is not set.
        """
        if self.selection is None:
            raise RuntimeError(
                "selection is not set. Call run(), or assign pipeline.selection "
                "before calling organize()."
            )

        log.info(
            "Organize: %d successful rip result(s) -> %s",
            sum(1 for res in self.rip_results if res.success),
            self.config.output_dir,
        )
        effective = self._effective_results()

        season_dir, extras_type_dirs = build_tv_tree(
            self.config.output_dir,
            self.config.show_name,
            self.config.season_number,
        )

        path_map: dict[int, Path] = {
            result.title_index: result.output_path
            for result in effective
            if result.success and result.output_path is not None
        }

        output_paths: list[Path] = []

        for entry in self.selection.episodes:
            episode_source = path_map.get(entry.title.index)
            if episode_source is None:
                log.warning(
                    "Episode title %d has no successful result; skipping",
                    entry.title.index,
                )
                continue
            episode_filename = build_episode_filename(
                self.config.show_name,
                entry.season_number,
                entry.episode_number,
                entry.episode_title,
            )
            episode_dest = season_dir / episode_filename
            safe_move(episode_source, episode_dest)
            output_paths.append(episode_dest)
            log.info(
                "Organized episode S%02dE%02d: %s",
                entry.season_number,
                entry.episode_number,
                episode_dest,
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
            extra_dest = (
                extras_type_dirs[classified_extra.extra_type]
                / classified_extra.output_filename
            )
            safe_move(extra_source, extra_dest)
            output_paths.append(extra_dest)
            log.info("Organized extra: %s", extra_dest)

        if self.config.keep_original:
            originals_src = self.config.temp_dir / ".tmp" / "originals"
            if originals_src.is_dir():
                originals_dest = season_dir / "originals"
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
        """Run the full show pipeline from disc discovery to Jellyfin organization.

        Chains all stages: discover → build signals → cluster episodes →
        classify → rip → encode → organize, followed by a non-fatal stream
        inspection pass.

        Args:
            on_progress:  Optional progress callback for long-running stages.

        Returns:
            List of :class:`pathlib.Path` for every file placed in the
            Jellyfin library tree.
        """
        self.discover(on_progress)

        signals_map = self._build_signals_map(self.disc_info.titles)  # type: ignore[union-attr]
        self.selection = self._build_show_selection(signals_map)

        log.info(
            "ShowPipeline: %d episode(s), %d extra(s)",
            len(self.selection.episodes),
            len(self.selection.extras),
        )

        self.rip(on_progress)
        self.encode(on_progress)
        self.organize()
        _inspect(self.output_paths)
        return self.output_paths
