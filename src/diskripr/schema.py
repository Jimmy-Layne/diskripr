"""Pydantic models for the diskripr batch job file format.

Defines the JSON schema used by ``diskripr queue`` for structured rip queues.

Types:

- ``JobOptions``  — Optional per-job overrides for all ``BaseConfig`` fields.
- ``MovieMeta``   — Required movie-specific metadata (name, year).
- ``ShowMeta``    — Required show-specific metadata (name, season, start_episode).
- ``MovieJob``    — A single movie rip job.
- ``ShowJob``     — A single TV-season rip job.
- ``JobFile``     — Top-level envelope (version + ordered jobs array).

The discriminated union ``Job = Union[MovieJob, ShowJob]`` is keyed on the
``type`` field and is what ``JobFile.jobs`` holds.

JSON Schema export
------------------
Call ``export_json_schema(path)`` to write the Draft 2020-12 schema to *path*.
The generated file is the authoritative machine-readable specification for the
job file format and is published at ``docs/_static/diskripr-queue-schema.json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

class JobOptions(BaseModel):
    """Per-job encoding and device options — every field is optional.

    When a field is omitted, the effective value is resolved from the
    ``diskripr queue run`` command-line flag, or the built-in default if
    no flag was supplied.
    """

    device: Optional[str] = Field(
        default=None,
        description="Block device path (e.g. '/dev/sr0').",
    )
    output_dir: Optional[str] = Field(
        default=None,
        description="Absolute path to the output root directory.",
    )
    temp_dir: Optional[str] = Field(
        default=None,
        description="Temporary working directory; null uses system temp.",
    )
    disc_number: Optional[int] = Field(
        default=None,
        description=(
            "Disc index within a multi-disc title (1-based). "
            "Null or omitted for single-disc titles."
        ),
    )
    rip_mode: Optional[Literal["main", "all", "ask"]] = Field(
        default=None,
        description="Title selection mode: 'main', 'all', or 'ask'.",
    )
    encode_format: Optional[Literal["h264", "h265", "none", "ask"]] = Field(
        default=None,
        description="Encoding format: 'h264', 'h265', 'none', or 'ask'.",
    )
    quality: Optional[int] = Field(
        default=None,
        description="HandBrake CRF value; null uses the format default.",
    )
    min_length: Optional[int] = Field(
        default=None,
        description="Minimum title length in seconds.",
    )
    keep_original: Optional[bool] = Field(
        default=None,
        description="Retain raw MKV files after encoding.",
    )
    eject_on_complete: Optional[bool] = Field(
        default=None,
        description="Eject the disc when the job finishes.",
    )


# ---------------------------------------------------------------------------
# Job metadata objects
# ---------------------------------------------------------------------------

class MovieMeta(BaseModel):
    """Required movie metadata for a movie rip job."""

    name: str = Field(
        description="Movie title as it should appear in the Jellyfin library.",
    )
    year: int = Field(
        ge=1888,
        le=2100,
        description="Release year (1888–2100); used in directory naming.",
    )


class ShowMeta(BaseModel):
    """Required show metadata for a TV-season rip job."""

    name: str = Field(
        description="Series title as it should appear in the Jellyfin library.",
    )
    season: int = Field(
        ge=0,
        description="Season number >= 0; 0 maps to Jellyfin 'Specials'.",
    )
    start_episode: int = Field(
        ge=1,
        description="Episode number of the first title on this disc; >= 1.",
    )


# ---------------------------------------------------------------------------
# Job variants
# ---------------------------------------------------------------------------

class MovieJob(BaseModel):
    """A single movie rip job."""

    type: Literal["movie"] = Field(
        description="Discriminator field — must be 'movie' for movie jobs.",
    )
    id: Optional[str] = Field(  # noqa: A003  (shadows built-in, acceptable in model)
        default=None,
        description=(
            "Client-assigned idempotency key; UUID v4 recommended. "
            "Logged alongside job status."
        ),
    )
    movie: MovieMeta = Field(
        description="Movie-specific metadata (name, year).",
    )
    options: Optional[JobOptions] = Field(
        default=None,
        description=(
            "Rip options; any omitted field falls back to the CLI default "
            "or the value supplied on the queue run command line."
        ),
    )


class ShowJob(BaseModel):
    """A single TV-season rip job."""

    type: Literal["show"] = Field(
        description="Discriminator field — must be 'show' for show jobs.",
    )
    id: Optional[str] = Field(  # noqa: A003
        default=None,
        description=(
            "Client-assigned idempotency key; UUID v4 recommended. "
            "Logged alongside job status."
        ),
    )
    show: ShowMeta = Field(
        description="Show-specific metadata (name, season, start_episode).",
    )
    options: Optional[JobOptions] = Field(
        default=None,
        description=(
            "Rip options; any omitted field falls back to the CLI default "
            "or the value supplied on the queue run command line."
        ),
    )


# Discriminated union keyed on the ``type`` literal field.
Job = Annotated[Union[MovieJob, ShowJob], Field(discriminator="type")]


# ---------------------------------------------------------------------------
# Top-level envelope
# ---------------------------------------------------------------------------

class JobFile(BaseModel):
    """Top-level job file envelope.

    A job file is a UTF-8 JSON document with a schema version and an ordered
    array of job objects processed sequentially by the queue runner.
    """

    version: Literal["1.0"] = Field(
        description="Schema version; must be '1.0' for this release.",
    )
    jobs: list[Job] = Field(
        description="Ordered list of job objects; processed sequentially.",
    )


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------

_SCHEMA_HEADER = "https://json-schema.org/draft/2020-12/schema"


def export_json_schema(dest: Path) -> None:
    """Write the Draft 2020-12 JSON Schema for ``JobFile`` to *dest*.

    Creates parent directories as needed.  The generated file is the
    authoritative machine-readable specification for the job file format.

    Args:
        dest: Destination file path (typically
              ``docs/_static/diskripr-queue-schema.json``).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    schema = JobFile.model_json_schema()
    schema["$schema"] = _SCHEMA_HEADER
    schema.setdefault("title", "diskripr job file")
    dest.write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
