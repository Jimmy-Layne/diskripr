"""Tests for ``diskripr.models``.

Covers:
- ``DriveInfo.__post_init__`` guard on negative ``drive_index``.
- ``Title.__post_init__`` guards: negative index, malformed duration, negative
  size_bytes / chapter_count.
- ``Title.duration_seconds`` computed property for representative values.
- ``Title`` optional MakeMKV segment fields (``segment_count``, ``segments_map``).
- ``ClassifiedExtra`` and ``Selection`` construction.
- ``EpisodeEntry`` and ``ShowSelection`` construction.
- ``RipResult`` and ``EncodeResult`` optional field defaults.
"""

from __future__ import annotations

import pytest

from diskripr.models import (
    AudioTrack,
    ClassifiedExtra,
    DiscInfo,
    DriveInfo,
    EncodeResult,
    EpisodeEntry,
    RipResult,
    Selection,
    ShowSelection,
    StreamReport,
    SubtitleTrack,
    Title,
    VideoTrack,
)


# ---------------------------------------------------------------------------
# DriveInfo
# ---------------------------------------------------------------------------

class TestDriveInfo:
    def test_valid_construction(self) -> None:
        drive = DriveInfo(device="/dev/sr0", drive_index=0)
        assert drive.device == "/dev/sr0"
        assert drive.drive_index == 0

    def test_negative_drive_index_raises(self) -> None:
        with pytest.raises(ValueError, match="drive_index"):
            DriveInfo(device="/dev/sr0", drive_index=-1)

    def test_zero_drive_index_is_valid(self) -> None:
        drive = DriveInfo(device="/dev/sr0", drive_index=0)
        assert drive.drive_index == 0


# ---------------------------------------------------------------------------
# DiscInfo
# ---------------------------------------------------------------------------

class TestDiscInfo:
    def test_construction(self) -> None:
        drive = DriveInfo(device="/dev/sr0", drive_index=0)
        disc = DiscInfo(drive=drive, disc_title="ROSENCRANTZ_AND_GUILDENSTERN")
        assert disc.disc_title == "ROSENCRANTZ_AND_GUILDENSTERN"
        assert disc.drive is drive


# ---------------------------------------------------------------------------
# Title — __post_init__ guards
# ---------------------------------------------------------------------------

