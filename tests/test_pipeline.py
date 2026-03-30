"""Tests for ``diskripr.pipeline.Pipeline``.

Each stage has its own test class.  Driver dependencies are mocked at the
pipeline module level (``diskripr.pipeline.<DriverClass>``) so no physical
disc drive is required.

The ``Pipeline`` instance is used throughout — stage tests set the relevant
state attributes directly (e.g. ``pipeline.disc_info``, ``pipeline.selection``)
before calling the method under test, matching the expected CLI usage pattern.

Test organisation mirrors the pipeline stages:
1. Pipeline.discover()   — drive detection + title scan
2. _select()             — title selection logic (main / all modes)
3. _classify()           — Jellyfin extra type assignment
4. Pipeline.rip()        — MakeMKV title extraction
5. Pipeline.encode()     — HandBrake re-encoding (optional stage)
6. Pipeline.organize()   — Jellyfin directory tree construction
7. Pipeline.run()        — full pipeline integration path
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest

from diskripr.config import Config
from diskripr.drivers.base import EncodeError, RipError, ToolNotFound
from diskripr.models import (
    ClassifiedExtra,
    DiscInfo,
    DriveInfo,
    EncodeResult,
    RipResult,
    Selection,
    Title,
)
from diskripr.pipeline import Pipeline, _classify, _select


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_drive(device: str = "/dev/sr0", index: int = 0) -> DriveInfo:
    return DriveInfo(device=device, drive_index=index)


# ---------------------------------------------------------------------------
# Stage 1: Pipeline.discover
# ---------------------------------------------------------------------------

class TestDiscover:
    def test_returns_disc_info_with_drive(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)

        drive = _make_drive(device=str(fake_device))
        title = make_title()

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
        ):
            mock_lsdvd.return_value.read_disc.return_value = None
            mock_makemkv.return_value.scan_drives.return_value = [drive]
            mock_makemkv.return_value.scan_titles.return_value = [title]

            result = pipeline.discover()

        assert result is pipeline.disc_info
        assert pipeline.disc_info.drive == drive
        assert len(pipeline.disc_info.titles) == 1
        assert pipeline.disc_info.titles[0] == title

    def test_fails_when_device_not_found(self, sample_config: Config) -> None:
        sample_config.device = "/dev/nonexistent_sr99"
        pipeline = Pipeline(sample_config)
        with pytest.raises(RuntimeError, match="Device not found"):
            pipeline.discover()

    def test_lsdvd_failure_is_non_fatal(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)

        drive = _make_drive(device=str(fake_device))
        title = make_title()

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
        ):
            mock_lsdvd.return_value.read_disc.side_effect = ToolNotFound("lsdvd")
            mock_makemkv.return_value.scan_drives.return_value = [drive]
            mock_makemkv.return_value.scan_titles.return_value = [title]

            pipeline.discover()

        assert pipeline.disc_info.drive == drive

    def test_falls_back_to_drive_index_zero_when_device_not_matched(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)

        drive_zero = DriveInfo(device="/dev/sr1", drive_index=0)
        title = make_title()

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
        ):
            mock_lsdvd.return_value.read_disc.return_value = None
            mock_makemkv.return_value.scan_drives.return_value = [drive_zero]
            mock_makemkv.return_value.scan_titles.return_value = [title]

            pipeline.discover()

        assert pipeline.disc_info.drive.drive_index == 0
        mock_makemkv.return_value.scan_titles.assert_called_once_with(0)

    def test_fails_with_diagnostic_when_no_titles_after_filtering(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.min_length = 9999

        drive = _make_drive(device=str(fake_device))
        short_title = make_title(duration="00:00:05", title_type="short")

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
        ):
            mock_lsdvd.return_value.read_disc.return_value = None
            mock_makemkv.return_value.scan_drives.return_value = [drive]
            mock_makemkv.return_value.scan_titles.return_value = [short_title]

            with pytest.raises(RuntimeError, match="No titles found"):
                pipeline.discover()

    def test_filters_titles_below_min_length(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.min_length = 60

        drive = _make_drive(device=str(fake_device))
        long_title = make_title(duration="01:57:00", title_type="main")
        short_title = make_title(index=1, duration="00:00:30", title_type="short")

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
        ):
            mock_lsdvd.return_value.read_disc.return_value = None
            mock_makemkv.return_value.scan_drives.return_value = [drive]
            mock_makemkv.return_value.scan_titles.return_value = [long_title, short_title]

            pipeline.discover()

        assert len(pipeline.disc_info.titles) == 1
        assert pipeline.disc_info.titles[0].index == 0


# ---------------------------------------------------------------------------
# Title selection logic (_select)
# ---------------------------------------------------------------------------

class TestTitleSelection:
    def test_main_mode_returns_only_longest_title(
        self, make_title: Callable[..., Title]
    ) -> None:
        short = make_title(index=0, duration="00:10:00", title_type="extra")
        long_title = make_title(index=1, duration="01:57:00", title_type="main")

        main, extras = _select([short, long_title], "main")

        assert main == long_title
        assert extras == []

    def test_all_mode_returns_all_titles(
        self, make_title: Callable[..., Title]
    ) -> None:
        short = make_title(index=0, duration="00:10:00", title_type="extra")
        long_title = make_title(index=1, duration="01:57:00", title_type="main")

        main, extras = _select([short, long_title], "all")

        assert main == long_title
        assert extras == [short]

    def test_main_title_is_excluded_from_extras_list(
        self, make_title: Callable[..., Title]
    ) -> None:
        title_a = make_title(index=0, duration="01:57:00", title_type="main")
        title_b = make_title(index=1, duration="00:30:00", title_type="extra")
        title_c = make_title(index=2, duration="00:15:00", title_type="extra")

        main, extras = _select([title_a, title_b, title_c], "all")

        assert main == title_a
        assert title_a not in extras
        assert len(extras) == 2
        assert title_b in extras
        assert title_c in extras


# ---------------------------------------------------------------------------
# Extras classification logic (_classify)
# ---------------------------------------------------------------------------

class TestExtrasClassification:
    def test_all_mode_defaults_extras_to_extra_type(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra = make_title(index=1, duration="00:15:00", title_type="extra")

        extras_dir = tmp_path / "extras"
        selection = _classify(main, [extra], extras_dir)

        assert len(selection.extras) == 1
        assert selection.extras[0].extra_type == "extra"

    def test_auto_numbers_extras_of_same_type(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_a = make_title(index=1, duration="00:15:00", title_type="extra")
        extra_b = make_title(index=2, duration="00:12:00", title_type="extra")

        extras_dir = tmp_path / "extras"
        selection = _classify(main, [extra_a, extra_b], extras_dir)

        filenames = [classified.output_filename for classified in selection.extras]
        assert filenames[0] == "Extra 1-extra.mkv"
        assert filenames[1] == "Extra 2-extra.mkv"

    def test_numbering_continues_from_existing_extras_in_multi_disc(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        extras_dir = tmp_path / "extras"
        extras_dir.mkdir()
        (extras_dir / "Extra 2-extra.mkv").touch()

        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra = make_title(index=1, duration="00:15:00", title_type="extra")

        selection = _classify(main, [extra], extras_dir)

        assert selection.extras[0].output_filename == "Extra 3-extra.mkv"

    def test_output_filename_matches_jellyfin_format(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra = make_title(index=1, duration="00:15:00", title_type="extra")

        extras_dir = tmp_path / "extras"
        selection = _classify(main, [extra], extras_dir)

        filename = selection.extras[0].output_filename
        assert filename.endswith("-extra.mkv")
        assert "Extra" in filename


# ---------------------------------------------------------------------------
# Stage 2: Pipeline.rip
# ---------------------------------------------------------------------------

class TestRipStage:
    def test_rips_main_title_to_temp_dir(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title()

        pipeline = Pipeline(sample_config)
        pipeline.disc_info = DiscInfo(drive=drive, disc_title="TEST", titles=[main])
        pipeline.selection = Selection(main=main, extras=[])

        rip_path = tmp_path / "output" / ".tmp" / "title_t00.mkv"
        rip_path.parent.mkdir(parents=True, exist_ok=True)
        rip_path.touch()

        expected_result = RipResult(
            title_index=0, output_path=rip_path, success=True
        )

        with patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv:
            mock_makemkv.return_value.rip_title.return_value = expected_result
            pipeline.rip()

        assert len(pipeline.rip_results) == 1
        assert pipeline.rip_results[0].success
        mock_makemkv.return_value.rip_title.assert_called_once_with(
            0, 0, tmp_path / "output" / ".tmp", sample_config.min_length, None
        )

    def test_rips_extras_when_present_in_selection(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_title = make_title(index=1, duration="00:15:00", title_type="extra")

        classified = ClassifiedExtra(
            title=extra_title, extra_type="extra", output_filename="Extra 1-extra.mkv"
        )

        pipeline = Pipeline(sample_config)
        pipeline.disc_info = DiscInfo(
            drive=drive, disc_title="TEST", titles=[main, extra_title]
        )
        pipeline.selection = Selection(main=main, extras=[classified])

        def _fake_rip(drive_index, title_index, output_dir, min_length, on_progress):
            fake_path = output_dir / f"title_t0{title_index}.mkv"
            fake_path.touch()
            return RipResult(
                title_index=title_index, output_path=fake_path, success=True
            )

        with patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv:
            mock_makemkv.return_value.rip_title.side_effect = _fake_rip
            pipeline.rip()

        assert len(pipeline.rip_results) == 2
        title_indices = {result.title_index for result in pipeline.rip_results}
        assert title_indices == {0, 1}

    def test_per_title_rip_error_does_not_abort_remaining_titles(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_title = make_title(index=1, duration="00:15:00", title_type="extra")
        classified = ClassifiedExtra(
            title=extra_title, extra_type="extra", output_filename="Extra 1-extra.mkv"
        )

        pipeline = Pipeline(sample_config)
        pipeline.disc_info = DiscInfo(
            drive=drive, disc_title="TEST", titles=[main, extra_title]
        )
        pipeline.selection = Selection(main=main, extras=[classified])

        success_path = tmp_path / "output" / ".tmp" / "title_t01.mkv"
        success_path.parent.mkdir(parents=True, exist_ok=True)
        success_path.touch()
        success_result = RipResult(
            title_index=1, output_path=success_path, success=True
        )

        with patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv:
            mock_makemkv.return_value.rip_title.side_effect = [
                RipError(["makemkvcon"], 1, "main title rip failed"),
                success_result,
            ]
            pipeline.rip()

        assert len(pipeline.rip_results) == 2
        failed = next(res for res in pipeline.rip_results if res.title_index == 0)
        succeeded = next(res for res in pipeline.rip_results if res.title_index == 1)
        assert not failed.success
        assert succeeded.success

    def test_returns_list_of_rip_results(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title()

        pipeline = Pipeline(sample_config)
        pipeline.disc_info = DiscInfo(drive=drive, disc_title="TEST", titles=[main])
        pipeline.selection = Selection(main=main, extras=[])

        fake_result = RipResult(title_index=0, output_path=None, success=False)

        with patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv:
            mock_makemkv.return_value.rip_title.return_value = fake_result
            returned = pipeline.rip()

        assert isinstance(returned, list)
        assert returned is pipeline.rip_results
        assert all(isinstance(result, RipResult) for result in returned)


# ---------------------------------------------------------------------------
# Stage 3: Pipeline.encode (optional)
# ---------------------------------------------------------------------------

class TestEncodeStage:
    def test_skipped_when_encode_format_is_none(
        self, sample_config: Config, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "none"

        pipeline = Pipeline(sample_config)
        pipeline.rip_results = [
            RipResult(
                title_index=0,
                output_path=tmp_path / "title_t00.mkv",
                success=True,
            )
        ]
        result = pipeline.encode()
        assert result == []
        assert pipeline.encode_results == []

    def test_skipped_when_handbrake_not_installed(
        self, sample_config: Config, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "h265"

        pipeline = Pipeline(sample_config)
        pipeline.rip_results = [
            RipResult(
                title_index=0,
                output_path=tmp_path / "title_t00.mkv",
                success=True,
            )
        ]
        with patch("diskripr.pipeline.HandBrakeDriver") as mock_hb:
            mock_hb.return_value.is_available.return_value = False
            result = pipeline.encode()

        assert result == []

    def test_encode_failure_keeps_original_mkv(
        self, sample_config: Config, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "h264"
        sample_config.temp_dir = tmp_path

        original = tmp_path / ".tmp" / "title_t00.mkv"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text("original content")

        pipeline = Pipeline(sample_config)
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=original, success=True)
        ]

        with patch("diskripr.pipeline.HandBrakeDriver") as mock_hb:
            mock_hb.return_value.is_available.return_value = True
            mock_hb.return_value.encode.side_effect = EncodeError(
                ["HandBrakeCLI"], 1, "encoding failed"
            )
            pipeline.encode()

        assert len(pipeline.encode_results) == 1
        assert not pipeline.encode_results[0].success
        assert original.exists(), "original MKV must still exist after encode failure"

    def test_keep_original_flag_moves_source_to_originals_subdir(
        self, sample_config: Config, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "h265"
        sample_config.keep_original = True
        sample_config.temp_dir = tmp_path

        original = tmp_path / ".tmp" / "title_t00.mkv"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text("original content")

        pipeline = Pipeline(sample_config)
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=original, success=True)
        ]

        encoded_path = tmp_path / ".tmp" / "title_t00_encoded.mkv"
        encode_result = EncodeResult(
            title_index=0,
            output_path=encoded_path,
            success=True,
            original_size_bytes=1000,
            encoded_size_bytes=800,
        )

        with patch("diskripr.pipeline.HandBrakeDriver") as mock_hb:
            mock_hb.return_value.is_available.return_value = True
            mock_hb.return_value.encode.return_value = encode_result
            pipeline.encode()

        originals_dir = tmp_path / ".tmp" / "originals"
        assert originals_dir.is_dir()
        assert (originals_dir / "title_t00.mkv").exists()
        assert len(pipeline.encode_results) == 1
        assert pipeline.encode_results[0].success


# ---------------------------------------------------------------------------
# Stage 4: Pipeline.organize
# ---------------------------------------------------------------------------

class TestOrganizeStage:
    def _make_rip_file(self, tmp_path: Path, name: str) -> Path:
        """Create a fake ripped MKV in a temp subdir and return its path."""
        temp_dir = tmp_path / ".tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        fpath = temp_dir / name
        fpath.write_text("fake mkv")
        return fpath

    def test_main_feature_placed_at_correct_jellyfin_path(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        main = make_title()

        pipeline = Pipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        pipeline.organize()

        expected = (
            tmp_path
            / "output"
            / "Movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "Rosencrantz And Guildenstern Are Dead (1990).mkv"
        )
        assert expected in pipeline.output_paths
        assert expected.exists()

    def test_extras_placed_in_extras_subdir(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        extra_file = self._make_rip_file(tmp_path, "title_t01.mkv")

        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_title = make_title(index=1, duration="00:15:00", title_type="extra")
        classified = ClassifiedExtra(
            title=extra_title, extra_type="extra", output_filename="Extra 1-extra.mkv"
        )

        pipeline = Pipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[classified])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True),
            RipResult(title_index=1, output_path=extra_file, success=True),
        ]

        pipeline.organize()

        extras_subdir = (
            tmp_path
            / "output"
            / "Movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "extras"
            / "Extra 1-extra.mkv"
        )
        assert extras_subdir in pipeline.output_paths
        assert extras_subdir.exists()

    def test_multi_disc_main_feature_gets_part_suffix(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path
        sample_config.disc_number = 2

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        main = make_title()

        pipeline = Pipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        pipeline.organize()

        expected = (
            tmp_path
            / "output"
            / "Movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "Rosencrantz And Guildenstern Are Dead (1990) - Part2.mkv"
        )
        assert expected in pipeline.output_paths
        assert expected.exists()

    def test_warns_when_single_disc_movie_dir_already_exists(
        self,
        sample_config: Config,
        make_title: Callable[..., Title],
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        movie_dir = (
            tmp_path
            / "output"
            / "Movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
        )
        movie_dir.mkdir(parents=True, exist_ok=True)
        (movie_dir / "Rosencrantz And Guildenstern Are Dead (1990).mkv").touch()

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        main = make_title()

        pipeline = Pipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        with caplog.at_level(logging.WARNING, logger="diskripr.pipeline"):
            with pytest.raises(FileExistsError):
                pipeline.organize()

        assert any(
            "already contains files" in record.message for record in caplog.records
        )

    def test_multi_disc_adds_files_alongside_existing_without_warning(
        self,
        sample_config: Config,
        make_title: Callable[..., Title],
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path
        sample_config.disc_number = 2

        movie_dir = (
            tmp_path
            / "output"
            / "Movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
        )
        movie_dir.mkdir(parents=True, exist_ok=True)
        (movie_dir / "Rosencrantz And Guildenstern Are Dead (1990) - Part1.mkv").touch()

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        main = make_title()

        pipeline = Pipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        with caplog.at_level(logging.WARNING, logger="diskripr.pipeline"):
            pipeline.organize()

        assert not any(
            "already contains files" in record.message for record in caplog.records
        )

    def test_temp_dir_cleaned_up_after_organize(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        temp_dir = tmp_path / ".tmp"
        assert temp_dir.is_dir()

        main = make_title()

        pipeline = Pipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        pipeline.organize()

        assert not temp_dir.exists()


# ---------------------------------------------------------------------------
# Full Pipeline.run()
# ---------------------------------------------------------------------------

class TestPipelineRun:
    def _setup_drivers(
        self,
        mock_lsdvd: MagicMock,
        mock_makemkv: MagicMock,
        mock_hb: MagicMock,
        mock_ffprobe: MagicMock,
        *,
        device: str,
        titles: list[Title],
        rip_side_effect: Callable,
    ) -> None:
        """Wire up common driver mocks for a full pipeline run."""
        drive = DriveInfo(device=device, drive_index=0)

        mock_lsdvd.return_value.read_disc.return_value = None
        mock_makemkv.return_value.scan_drives.return_value = [drive]
        mock_makemkv.return_value.scan_titles.return_value = titles
        mock_makemkv.return_value.rip_title.side_effect = rip_side_effect
        mock_hb.return_value.is_available.return_value = False
        mock_ffprobe.return_value.is_available.return_value = False

    def test_full_pipeline_main_only_no_encode(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"
        sample_config.rip_mode = "main"
        sample_config.encode_format = "none"
        sample_config.eject_on_complete = False

        main = make_title()

        def _fake_rip(drive_index, title_index, output_dir, min_length, on_progress):
            rip_path = output_dir / "title_t00.mkv"
            rip_path.touch()
            return RipResult(title_index=0, output_path=rip_path, success=True)

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
            patch("diskripr.pipeline.HandBrakeDriver") as mock_hb,
            patch("diskripr.pipeline.FfprobeDriver") as mock_ffprobe,
        ):
            self._setup_drivers(
                mock_lsdvd,
                mock_makemkv,
                mock_hb,
                mock_ffprobe,
                device=str(fake_device),
                titles=[main],
                rip_side_effect=_fake_rip,
            )
            output_paths = pipeline.run()

        assert len(output_paths) == 1
        assert output_paths[0].suffix == ".mkv"
        assert output_paths[0].exists()
        assert output_paths is pipeline.output_paths

    def test_full_pipeline_with_encode(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"
        sample_config.rip_mode = "main"
        sample_config.encode_format = "h265"
        sample_config.eject_on_complete = False

        main = make_title()

        def _fake_rip(drive_index, title_index, output_dir, min_length, on_progress):
            rip_path = output_dir / "title_t00.mkv"
            rip_path.touch()
            return RipResult(title_index=0, output_path=rip_path, success=True)

        def _fake_encode(
            title_index, input_path, output_path, encoder, quality, on_progress=None
        ):
            output_path.touch()
            return EncodeResult(
                title_index=title_index,
                output_path=output_path,
                success=True,
                original_size_bytes=1000,
                encoded_size_bytes=700,
            )

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
            patch("diskripr.pipeline.HandBrakeDriver") as mock_hb,
            patch("diskripr.pipeline.FfprobeDriver") as mock_ffprobe,
        ):
            self._setup_drivers(
                mock_lsdvd,
                mock_makemkv,
                mock_hb,
                mock_ffprobe,
                device=str(fake_device),
                titles=[main],
                rip_side_effect=_fake_rip,
            )
            mock_hb.return_value.is_available.return_value = True
            mock_hb.return_value.encode.side_effect = _fake_encode

            output_paths = pipeline.run()

        assert len(output_paths) == 1
        assert output_paths[0].exists()
        mock_hb.return_value.encode.assert_called_once()

    def test_full_pipeline_all_mode_with_extras(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"
        sample_config.rip_mode = "all"
        sample_config.encode_format = "none"
        sample_config.eject_on_complete = False

        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra = make_title(index=1, duration="00:15:00", title_type="extra")

        def _fake_rip(drive_index, title_index, output_dir, min_length, on_progress):
            rip_path = output_dir / f"title_t0{title_index}.mkv"
            rip_path.touch()
            return RipResult(
                title_index=title_index, output_path=rip_path, success=True
            )

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
            patch("diskripr.pipeline.HandBrakeDriver") as mock_hb,
            patch("diskripr.pipeline.FfprobeDriver") as mock_ffprobe,
        ):
            self._setup_drivers(
                mock_lsdvd,
                mock_makemkv,
                mock_hb,
                mock_ffprobe,
                device=str(fake_device),
                titles=[main, extra],
                rip_side_effect=_fake_rip,
            )
            output_paths = pipeline.run()

        assert len(output_paths) == 2
        names = [path.name for path in output_paths]
        assert any("Extra" not in name for name in names), "expected a main feature file"
        assert any("Extra" in name for name in names), "expected an extra file"

    def test_multi_disc_second_run_merges_extras_without_collision(
        self, sample_config: Config, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"
        sample_config.rip_mode = "all"
        sample_config.encode_format = "none"
        sample_config.eject_on_complete = False
        sample_config.disc_number = 2

        extras_dir = (
            tmp_path
            / "output"
            / "Movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "extras"
        )
        extras_dir.mkdir(parents=True, exist_ok=True)
        (extras_dir / "Extra 1-extra.mkv").touch()

        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra = make_title(index=1, duration="00:15:00", title_type="extra")

        def _fake_rip(drive_index, title_index, output_dir, min_length, on_progress):
            rip_path = output_dir / f"title_t0{title_index}.mkv"
            rip_path.touch()
            return RipResult(
                title_index=title_index, output_path=rip_path, success=True
            )

        pipeline = Pipeline(sample_config)
        with (
            patch("diskripr.pipeline.LsdvdDriver") as mock_lsdvd,
            patch("diskripr.pipeline.MakeMKVDriver") as mock_makemkv,
            patch("diskripr.pipeline.HandBrakeDriver") as mock_hb,
            patch("diskripr.pipeline.FfprobeDriver") as mock_ffprobe,
        ):
            self._setup_drivers(
                mock_lsdvd,
                mock_makemkv,
                mock_hb,
                mock_ffprobe,
                device=str(fake_device),
                titles=[main, extra],
                rip_side_effect=_fake_rip,
            )
            output_paths = pipeline.run()

        extra_paths = [path for path in output_paths if "Extra" in path.name]
        assert len(extra_paths) == 1
        assert extra_paths[0].name == "Extra 2-extra.mkv"
