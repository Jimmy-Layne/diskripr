"""Tests for ``diskripr.pipeline`` — MoviePipeline, ShowPipeline, and helpers.

Each stage has its own test class.  Driver dependencies are mocked at the
pipeline module level (``diskripr.pipeline.<DriverClass>``) so no physical
disc drive is required.

Stage state attributes are set directly on instances (e.g.
``pipeline.disc_info``, ``pipeline.selection``) before calling the method
under test, matching the expected CLI usage pattern.

Test organisation:
1. BasePipeline.discover()  — drive detection + title scan
2. _assemble_signals()      — signal assembly, graceful degradation
3. _select()                — title selection logic (main / all modes)
4. _classify()              — Jellyfin extra type assignment with heuristics
5. BasePipeline.rip()       — MakeMKV title extraction
6. BasePipeline.encode()    — HandBrake re-encoding (optional stage)
7. MoviePipeline.organize() — Jellyfin movie directory tree construction
8. MoviePipeline.run()      — full movie pipeline integration path
9. ShowPipeline             — episode clustering, numbering, TV organization
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest

from diskripr.config import MovieConfig, ShowConfig
from diskripr.drivers.base import EncodeError, RipError, ToolNotFound
from diskripr.drivers.ifo import IfoPgc, IfoVts
from diskripr.drivers.lsdvd import LsdvdTitle
from diskripr.models import (
    ClassifiedExtra,
    DiscInfo,
    DriveInfo,
    EncodeResult,
    EpisodeEntry,
    RipResult,
    Selection,
    ShowSelection,
    Title,
)
from diskripr.pipeline import (
    MoviePipeline,
    ShowPipeline,
    _assemble_signals,
    _classify,
    _select,
)


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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)

        drive = _make_drive(device=str(fake_device))
        title = make_title()

        pipeline = MoviePipeline(sample_config)
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

    def test_fails_when_device_not_found(self, sample_config: MovieConfig) -> None:
        sample_config.device = "/dev/nonexistent_sr99"
        pipeline = MoviePipeline(sample_config)
        with pytest.raises(RuntimeError, match="Device not found"):
            pipeline.discover()

    def test_lsdvd_failure_is_non_fatal(
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)

        drive = _make_drive(device=str(fake_device))
        title = make_title()

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)

        drive_zero = DriveInfo(device="/dev/sr1", drive_index=0)
        title = make_title()

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.min_length = 9999

        drive = _make_drive(device=str(fake_device))
        short_title = make_title(duration="00:00:05", title_type="short")

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        fake_device = tmp_path / "sr0"
        fake_device.touch()
        sample_config.device = str(fake_device)
        sample_config.min_length = 60

        drive = _make_drive(device=str(fake_device))
        long_title = make_title(duration="01:57:00", title_type="main")
        short_title = make_title(index=1, duration="00:00:30", title_type="short")

        pipeline = MoviePipeline(sample_config)
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

        movie_dir = tmp_path / "movie"
        selection = _classify(main, [extra], movie_dir)

        assert len(selection.extras) == 1
        assert selection.extras[0].extra_type == "extra"

    def test_auto_numbers_extras_of_same_type(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        # Generic names (Title_NN) trigger counter-based filenames.
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_a = make_title(index=1, duration="00:15:00", name="Title_01", title_type="extra")
        extra_b = make_title(index=2, duration="00:12:00", name="Title_02", title_type="extra")

        movie_dir = tmp_path / "movie"
        selection = _classify(main, [extra_a, extra_b], movie_dir)

        filenames = [classified.output_filename for classified in selection.extras]
        assert filenames[0] == "Extra 1.mkv"
        assert filenames[1] == "Extra 2.mkv"

    def test_numbering_continues_from_existing_extras_in_multi_disc(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        # Pre-seed an existing extras subdir so scan_existing_extras finds counter 2.
        # Use a generic name so the counter-based filename path is exercised.
        extras_subdir = tmp_path / "movie" / "extras"
        extras_subdir.mkdir(parents=True)
        (extras_subdir / "Extra 2.mkv").touch()

        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra = make_title(index=1, duration="00:15:00", name="Title_01", title_type="extra")

        selection = _classify(main, [extra], tmp_path / "movie")

        assert selection.extras[0].output_filename == "Extra 3.mkv"

    def test_output_filename_matches_jellyfin_format(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        # Generic title name → counter-based filename containing the type label.
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra = make_title(index=1, duration="00:15:00", name="Title_01", title_type="extra")

        movie_dir = tmp_path / "movie"
        selection = _classify(main, [extra], movie_dir)

        filename = selection.extras[0].output_filename
        assert filename.endswith(".mkv")
        assert "Extra" in filename


# ---------------------------------------------------------------------------
# Stage 2: Pipeline.rip
# ---------------------------------------------------------------------------

class TestRipStage:
    def test_rips_main_title_to_temp_dir(
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title()

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_title = make_title(index=1, duration="00:15:00", title_type="extra")

        classified = ClassifiedExtra(
            title=extra_title, extra_type="extra", output_filename="Extra 1.mkv"
        )

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_title = make_title(index=1, duration="00:15:00", title_type="extra")
        classified = ClassifiedExtra(
            title=extra_title, extra_type="extra", output_filename="Extra 1.mkv"
        )

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path / "output"

        drive = _make_drive()
        main = make_title()

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "none"

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "h265"

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "h264"
        sample_config.temp_dir = tmp_path

        original = tmp_path / ".tmp" / "title_t00.mkv"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text("original content")

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, tmp_path: Path
    ) -> None:
        sample_config.encode_format = "h265"
        sample_config.keep_original = True
        sample_config.temp_dir = tmp_path

        original = tmp_path / ".tmp" / "title_t00.mkv"
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_text("original content")

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        main = make_title()

        pipeline = MoviePipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        pipeline.organize()

        expected = (
            tmp_path
            / "output"
            / "movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "Rosencrantz And Guildenstern Are Dead (1990).mkv"
        )
        assert expected in pipeline.output_paths
        assert expected.exists()

    def test_extras_placed_in_extras_subdir(
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        extra_file = self._make_rip_file(tmp_path, "title_t01.mkv")

        main = make_title(index=0, duration="01:57:00", title_type="main")
        extra_title = make_title(index=1, duration="00:15:00", title_type="extra")
        classified = ClassifiedExtra(
            title=extra_title, extra_type="extra", output_filename="Extra 1.mkv"
        )

        pipeline = MoviePipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[classified])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True),
            RipResult(title_index=1, output_path=extra_file, success=True),
        ]

        pipeline.organize()

        extras_subdir = (
            tmp_path
            / "output"
            / "movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "extras"
            / "Extra 1.mkv"
        )
        assert extras_subdir in pipeline.output_paths
        assert extras_subdir.exists()

    def test_multi_disc_main_feature_gets_part_suffix(
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path
        sample_config.disc_number = 2

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        main = make_title()

        pipeline = MoviePipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        pipeline.organize()

        expected = (
            tmp_path
            / "output"
            / "movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "Rosencrantz And Guildenstern Are Dead (1990) - Part2.mkv"
        )
        assert expected in pipeline.output_paths
        assert expected.exists()

    def test_warns_when_single_disc_movie_dir_already_exists(
        self,
        sample_config: MovieConfig,
        make_title: Callable[..., Title],
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        movie_dir = (
            tmp_path
            / "output"
            / "movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
        )
        movie_dir.mkdir(parents=True, exist_ok=True)
        (movie_dir / "existing_extra.mkv").touch()

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        main = make_title()

        pipeline = MoviePipeline(sample_config)
        pipeline.selection = Selection(main=main, extras=[])
        pipeline.rip_results = [
            RipResult(title_index=0, output_path=main_file, success=True)
        ]

        with caplog.at_level(logging.WARNING, logger="diskripr.pipeline"):
            pipeline.organize()

        assert any(
            "already contains files" in record.message for record in caplog.records
        )

    def test_multi_disc_adds_files_alongside_existing_without_warning(
        self,
        sample_config: MovieConfig,
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

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        sample_config.output_dir = tmp_path / "output"
        sample_config.temp_dir = tmp_path

        main_file = self._make_rip_file(tmp_path, "title_t00.mkv")
        temp_dir = tmp_path / ".tmp"
        assert temp_dir.is_dir()

        main = make_title()

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
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

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
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

        pipeline = MoviePipeline(sample_config)
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
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
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
        # Generic name → counter-based extra filename in the extras/ subdir.
        extra = make_title(index=1, duration="00:15:00", name="Title_01", title_type="extra")

        def _fake_rip(drive_index, title_index, output_dir, min_length, on_progress):
            rip_path = output_dir / f"title_t0{title_index}.mkv"
            rip_path.touch()
            return RipResult(
                title_index=title_index, output_path=rip_path, success=True
            )

        pipeline = MoviePipeline(sample_config)
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
        # Main feature is directly in the movie dir; extra goes into a type subdir.
        movie_dir_paths = [
            path for path in output_paths if path.parent.name.endswith(")")
        ]
        extra_subdir_paths = [
            path for path in output_paths
            if not path.parent.name.endswith(")")
        ]
        assert len(movie_dir_paths) == 1, "expected one main feature file"
        assert len(extra_subdir_paths) == 1, "expected one extra in a type subdir"

    def test_multi_disc_second_run_merges_extras_without_collision(
        self, sample_config: MovieConfig, make_title: Callable[..., Title], tmp_path: Path
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
            / "movies"
            / "Rosencrantz And Guildenstern Are Dead (1990)"
            / "extras"
        )
        extras_dir.mkdir(parents=True, exist_ok=True)
        (extras_dir / "Extra 1.mkv").touch()

        main = make_title(index=0, duration="01:57:00", title_type="main")
        # Generic name so counter-based "Extra N.mkv" naming is used.
        extra = make_title(index=1, duration="00:15:00", name="Title_01", title_type="extra")

        def _fake_rip(drive_index, title_index, output_dir, min_length, on_progress):
            rip_path = output_dir / f"title_t0{title_index}.mkv"
            rip_path.touch()
            return RipResult(
                title_index=title_index, output_path=rip_path, success=True
            )

        pipeline = MoviePipeline(sample_config)
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
        assert extra_paths[0].name == "Extra 2.mkv"


# ---------------------------------------------------------------------------
# _assemble_signals() — signal assembly and graceful degradation
# ---------------------------------------------------------------------------

class TestAssembleSignals:
    def _make_title(
        self,
        index: int = 0,
        duration: str = "01:00:00",
        chapter_count: int = 10,
    ) -> Title:
        return Title(
            index=index,
            name="Test Title",
            duration=duration,
            size_bytes=1_000_000,
            chapter_count=chapter_count,
            stream_summary="",
            title_type="main",
        )

    def _make_lsdvd_title(
        self,
        vts: int = 1,
        ttn: int = 1,
        audio: int = 2,
        cells: int = 12,
        duration: str = "01:00:00",
    ) -> LsdvdTitle:
        return LsdvdTitle(
            index=1,
            duration=duration,
            vts_number=vts,
            ttn=ttn,
            audio_stream_count=audio,
            cell_count=cells,
        )

    def test_lsdvd_absent_leaves_fields_none(self) -> None:
        title = self._make_title()
        signals = _assemble_signals(title, None, None, None)
        assert signals.vts_number is None
        assert signals.ttn is None
        assert signals.audio_stream_count is None
        assert signals.cell_count is None

    def test_ifo_absent_leaves_pgc_and_cell_fields_none(self) -> None:
        title = self._make_title()
        lt = self._make_lsdvd_title()
        signals = _assemble_signals(title, lt, None, reference_vts=1)
        assert signals.pgc_count_in_vts is None
        assert signals.cell_durations is None

    def test_lsdvd_fields_populated_when_present(self) -> None:
        title = self._make_title()
        lt = self._make_lsdvd_title(vts=2, ttn=3, audio=4, cells=8)
        signals = _assemble_signals(title, lt, None, reference_vts=1)
        assert signals.vts_number == 2
        assert signals.ttn == 3
        assert signals.audio_stream_count == 4
        assert signals.cell_count == 8

    def test_reference_vts_propagated(self) -> None:
        title = self._make_title()
        signals = _assemble_signals(title, None, None, reference_vts=3)
        assert signals.reference_vts == 3

    def test_cell_durations_populated_from_nearest_pgc(self) -> None:
        title = self._make_title(duration="01:00:00")  # 3600 seconds
        lt = self._make_lsdvd_title()
        pgc_near = IfoPgc(
            duration_seconds=3602,  # within 5s tolerance
            nb_program=5,
            cell_durations=[300, 400, 500, 600, 700, 800, 300],
        )
        pgc_far = IfoPgc(
            duration_seconds=1800,  # too far away
            nb_program=3,
            cell_durations=[200, 300, 400],
        )
        ifo_vts = IfoVts(vts_index=1, pgc_count=2, pgcs=[pgc_near, pgc_far])
        signals = _assemble_signals(title, lt, ifo_vts, reference_vts=1)
        assert signals.cell_durations == pgc_near.cell_durations

    def test_cell_durations_none_when_no_pgc_within_tolerance(self) -> None:
        title = self._make_title(duration="01:00:00")  # 3600 seconds
        lt = self._make_lsdvd_title()
        pgc_far = IfoPgc(
            duration_seconds=1800,  # 1800s away — exceeds 5s tolerance
            nb_program=3,
            cell_durations=[200, 300, 400],
        )
        ifo_vts = IfoVts(vts_index=1, pgc_count=1, pgcs=[pgc_far])
        signals = _assemble_signals(title, lt, ifo_vts, reference_vts=1)
        assert signals.cell_durations is None

    def test_segment_count_and_map_from_title(self) -> None:
        title = Title(
            index=0,
            name="t01",
            duration="00:30:00",
            size_bytes=500_000,
            chapter_count=3,
            stream_summary="",
            title_type="extra",
            segment_count=4,
            segments_map="0,1,2,3",
        )
        signals = _assemble_signals(title, None, None, None)
        assert signals.segment_count == 4
        assert signals.segments_map == "0,1,2,3"


# ---------------------------------------------------------------------------
# ShowPipeline — episode clustering, numbering, and TV organization
# ---------------------------------------------------------------------------

def _make_show_config(tmp_path: Path, **overrides: object) -> ShowConfig:
    defaults: dict[str, object] = {
        "show_name": "The Test Show",
        "season_number": 1,
        "start_episode": 1,
        "output_dir": tmp_path / "output",
        "temp_dir": tmp_path / "output",
        "eject_on_complete": False,
        "encode_format": "none",
    }
    defaults.update(overrides)
    return ShowConfig(**defaults)  # type: ignore[arg-type]


def _make_show_title(
    make_title: Callable[..., Title],
    index: int,
    duration: str = "00:45:00",
    name: str = "Title_01",
) -> Title:
    return make_title(index=index, duration=duration, name=name, title_type="main")


class TestShowPipelineEpisodeNumbering:
    def test_episode_numbers_start_from_start_episode(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """Episodes are numbered consecutively from config.start_episode."""
        cfg = _make_show_config(tmp_path, start_episode=3)
        pipeline = ShowPipeline(cfg)

        ep1 = _make_show_title(make_title, 0, duration="00:45:00")
        ep2 = _make_show_title(make_title, 1, duration="00:45:00")
        pipeline.disc_info = DiscInfo(
            drive=DriveInfo(device="/dev/sr0", drive_index=0),
            disc_title="TEST",
            titles=[ep1, ep2],
        )

        selection = pipeline._build_show_selection({})

        episode_numbers = [entry.episode_number for entry in selection.episodes]
        assert episode_numbers == [3, 4]

    def test_episode_season_number_matches_config(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        cfg = _make_show_config(tmp_path, season_number=2, start_episode=1)
        pipeline = ShowPipeline(cfg)

        ep1 = _make_show_title(make_title, 0)
        pipeline.disc_info = DiscInfo(
            drive=DriveInfo(device="/dev/sr0", drive_index=0),
            disc_title="TEST",
            titles=[ep1],
        )

        selection = pipeline._build_show_selection({})

        assert selection.episodes[0].season_number == 2

    def test_single_title_becomes_single_episode(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        cfg = _make_show_config(tmp_path)
        pipeline = ShowPipeline(cfg)

        ep = _make_show_title(make_title, 0)
        pipeline.disc_info = DiscInfo(
            drive=DriveInfo(device="/dev/sr0", drive_index=0),
            disc_title="TEST",
            titles=[ep],
        )

        selection = pipeline._build_show_selection({})

        assert len(selection.episodes) == 1
        assert selection.episodes[0].episode_number == 1


class TestShowPipelineExtrasClassification:
    def test_extras_classified_via_heuristics_not_hardcoded(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """Short single-chapter titles are classified by heuristics, not hardcoded 'extra'."""
        cfg = _make_show_config(tmp_path)
        pipeline = ShowPipeline(cfg)

        # Three similar-duration episodes
        ep1 = _make_show_title(make_title, 0, duration="00:45:00")
        ep2 = _make_show_title(make_title, 1, duration="00:44:30")
        ep3 = _make_show_title(make_title, 2, duration="00:46:00")
        # One very short title with a trailer keyword → heuristics gives 'trailer'
        trailer = make_title(
            index=3,
            duration="00:01:30",
            name="Theatrical Trailer",
            title_type="extra",
        )
        pipeline.disc_info = DiscInfo(
            drive=DriveInfo(device="/dev/sr0", drive_index=0),
            disc_title="TEST",
            titles=[ep1, ep2, ep3, trailer],
        )

        selection = pipeline._build_show_selection({})

        assert len(selection.episodes) == 3
        assert len(selection.extras) == 1
        assert selection.extras[0].extra_type == "trailer"

    def test_extras_use_display_name_when_descriptive(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """Non-generic title names appear in the extra filename."""
        cfg = _make_show_config(tmp_path)
        pipeline = ShowPipeline(cfg)

        ep1 = _make_show_title(make_title, 0)
        ep2 = _make_show_title(make_title, 1, duration="00:44:00")
        ep3 = _make_show_title(make_title, 2, duration="00:46:00")
        deleted_scene = make_title(
            index=3,
            duration="00:01:00",
            name="Deleted Scene",
            chapter_count=1,
            title_type="extra",
        )
        pipeline.disc_info = DiscInfo(
            drive=DriveInfo(device="/dev/sr0", drive_index=0),
            disc_title="TEST",
            titles=[ep1, ep2, ep3, deleted_scene],
        )

        selection = pipeline._build_show_selection({})

        assert len(selection.extras) == 1
        # "Deleted Scene" is a descriptive name → used directly as filename stem
        assert selection.extras[0].output_filename == "Deleted Scene.mkv"


class TestShowPipelineOrganize:
    def _make_rip_file(self, base_dir: Path, name: str) -> Path:
        path = base_dir / name
        path.touch()
        return path

    def test_episodes_placed_in_season_directory(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        cfg = _make_show_config(tmp_path, season_number=1, start_episode=1)
        pipeline = ShowPipeline(cfg)

        ep_file = self._make_rip_file(tmp_path, "ep.mkv")
        ep_title = _make_show_title(make_title, 0)

        pipeline.rip_results = [
            RipResult(title_index=0, output_path=ep_file, success=True)
        ]
        pipeline.selection = ShowSelection(
            episodes=[EpisodeEntry(title=ep_title, season_number=1, episode_number=1)],
            extras=[],
        )

        pipeline.organize()

        expected = (
            tmp_path
            / "output"
            / "Shows"
            / "The Test Show"
            / "Season 01"
            / "The Test Show S01E01.mkv"
        )
        assert expected in pipeline.output_paths
        assert expected.exists()

    def test_episode_with_title_uses_title_in_filename(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        cfg = _make_show_config(tmp_path, season_number=2, start_episode=5)
        pipeline = ShowPipeline(cfg)

        ep_file = self._make_rip_file(tmp_path, "ep.mkv")
        ep_title = _make_show_title(make_title, 0)

        pipeline.rip_results = [
            RipResult(title_index=0, output_path=ep_file, success=True)
        ]
        pipeline.selection = ShowSelection(
            episodes=[
                EpisodeEntry(
                    title=ep_title,
                    season_number=2,
                    episode_number=5,
                    episode_title="Pilot",
                )
            ],
            extras=[],
        )

        pipeline.organize()

        expected = (
            tmp_path
            / "output"
            / "Shows"
            / "The Test Show"
            / "Season 02"
            / "The Test Show S02E05 - Pilot.mkv"
        )
        assert expected in pipeline.output_paths
        assert expected.exists()

    def test_extras_routed_to_type_subdirectory(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        cfg = _make_show_config(tmp_path)
        pipeline = ShowPipeline(cfg)

        ep_file = self._make_rip_file(tmp_path, "ep.mkv")
        extra_file = self._make_rip_file(tmp_path, "extra.mkv")

        ep_title = _make_show_title(make_title, 0)
        extra_title = make_title(index=1, duration="00:10:00", title_type="extra")

        pipeline.rip_results = [
            RipResult(title_index=0, output_path=ep_file, success=True),
            RipResult(title_index=1, output_path=extra_file, success=True),
        ]
        pipeline.selection = ShowSelection(
            episodes=[
                EpisodeEntry(title=ep_title, season_number=1, episode_number=1)
            ],
            extras=[
                ClassifiedExtra(
                    title=extra_title,
                    extra_type="featurette",
                    output_filename="Featurette 1.mkv",
                )
            ],
        )

        pipeline.organize()

        extra_dest = (
            tmp_path
            / "output"
            / "Shows"
            / "The Test Show"
            / "Season 01"
            / "featurettes"
            / "Featurette 1.mkv"
        )
        assert extra_dest in pipeline.output_paths
        assert extra_dest.exists()

    def test_season_zero_maps_to_season_00_directory(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        cfg = _make_show_config(tmp_path, season_number=0, start_episode=1)
        pipeline = ShowPipeline(cfg)

        ep_file = self._make_rip_file(tmp_path, "ep.mkv")
        ep_title = _make_show_title(make_title, 0)

        pipeline.rip_results = [
            RipResult(title_index=0, output_path=ep_file, success=True)
        ]
        pipeline.selection = ShowSelection(
            episodes=[EpisodeEntry(title=ep_title, season_number=0, episode_number=1)],
            extras=[],
        )

        pipeline.organize()

        expected = (
            tmp_path
            / "output"
            / "Shows"
            / "The Test Show"
            / "Season 00"
            / "The Test Show S00E01.mkv"
        )
        assert expected.exists()

    def test_organize_raises_when_selection_not_set(self, tmp_path: Path) -> None:
        cfg = _make_show_config(tmp_path)
        pipeline = ShowPipeline(cfg)
        pipeline.rip_results = []
        with pytest.raises(RuntimeError, match="selection is not set"):
            pipeline.organize()