class TestTitlePostInit:
    def test_valid_construction(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title()
        assert title.index == 0
        assert title.duration == "01:57:25"

    def test_negative_index_raises(self, make_title):  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="index"):
            make_title(index=-1)

    def test_bad_duration_format_raises(self, make_title):  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="duration"):
            make_title(duration="not-a-time")

    def test_duration_missing_seconds_raises(self, make_title):  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="duration"):
            make_title(duration="01:57")

    def test_negative_size_bytes_raises(self, make_title):  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="size_bytes"):
            make_title(size_bytes=-1)

    def test_negative_chapter_count_raises(self, make_title):  # type: ignore[no-untyped-def]
        with pytest.raises(ValueError, match="chapter_count"):
            make_title(chapter_count=-1)

    def test_zero_size_bytes_is_valid(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(size_bytes=0)
        assert title.size_bytes == 0

    def test_duration_single_digit_hours_is_valid(self, make_title):  # type: ignore[no-untyped-def]
        # The model regex allows variable-width hours; drivers normalise to 2.
        title = make_title(duration="1:57:25")
        assert title.duration == "1:57:25"


# ---------------------------------------------------------------------------
# Title — duration_seconds property
# ---------------------------------------------------------------------------

class TestTitleDurationSeconds:
    def test_main_feature_duration(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(duration="01:57:25")
        assert title.duration_seconds == 7045  # 1*3600 + 57*60 + 25

    def test_short_clip_duration(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(duration="00:00:32")
        assert title.duration_seconds == 32

    def test_zero_duration(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(duration="00:00:00")
        assert title.duration_seconds == 0

    def test_extra_duration_boundary(self, make_title):  # type: ignore[no-untyped-def]
        # Exactly 10 minutes — the lower bound for "extra" classification.
        title = make_title(duration="00:10:00")
        assert title.duration_seconds == 600

    def test_feature_length_boundary(self, make_title):  # type: ignore[no-untyped-def]
        # Exactly 45 minutes — the lower bound for "feature-length".
        title = make_title(duration="00:45:00")
        assert title.duration_seconds == 2700


# ---------------------------------------------------------------------------
# Title — optional MakeMKV segment fields
# ---------------------------------------------------------------------------

class TestTitleSegmentFields:
    def test_segment_count_defaults_none(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title()
        assert title.segment_count is None

    def test_segments_map_defaults_none(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title()
        assert title.segments_map is None

    def test_segment_count_stored(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(segment_count=4)
        assert title.segment_count == 4

    def test_segments_map_stored(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(segments_map="0,1,2,3")
        assert title.segments_map == "0,1,2,3"

    def test_both_segment_fields_together(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(segment_count=2, segments_map="0,1")
        assert title.segment_count == 2
        assert title.segments_map == "0,1"


# ---------------------------------------------------------------------------
# ClassifiedExtra
# ---------------------------------------------------------------------------

class TestClassifiedExtra:
    def test_construction(self, sample_title: Title) -> None:
        extra = ClassifiedExtra(
            title=sample_title,
            extra_type="behindthescenes",
            output_filename="Behind the Scenes 1-behindthescenes.mkv",
        )
        assert extra.extra_type == "behindthescenes"
        assert extra.title is sample_title
        assert extra.output_filename.endswith(".mkv")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------

class TestSelection:
    def test_construction_without_extras(self, sample_title: Title) -> None:
        sel = Selection(main=sample_title)
        assert sel.main is sample_title
        assert sel.extras == []

    def test_construction_with_extras(self, make_title) -> None:  # type: ignore[no-untyped-def]
        main = make_title(index=0, title_type="main")
        extra_title = make_title(index=1, duration="00:12:00", title_type="extra")
        classified = ClassifiedExtra(
            title=extra_title,
            extra_type="featurette",
            output_filename="Featurette 1-featurette.mkv",
        )
        sel = Selection(main=main, extras=[classified])
        assert len(sel.extras) == 1
        assert sel.extras[0].extra_type == "featurette"


# ---------------------------------------------------------------------------
# EpisodeEntry
# ---------------------------------------------------------------------------

class TestEpisodeEntry:
    def test_construction_with_title(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(index=0, duration="00:42:00", title_type="main")
        entry = EpisodeEntry(title=title, season_number=1, episode_number=3)
        assert entry.title is title
        assert entry.season_number == 1
        assert entry.episode_number == 3
        assert entry.episode_title is None

    def test_episode_title_stored(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(index=1, duration="00:42:00", title_type="main")
        entry = EpisodeEntry(
            title=title,
            season_number=2,
            episode_number=5,
            episode_title="Pilot",
        )
        assert entry.episode_title == "Pilot"

    def test_episode_title_defaults_none(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(index=0, duration="00:42:00", title_type="main")
        entry = EpisodeEntry(title=title, season_number=1, episode_number=1)
        assert entry.episode_title is None


# ---------------------------------------------------------------------------
# ShowSelection
# ---------------------------------------------------------------------------

class TestShowSelection:
    def test_empty_construction(self) -> None:
        show_sel = ShowSelection()
        assert show_sel.episodes == []
        assert show_sel.extras == []

    def test_construction_with_episodes(self, make_title):  # type: ignore[no-untyped-def]
        title = make_title(index=0, duration="00:42:00", title_type="main")
        entry = EpisodeEntry(title=title, season_number=1, episode_number=1)
        show_sel = ShowSelection(episodes=[entry])
        assert len(show_sel.episodes) == 1
        assert show_sel.episodes[0] is entry

    def test_construction_with_extras(self, make_title):  # type: ignore[no-untyped-def]
        extra_title = make_title(index=1, duration="00:05:00", title_type="extra")
        classified = ClassifiedExtra(
            title=extra_title,
            extra_type="behindthescenes",
            output_filename="Behind the Scenes 1-behindthescenes.mkv",
        )
        show_sel = ShowSelection(extras=[classified])
        assert len(show_sel.extras) == 1
        assert show_sel.extras[0].extra_type == "behindthescenes"

    def test_construction_with_episodes_and_extras(self, make_title):  # type: ignore[no-untyped-def]
        ep_title = make_title(index=0, duration="00:42:00", title_type="main")
        extra_title = make_title(index=1, duration="00:05:00", title_type="extra")
        entry = EpisodeEntry(title=ep_title, season_number=1, episode_number=1)
        classified = ClassifiedExtra(
            title=extra_title,
            extra_type="featurette",
            output_filename="Featurette 1-featurette.mkv",
        )
        show_sel = ShowSelection(episodes=[entry], extras=[classified])
        assert len(show_sel.episodes) == 1
        assert len(show_sel.extras) == 1


# ---------------------------------------------------------------------------
# RipResult / EncodeResult
# ---------------------------------------------------------------------------

class TestRipResult:
    def test_successful_rip(self, tmp_path):  # type: ignore[no-untyped-def]
        output_path = tmp_path / "title_t00.mkv"
        result = RipResult(title_index=0, output_path=output_path, success=True)
        assert result.success is True
        assert result.error_message is None

    def test_failed_rip(self) -> None:
        result = RipResult(
            title_index=0,
            output_path=None,
            success=False,
            error_message="CSS decryption failed",
        )
        assert result.success is False
        assert result.error_message == "CSS decryption failed"


class TestEncodeResult:
    def test_successful_encode(self, tmp_path):  # type: ignore[no-untyped-def]
        output_path = tmp_path / "encoded.mkv"
        result = EncodeResult(
            title_index=0,
            output_path=output_path,
            success=True,
            original_size_bytes=100_000_000,
            encoded_size_bytes=40_000_000,
        )
        assert result.success is True
        assert result.original_size_bytes == 100_000_000
        assert result.encoded_size_bytes == 40_000_000

    def test_failed_encode_optional_fields_default_none(self) -> None:
        result = EncodeResult(title_index=0, output_path=None, success=False)
        assert result.original_size_bytes is None
        assert result.encoded_size_bytes is None
        assert result.error_message is None


# ---------------------------------------------------------------------------
# Stream report types
# ---------------------------------------------------------------------------

class TestStreamTypes:
    def test_video_track(self) -> None:
        track = VideoTrack(codec="mpeg2video", resolution="720x480")
        assert track.codec == "mpeg2video"
        assert track.resolution == "720x480"

    def test_audio_track(self) -> None:
        track = AudioTrack(codec="ac3", language="eng", channels=6)
        assert track.channels == 6

    def test_subtitle_track_forced(self) -> None:
        track = SubtitleTrack(
            codec="dvd_subtitle",
            language="eng",
            track_title="English (Forced)",
            forced=True,
        )
        assert track.forced is True

    def test_stream_report_empty_on_init(self) -> None:
        report = StreamReport()
        assert report.video_tracks == []
        assert report.audio_tracks == []
        assert report.subtitle_tracks == []
