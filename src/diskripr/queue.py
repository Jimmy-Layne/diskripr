"""Batch queue runner and job file validation for ``diskripr queue``.

Provides:

- ``validate_job_file(path)``   — validates an entire job file before any disc
  operation begins; returns a list of human-readable error strings.  An empty
  list means the file is valid.
- ``wait_for_disc_removed(device, ...)`` — polls until the drive reports no
  disc present; raises :exc:`TimeoutError` if the timeout elapses.
- ``wait_for_disc_inserted(device, ...)`` — polls until the drive reports a
  disc present; raises :exc:`TimeoutError` if the timeout elapses.
- ``QueueRunner``               — runs a validated :class:`~diskripr.schema.JobFile`
  sequentially, resolving per-job options and handling disc swaps between jobs.

Disc-state detection
--------------------
Drive state is probed by reading the ``/dev/sr*`` block device with
``dd if=<device> count=0`` and checking the return code, **or** — preferred
when available — by running ``udevadm info --query=property --name=<device>``
and checking the ``ID_CDROM_MEDIA`` property.  The implementation uses a
dedicated :func:`_disc_present` helper that can be injected during tests.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import ValidationError

from diskripr.config import MovieConfig, ShowConfig
from diskripr.pipeline import MoviePipeline, ShowPipeline
from diskripr.schema import JobFile, JobOptions, MovieJob, ShowJob

_LOG = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Disc-swap timing constants
# ---------------------------------------------------------------------------

_DEFAULT_POLL_INTERVAL: int = 5     # seconds between each device probe
_DEFAULT_TIMEOUT: int = 1800        # 30 minutes total wait (removed + inserted)


# ---------------------------------------------------------------------------
# Error formatting helpers
# ---------------------------------------------------------------------------

# Literal values used as union discriminators; pydantic v2 inserts these as
# an extra path segment in error locations for discriminated unions.
_UNION_DISCRIMINATOR_TAGS = frozenset({"movie", "show"})


def _loc_to_field(loc: tuple[str | int, ...]) -> str:
    """Convert a pydantic error location tuple to a dotted field string.

    Pydantic v2 location tuples for discriminated unions look like::

        ('jobs', 0, 'movie', 'movie', 'name')   # union tag + field path
        ('jobs', 1, 'show',  'show',  'season')
        ('version',)

    The ``jobs`` prefix, the integer job index, and the union variant tag
    (e.g. the leading ``'movie'`` for a ``MovieJob`` error) are all stripped
    because :func:`validate_job_file` already prefixes the job index in the
    ``Error in jobs[N]:`` message, and the tag adds no user-visible meaning.

    Examples::

        ('jobs', 0, 'movie', 'movie', 'year')  -> 'movie.year'
        ('jobs', 1, 'show',  'show',  'season')-> 'show.season'
        ('jobs', 2)                            -> ''   (top-level job error)
        ('version',)                           -> 'version'
    """
    # Strip ('jobs', <int>) prefix for per-job errors.
    if loc and loc[0] == "jobs" and len(loc) >= 2 and isinstance(loc[1], int):
        remainder = loc[2:]
    else:
        remainder = loc

    if not remainder:
        return ""

    # Pydantic v2 inserts the matched variant tag as the first segment after
    # the job index for discriminated unions.  Strip it when present.
    if remainder and remainder[0] in _UNION_DISCRIMINATOR_TAGS:
        remainder = remainder[1:]

    if not remainder:
        return ""

    # Build a dotted path; integer segments become bracket notation.
    parts = []
    for segment in remainder:
        if isinstance(segment, int):
            parts.append(f"[{segment}]")
        else:
            parts.append(str(segment))

    return ".".join(parts).replace(".[", "[")


def _format_error(job_index: int | None, field_path: str, message: str) -> str:
    """Return a single human-readable error string.

    Format::

        Error in jobs[N]: "field.path" <message>
        Error in file: "field" <message>      (for top-level envelope errors)
    """
    if job_index is not None:
        prefix = f"Error in jobs[{job_index}]"
    else:
        prefix = "Error in file"

    if field_path:
        return f'{prefix}: "{field_path}" {message}'
    return f"{prefix}: {message}"


def _pydantic_errors_to_strings(exc: ValidationError) -> list[str]:
    """Convert a ``ValidationError`` into ``validate_job_file`` error strings."""
    messages: list[str] = []
    for error in exc.errors():
        loc: tuple[str | int, ...] = error["loc"]
        raw_message: str = error["msg"]

        # Determine the job index, if this error is inside the jobs array.
        job_index: int | None = None
        if loc and loc[0] == "jobs" and len(loc) >= 2 and isinstance(loc[1], int):
            job_index = loc[1]

        field_path = _loc_to_field(loc)
        messages.append(_format_error(job_index, field_path, raw_message))

    return messages


# ---------------------------------------------------------------------------
# Public validation API
# ---------------------------------------------------------------------------

def validate_job_file(path: Path) -> list[str]:
    """Validate the job file at *path* and return human-readable error strings.

    The entire file is validated before any disc operation begins.  An empty
    return list means the file is structurally valid and ready for
    ``QueueRunner``.

    Errors are formatted as::

        Error in jobs[N]: "field.path" <message>

    Args:
        path: Path to the JSON job file.

    Returns:
        A list of error strings (empty if the file is valid).
    """
    raw: Any
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return [f"Could not read job file: {exc}"]
    except json.JSONDecodeError as exc:
        return [f"Job file is not valid JSON: {exc}"]

    try:
        JobFile.model_validate(raw)
    except ValidationError as exc:
        return _pydantic_errors_to_strings(exc)

    return []


# ---------------------------------------------------------------------------
# Disc-state detection
# ---------------------------------------------------------------------------

def _disc_present_udevadm(device: str) -> bool:
    """Return ``True`` if *device* currently has a disc present.

    Runs ``udevadm info --query=property --name=<device>`` and checks for the
    ``ID_CDROM_MEDIA=1`` property.  Falls back to ``False`` on any error so
    callers treat an inaccessible drive as "no disc".
    """
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", f"--name={device}"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return "ID_CDROM_MEDIA=1" in result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


# The default disc-presence probe.  Tests replace this with a callable mock.
_DEFAULT_DISC_PROBE: Callable[[str], bool] = _disc_present_udevadm  # pylint: disable=invalid-name


# ---------------------------------------------------------------------------
# Disc swap polling (8.3)
# ---------------------------------------------------------------------------

def wait_for_disc_removed(
    device: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
    *,
    disc_probe: Callable[[str], bool] = _DEFAULT_DISC_PROBE,
) -> None:
    """Block until *device* reports no disc present.

    Polls *device* every *poll_interval* seconds.  Raises :exc:`TimeoutError`
    if the drive still has a disc after *timeout_seconds* have elapsed.

    Args:
        device:          Block device path (e.g. ``"/dev/sr0"``).
        timeout_seconds: Maximum wait in seconds (default 1800 = 30 min).
        poll_interval:   Seconds between each probe (default 5).
        disc_probe:      Callable ``(device) -> bool``; injectable for testing.

    Raises:
        TimeoutError: Disc was not removed within *timeout_seconds*.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        if not disc_probe(device):
            _LOG.info("Disc removed from %s.", device)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for disc to be removed from {device} "
                f"after {timeout_seconds}s."
            )
        _LOG.debug("Disc still present in %s; retrying in %ds.", device, poll_interval)
        time.sleep(poll_interval)


