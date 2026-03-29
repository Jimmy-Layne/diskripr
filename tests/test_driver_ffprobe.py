"""Tests for ``diskripr.drivers.ffprobe``.

Strategy:
- Module-level parsing helpers (``_parse_video_stream``,
  ``_parse_audio_stream``, ``_parse_subtitle_stream``) are tested directly
  against representative stream dicts.
- ``_parse_streams`` is tested against the JSON fixture loaded from disk.
- ``inspect`` is tested by mocking ``is_available`` and ``run`` so no binary
  or MKV file is required.

Fixture files used:
- ``tests/data/ffprobe/dvd_streams.json``

Expected fixture content (from HandBrake scan of A1_t01.mkv + synthetic
subtitle added for coverage):
  - 1 video track:    mpeg2video, 720x480
  - 1 audio track:    ac3, English (eng), 6 channels
  - 1 subtitle track: dvd_subtitle, English (eng), forced=True
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from conftest import load_fixture, DATA_DIR
from diskripr.drivers.ffprobe import (
    FfprobeDriver,
    _parse_audio_stream,
    _parse_subtitle_stream,
    _parse_video_stream,
)
from diskripr.models import AudioTrack, StreamReport, SubtitleTrack, VideoTrack


# ---------------------------------------------------------------------------
# Helper — load fixture streams list
# ---------------------------------------------------------------------------

def _fixture_streams() -> list[dict[str, Any]]:
    raw = load_fixture("ffprobe", "dvd_streams.json")
    return json.loads(raw)["streams"]


# ---------------------------------------------------------------------------
# _parse_video_stream
# ---------------------------------------------------------------------------

class TestParseVideoStream:
    def test_codec_and_resolution(self) -> None:
        stream = _fixture_streams()[0]
        track = _parse_video_stream(stream)
        assert isinstance(track, VideoTrack)
        assert track.codec == "mpeg2video"
        assert track.resolution == "720x480"

    def test_missing_width_falls_back(self) -> None:
        stream: dict[str, Any] = {"codec_name": "h264"}
        track = _parse_video_stream(stream)
        assert track.resolution == "unknown"

    def test_missing_codec_name_falls_back(self) -> None:
        stream: dict[str, Any] = {"width": 1920, "height": 1080}
        track = _parse_video_stream(stream)
        assert track.codec == "unknown"


# ---------------------------------------------------------------------------
# _parse_audio_stream
# ---------------------------------------------------------------------------

class TestParseAudioStream:
    def test_codec_language_channels(self) -> None:
        stream = _fixture_streams()[1]
        track = _parse_audio_stream(stream)
        assert isinstance(track, AudioTrack)
        assert track.codec == "ac3"
        assert track.language == "eng"
        assert track.channels == 6

    def test_missing_language_defaults_to_und(self) -> None:
        stream: dict[str, Any] = {"codec_name": "ac3", "channels": 2}
        track = _parse_audio_stream(stream)
        assert track.language == "und"

    def test_missing_channels_defaults_to_zero(self) -> None:
        stream: dict[str, Any] = {
            "codec_name": "ac3",
            "tags": {"language": "eng"},
        }
        track = _parse_audio_stream(stream)
        assert track.channels == 0


# ---------------------------------------------------------------------------
# _parse_subtitle_stream
# ---------------------------------------------------------------------------

class TestParseSubtitleStream:
    def test_dvd_subtitle_forced(self) -> None:
        stream = _fixture_streams()[2]
        track = _parse_subtitle_stream(stream)
        assert isinstance(track, SubtitleTrack)
        assert track.codec == "dvd_subtitle"
        assert track.language == "eng"
        assert track.track_title == "English (Forced)"
        assert track.forced is True

    def test_not_forced_when_disposition_zero(self) -> None:
        stream: dict[str, Any] = {
            "codec_name": "dvd_subtitle",
            "tags": {"language": "fra", "title": "French"},
            "disposition": {"forced": 0},
        }
        track = _parse_subtitle_stream(stream)
        assert track.forced is False

    def test_missing_disposition_defaults_not_forced(self) -> None:
        stream: dict[str, Any] = {
            "codec_name": "dvd_subtitle",
            "tags": {"language": "eng"},
        }
        track = _parse_subtitle_stream(stream)
        assert track.forced is False

    def test_missing_title_tag_defaults_empty_string(self) -> None:
        stream: dict[str, Any] = {
            "codec_name": "dvd_subtitle",
            "tags": {"language": "eng"},
            "disposition": {"forced": 0},
        }
        track = _parse_subtitle_stream(stream)
        assert track.track_title == ""


# ---------------------------------------------------------------------------
# _parse_streams — integration of all three parsers
# ---------------------------------------------------------------------------

class TestParseStreams:
    def test_fixture_produces_correct_report(self) -> None:
        streams = _fixture_streams()
        report = FfprobeDriver._parse_streams(streams)
        assert isinstance(report, StreamReport)
        assert len(report.video_tracks) == 1
        assert len(report.audio_tracks) == 1
        assert len(report.subtitle_tracks) == 1

    def test_unknown_codec_type_ignored(self) -> None:
        streams: list[dict[str, Any]] = [
            {"codec_type": "data", "codec_name": "bin_data"},
        ]
        report = FfprobeDriver._parse_streams(streams)
        assert report.video_tracks == []
        assert report.audio_tracks == []
        assert report.subtitle_tracks == []

    def test_empty_streams_list(self) -> None:
        report = FfprobeDriver._parse_streams([])
        assert report.video_tracks == []
        assert report.audio_tracks == []
        assert report.subtitle_tracks == []

    def test_multiple_audio_tracks(self) -> None:
        streams: list[dict[str, Any]] = [
            {
                "codec_type": "audio",
                "codec_name": "ac3",
                "channels": 6,
                "tags": {"language": "eng"},
            },
            {
                "codec_type": "audio",
                "codec_name": "ac3",
                "channels": 2,
                "tags": {"language": "eng"},
            },
        ]
        report = FfprobeDriver._parse_streams(streams)
        assert len(report.audio_tracks) == 2


# ---------------------------------------------------------------------------
# inspect() — subprocess boundary
# ---------------------------------------------------------------------------

class TestInspect:
    def test_returns_none_when_ffprobe_not_on_path(self, tmp_path: Path) -> None:
        driver = FfprobeDriver()
        with patch.object(driver, "is_available", return_value=False):
            result = driver.inspect(tmp_path / "movie.mkv")
        assert result is None

    def test_returns_none_on_invalid_json(self, tmp_path: Path) -> None:
        driver = FfprobeDriver()
        completed = subprocess.CompletedProcess(["ffprobe"], 0, "not-json", "")
        with patch.object(driver, "is_available", return_value=True):
            with patch.object(driver, "run", return_value=completed):
                result = driver.inspect(tmp_path / "movie.mkv")
        assert result is None

    def test_returns_none_on_tool_error(self, tmp_path: Path) -> None:
        from diskripr.drivers.base import ToolError
        driver = FfprobeDriver()
        with patch.object(driver, "is_available", return_value=True):
            with patch.object(
                driver,
                "run",
                side_effect=ToolError(["ffprobe"], 1, "no such file"),
            ):
                result = driver.inspect(tmp_path / "missing.mkv")
        assert result is None

    def test_returns_stream_report_on_success(self, tmp_path: Path) -> None:
        driver = FfprobeDriver()
        fixture_text = load_fixture("ffprobe", "dvd_streams.json")
        completed = subprocess.CompletedProcess(["ffprobe"], 0, fixture_text, "")
        with patch.object(driver, "is_available", return_value=True):
            with patch.object(driver, "run", return_value=completed):
                result = driver.inspect(tmp_path / "movie.mkv")
        assert result is not None
        assert len(result.video_tracks) == 1
        assert result.video_tracks[0].codec == "mpeg2video"
        assert len(result.audio_tracks) == 1
        assert len(result.subtitle_tracks) == 1
        assert result.subtitle_tracks[0].forced is True

    def test_empty_streams_array_returns_empty_report(self, tmp_path: Path) -> None:
        driver = FfprobeDriver()
        completed = subprocess.CompletedProcess(
            ["ffprobe"], 0, '{"streams": []}', ""
        )
        with patch.object(driver, "is_available", return_value=True):
            with patch.object(driver, "run", return_value=completed):
                result = driver.inspect(tmp_path / "movie.mkv")
        assert result is not None
        assert result.video_tracks == []
        assert result.audio_tracks == []
        assert result.subtitle_tracks == []
