"""Data models that flow between diskripr pipeline stages.

All types are plain dataclasses. No Pydantic — data is populated by driver
code that has already validated tool output during parsing. Occasional
``__post_init__`` guards handle invariants (e.g. year must be a 4-digit int).

Defined types:

- ``DriveInfo``         — Optical drive device path and MakeMKV drive index.
- ``DiscInfo``          — Drive info, disc title string, and title list.
- ``Title``             — Per-title metadata from a MakeMKV scan: index, name,
                          duration (HH:MM:SS), size in bytes, chapter count,
                          stream summary, a heuristic type tag
                          (``"main"``, ``"feature-length"``, ``"extra"``,
                          ``"short"``), and optional MakeMKV segment fields.
                          Computed property ``duration_seconds``.
- ``Selection``         — The chosen main ``Title`` plus a list of
                          ``ClassifiedExtra`` objects.
- ``ClassifiedExtra``   — A ``Title`` reference annotated with a Jellyfin extra
                          type (e.g. ``"behindthescenes"``) and the generated
                          output filename.
- ``EpisodeEntry``      — A ``Title`` mapped to a TV episode with season/episode
                          numbers and an optional episode title.
- ``ShowSelection``     — Episode and extra lists produced by show classification.
- ``RipResult``         — Outcome of ripping a single title: title index,
                          output path, success flag, error message.
- ``EncodeResult``      — Same shape as ``RipResult`` plus original and encoded
                          file sizes in bytes.
- ``VideoTrack``        — Codec name and resolution string.
- ``AudioTrack``        — Codec name, language code, and channel count.
- ``SubtitleTrack``     — Codec name, language code, track title, and forced
                          flag.
- ``StreamReport``      — Aggregated video, audio, and subtitle tracks for a
                          single MKV file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

TitleType = Literal["main", "feature-length", "extra", "short"]

JellyfinExtraType = Literal[
    "behindthescenes",
    "deletedscene",
    "featurette",
    "interview",
    "scene",
    "short",
    "trailer",
    "extra",
]

_DURATION_RE = re.compile(r"^(\d+):([0-5]\d):([0-5]\d)$")


@dataclass
class DriveInfo:
    """Optical drive device path and MakeMKV drive index."""

    device: str
    drive_index: int

    def __post_init__(self) -> None:
        if self.drive_index < 0:
            raise ValueError(
                f"drive_index must be non-negative, got {self.drive_index}"
            )


@dataclass
class DiscInfo:
    """Drive info plus the disc title string returned by MakeMKV."""

    drive: DriveInfo
    disc_title: str
    titles: list[Title] = field(default_factory=list)


@dataclass
class Title:
    """Per-title metadata from a MakeMKV scan."""

    index: int
    name: str
    duration: str          # HH:MM:SS
    size_bytes: int
    chapter_count: int
    stream_summary: str
    title_type: TitleType
    segment_count: Optional[int] = None
    segments_map: Optional[str] = None

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError(f"index must be non-negative, got {self.index}")
        if not _DURATION_RE.match(self.duration):
            raise ValueError(
                f"duration must be HH:MM:SS, got {self.duration!r}"
            )
        if self.size_bytes < 0:
            raise ValueError(
                f"size_bytes must be non-negative, got {self.size_bytes}"
            )
        if self.chapter_count < 0:
            raise ValueError(
                f"chapter_count must be non-negative, got {self.chapter_count}"
            )

    @property
    def duration_seconds(self) -> int:
        """Return duration as total seconds."""
        match = _DURATION_RE.match(self.duration)
        hours, minutes, seconds = (int(part) for part in match.groups())  # type: ignore[union-attr]
        return hours * 3600 + minutes * 60 + seconds


@dataclass
class ClassifiedExtra:
    """A Title annotated with a Jellyfin extra type and output filename."""

    title: Title
    extra_type: JellyfinExtraType
    output_filename: str


@dataclass
class Selection:
    """The chosen main Title plus any classified extras."""

    main: Title
    extras: list[ClassifiedExtra] = field(default_factory=list)


@dataclass
class EpisodeEntry:
    """A Title mapped to a specific TV episode."""

    title: Title
    season_number: int
    episode_number: int
    episode_title: Optional[str] = None


@dataclass
class ShowSelection:
    """Episode and extra lists produced by show classification."""

    episodes: list[EpisodeEntry] = field(default_factory=list)
    extras: list[ClassifiedExtra] = field(default_factory=list)


@dataclass
class RipResult:
    """Outcome of ripping a single title."""

    title_index: int
    output_path: Optional[Path]
    success: bool
    error_message: Optional[str] = None


@dataclass
class EncodeResult:
    """Outcome of encoding a single title, including before/after file sizes."""

    title_index: int
    output_path: Optional[Path]
    success: bool
    error_message: Optional[str] = None
    original_size_bytes: Optional[int] = None
    encoded_size_bytes: Optional[int] = None


@dataclass
class VideoTrack:
    """Codec name and resolution string for a video stream."""

    codec: str
    resolution: str


@dataclass
class AudioTrack:
    """Codec name, language code, and channel count for an audio stream."""

    codec: str
    language: str
    channels: int


@dataclass
class SubtitleTrack:
    """Codec name, language code, track title, and forced flag for a subtitle stream."""

    codec: str
    language: str
    track_title: str
    forced: bool


@dataclass
class StreamReport:
    """Aggregated stream tracks for a single MKV file."""

    video_tracks: list[VideoTrack] = field(default_factory=list)
    audio_tracks: list[AudioTrack] = field(default_factory=list)
    subtitle_tracks: list[SubtitleTrack] = field(default_factory=list)