def wait_for_disc_inserted(
    device: str,
    timeout_seconds: int = _DEFAULT_TIMEOUT,
    poll_interval: int = _DEFAULT_POLL_INTERVAL,
    *,
    disc_probe: Callable[[str], bool] = _DEFAULT_DISC_PROBE,
) -> None:
    """Block until *device* reports a disc present.

    Polls *device* every *poll_interval* seconds.  Raises :exc:`TimeoutError`
    if no disc is detected within *timeout_seconds*.

    Args:
        device:          Block device path (e.g. ``"/dev/sr0"``).
        timeout_seconds: Maximum wait in seconds (default 1800 = 30 min).
        poll_interval:   Seconds between each probe (default 5).
        disc_probe:      Callable ``(device) -> bool``; injectable for testing.

    Raises:
        TimeoutError: No disc was inserted within *timeout_seconds*.
    """
    deadline = time.monotonic() + timeout_seconds
    while True:
        if disc_probe(device):
            _LOG.info("Disc detected in %s.", device)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Timed out waiting for disc to be inserted into {device} "
                f"after {timeout_seconds}s."
            )
        _LOG.debug("No disc in %s; retrying in %ds.", device, poll_interval)
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Option resolution
# ---------------------------------------------------------------------------

#: Built-in defaults for all ``BaseConfig`` fields that appear in
#: ``JobOptions``.  These match the ``BaseConfig`` field defaults so that
#: the queue runner produces the same behaviour as the CLI when no overrides
#: are supplied.
_BUILTIN_DEFAULTS: dict[str, Any] = {
    "device": "/dev/sr0",
    "output_dir": "dvd_output",
    "temp_dir": None,
    "disc_number": None,
    "rip_mode": "main",
    "encode_format": "none",
    "quality": None,
    "min_length": 10,
    "keep_original": False,
    "eject_on_complete": True,
}


def resolve_options(
    job_options: Optional[JobOptions],
    global_overrides: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the effective option set for a single job.

    Priority order (highest first):

    1. Values explicitly set in the job's ``options`` object.
    2. Values supplied in *global_overrides* (from the CLI ``queue run`` flags).
    3. Built-in diskripr defaults.

    Args:
        job_options:      The job's ``options`` object, or ``None`` if absent.
        global_overrides: Mapping of option names to values from the CLI.
                          Only keys present in this dict are considered
                          overrides — the caller must omit keys it did not
                          receive from the user.

    Returns:
        A dict of all resolved option values, ready to be passed as keyword
        arguments to :class:`~diskripr.config.MovieConfig` or
        :class:`~diskripr.config.ShowConfig`.
    """
    result: dict[str, Any] = dict(_BUILTIN_DEFAULTS)

    # Layer 2: CLI global overrides.
    result.update(global_overrides)

    # Layer 1: per-job options (highest priority; only non-None values win).
    if job_options is not None:
        for option_name, option_value in job_options.model_dump().items():
            if option_value is not None:
                result[option_name] = option_value

    return result


# ---------------------------------------------------------------------------
# Job label helper
# ---------------------------------------------------------------------------

def _job_label(job: MovieJob | ShowJob) -> str:
    """Return a short human-readable description of *job* for log messages."""
    if isinstance(job, MovieJob):
        return f"movie '{job.movie.name}' ({job.movie.year})"
    return f"show '{job.show.name}' S{job.show.season:02d} ep{job.show.start_episode}"


# ---------------------------------------------------------------------------
# QueueRunner (8.4)
# ---------------------------------------------------------------------------

@dataclass
class QueueRunner:
    """Runs a validated :class:`~diskripr.schema.JobFile` sequentially.

    Resolves effective options per job (job options → CLI overrides → built-in
    defaults), dispatches to the appropriate pipeline, and handles disc swap
    sequencing between jobs.

    Attributes:
        poll_interval:  Seconds between disc-state probes (default 5).
        timeout_seconds: Maximum wait for disc removal/insertion (default 1800).
        disc_probe:     Callable ``(device) -> bool`` used to detect disc state.
                        Injected during tests; defaults to the udevadm probe.
    """

    poll_interval: int = field(default=_DEFAULT_POLL_INTERVAL)
    timeout_seconds: int = field(default=_DEFAULT_TIMEOUT)
    disc_probe: Callable[[str], bool] = field(default=_DEFAULT_DISC_PROBE)

    def run(
        self,
        job_file: JobFile,
        global_overrides: Optional[dict[str, Any]] = None,
    ) -> None:
        """Execute all jobs in *job_file* in order.

        Logs a warning for any job whose resolved ``rip_mode`` is ``"ask"``
        (unattended operation with ask-mode requires operator presence).

        Between jobs, performs disc swap sequencing:

        * If ``eject_on_complete=True`` (the default), the pipeline ejects the
          disc after the job finishes.  The runner then waits for the drive to
          report empty, prompts or logs the swap message, and waits for a new
          disc to be inserted.
        * If ``eject_on_complete=False``, the runner prompts the user to
          manually remove and insert a disc, then starts polling for insertion.

        Args:
            job_file:         Validated job file (must pass
                              :func:`validate_job_file` with no errors).
            global_overrides: Optional dict of CLI-supplied option values
                              that act as defaults for all jobs.  Keys absent
                              here fall through to built-in defaults.
        """
        if global_overrides is None:
            global_overrides = {}

        jobs = job_file.jobs
        total = len(jobs)

        for index, job in enumerate(jobs):
            job_num = index + 1
            label = _job_label(job)
            id_str = f" (id={job.id})" if job.id else ""

            _LOG.info(
                "Starting job %d/%d: %s%s", job_num, total, label, id_str
            )

            opts = resolve_options(job.options, global_overrides)

            if opts.get("rip_mode") == "ask":
                _LOG.warning(
                    "Job %d/%d has rip_mode='ask' — unattended queue run "
                    "will require operator interaction.",
                    job_num,
                    total,
                )

            pipeline = self._build_pipeline(job, opts)
            pipeline.run()

            _LOG.info(
                "Job %d/%d complete: %s%s", job_num, total, label, id_str
            )

            # Disc swap sequencing between jobs — skip after the last job.
            if index < total - 1:
                next_device = opts.get("device", _BUILTIN_DEFAULTS["device"])
                eject_done = bool(opts.get("eject_on_complete", True))
                self._swap_disc(next_device, job_num, total, eject_done)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_pipeline(
        self,
        job: MovieJob | ShowJob,
        opts: dict[str, Any],
    ) -> MoviePipeline | ShowPipeline:
        """Construct the appropriate pipeline for *job* using resolved *opts*.

        Args:
            job:  The job to build a pipeline for.
            opts: Fully-resolved option dict from :func:`resolve_options`.

        Returns:
            A configured :class:`~diskripr.pipeline.MoviePipeline` or
            :class:`~diskripr.pipeline.ShowPipeline` instance, not yet run.
        """
        common = {
            "device": opts["device"],
            "output_dir": opts["output_dir"],
            "temp_dir": opts.get("temp_dir"),
            "disc_number": opts.get("disc_number"),
            "rip_mode": opts["rip_mode"],
            "encode_format": opts["encode_format"],
            "quality": opts.get("quality"),
            "min_length": opts["min_length"],
            "keep_original": opts["keep_original"],
            "eject_on_complete": opts["eject_on_complete"],
        }

        if isinstance(job, MovieJob):
            config = MovieConfig(
                movie_name=job.movie.name,
                movie_year=job.movie.year,
                **common,
            )
            return MoviePipeline(config)

        # ShowJob
        config = ShowConfig(
            show_name=job.show.name,
            season_number=job.show.season,
            start_episode=job.show.start_episode,
            **common,
        )
        return ShowPipeline(config)

    def _swap_disc(
        self,
        device: str,
        completed_job_num: int,
        total: int,
        eject_done: bool,
    ) -> None:
        """Handle disc swap sequencing after a completed job.

        Args:
            device:            Block device path.
            completed_job_num: 1-based index of the job that just finished.
            total:             Total job count.
            eject_done:        ``True`` if the pipeline already ejected the disc.
        """
        next_job_num = completed_job_num + 1

        if eject_done:
            # Pipeline ejected the disc; wait for the drive to go empty.
            _LOG.info(
                "Waiting for disc to be removed from %s before job %d/%d...",
                device,
                next_job_num,
                total,
            )
            wait_for_disc_removed(
                device,
                timeout_seconds=self.timeout_seconds,
                poll_interval=self.poll_interval,
                disc_probe=self.disc_probe,
            )
        else:
            # User must manually remove the old disc.
            print(
                f"Job {completed_job_num} complete. "
                "Please remove the disc and insert the next one. "
                "Press Enter to begin polling..."
            )
            input()

        _LOG.info(
            "Waiting for disc %d of %d to be inserted into %s...",
            next_job_num,
            total,
            device,
        )
        wait_for_disc_inserted(
            device,
            timeout_seconds=self.timeout_seconds,
            poll_interval=self.poll_interval,
            disc_probe=self.disc_probe,
        )
        _LOG.info(
            "Disc detected — starting job %d/%d.", next_job_num, total
        )
