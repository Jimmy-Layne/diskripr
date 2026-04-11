"""Tests for ``diskripr.cli`` — movie and show command groups.

Uses Click's ``CliRunner`` for invocation.  Pipeline classes and drivers are
mocked at the ``diskripr.cli`` module boundary so no physical disc drive is
required.

Test organisation:
1. diskripr movie scan   — disc listing, --output-json, --append
2. diskripr movie rip    — config construction, pipeline invocation
3. diskripr movie organize — temp-dir scan, organize dispatch
4. diskripr show scan    — disc listing, episode cluster, --output-json
5. diskripr show rip     — ShowPipeline invocation with correct ShowConfig
6. diskripr show organize — temp-dir scan, episode number assignment
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from diskripr.cli import cli
from diskripr.config import MovieConfig, ShowConfig
from diskripr.models import (
    DiscInfo,
    DriveInfo,
    Selection,
    Title,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def skip_dep_checks():
    """Bypass makemkvcon / lsdvd availability checks for all CLI tests.

    Also suppresses _configure_logging() so the diskripr package logger is not
    mutated (propagate=False, extra handlers) during test runs, which would
    break caplog capture in test_pipeline.py when tests run in suite order.
    """
    with patch("diskripr.cli._configure_logging"):
        with patch("diskripr.cli._check_required_deps"):
            with patch("diskripr.cli.check_available", return_value=True):
                yield


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_drive(device: str = "/dev/sr0") -> DriveInfo:
    return DriveInfo(device=device, drive_index=0)


def _make_disc(titles: list[Title], device: str = "/dev/sr0") -> DiscInfo:
    return DiscInfo(
        drive=_make_drive(device),
        disc_title="TEST DISC",
        titles=titles,
    )


def _make_title(
    make_title_factory: Callable[..., Title],
    index: int = 0,
    duration: str = "01:57:25",
    name: str = "Test Title",
    title_type: str = "main",
) -> Title:
    return make_title_factory(
        index=index,
        duration=duration,
        name=name,
        title_type=title_type,
    )


# ---------------------------------------------------------------------------
# 1. diskripr movie scan
# ---------------------------------------------------------------------------


class TestMovieScan:
    def test_displays_disc_info(self, make_title: Callable[..., Title]) -> None:
        """Scan output contains disc title and at least one title row."""
        title = _make_title(make_title)
        disc_info = _make_disc([title])
        runner = CliRunner()

        with patch("diskripr.cli.MoviePipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.discover.return_value = disc_info
            mock_cls.return_value = mock_pipeline

            result = runner.invoke(cli, ["movie", "scan"])

        assert result.exit_code == 0, result.output
        assert "TEST DISC" in result.output
        assert "Test Title" in result.output

    def test_output_json_creates_envelope_with_null_metadata(
        self,
        make_title: Callable[..., Title],
        tmp_path: Path,
    ) -> None:
        """--output-json writes a version-1.0 envelope with null movie metadata."""
        title = _make_title(make_title)
        disc_info = _make_disc([title])
        output_file = tmp_path / "jobs.json"
        runner = CliRunner()

        with patch("diskripr.cli.MoviePipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.discover.return_value = disc_info
            mock_cls.return_value = mock_pipeline

            result = runner.invoke(
                cli,
                ["movie", "scan", "--output-json", str(output_file)],
            )

        assert result.exit_code == 0, result.output
        assert output_file.exists()
        envelope = json.loads(output_file.read_text())
        assert envelope["version"] == "1.0"
        assert len(envelope["jobs"]) == 1
        job = envelope["jobs"][0]
        assert job["type"] == "movie"
        assert job["movie"]["name"] is None
        assert job["movie"]["year"] is None
        assert "id" in job

    def test_output_json_append_adds_to_existing_file(
        self,
        make_title: Callable[..., Title],
        tmp_path: Path,
    ) -> None:
        """--append adds a second job to an existing envelope file."""
        title = _make_title(make_title)
        disc_info = _make_disc([title])
        output_file = tmp_path / "jobs.json"
        existing_envelope = {
            "version": "1.0",
            "jobs": [{"id": "abc", "type": "movie", "movie": {"name": "X", "year": 2000}}],
        }
        output_file.write_text(json.dumps(existing_envelope))
        runner = CliRunner()

        with patch("diskripr.cli.MoviePipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.discover.return_value = disc_info
            mock_cls.return_value = mock_pipeline

            result = runner.invoke(
                cli,
                ["movie", "scan", "--output-json", str(output_file), "--append"],
            )

        assert result.exit_code == 0, result.output
        envelope = json.loads(output_file.read_text())
        assert len(envelope["jobs"]) == 2
        assert envelope["jobs"][0]["id"] == "abc"
        assert envelope["jobs"][1]["type"] == "movie"

    def test_output_json_append_fails_on_malformed_existing_file(
        self,
        make_title: Callable[..., Title],
        tmp_path: Path,
    ) -> None:
        """--append exits non-zero when the target file is not a valid envelope."""
        title = _make_title(make_title)
        disc_info = _make_disc([title])
        output_file = tmp_path / "jobs.json"
        output_file.write_text('{"version": "1.0"}')  # missing 'jobs' key
        runner = CliRunner()

        with patch("diskripr.cli.MoviePipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.discover.return_value = disc_info
            mock_cls.return_value = mock_pipeline

            result = runner.invoke(
                cli,
                ["movie", "scan", "--output-json", str(output_file), "--append"],
            )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 2. diskripr movie rip
# ---------------------------------------------------------------------------


class TestMovieRip:
    def test_invokes_movie_pipeline_with_correct_config(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """movie rip constructs a MovieConfig and calls run() on MoviePipeline."""
        title = _make_title(make_title)
        disc_info = _make_disc([title])
        selection = Selection(
            main=title,
            extras=[],
        )
        output_file = tmp_path / "output" / "movies" / "Test Movie (2001)" / "Test Movie (2001).mkv"

        runner = CliRunner()
        with patch("diskripr.cli.MoviePipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.disc_info = disc_info
            mock_pipeline.discover.return_value = disc_info
            mock_pipeline.selection = selection
            mock_pipeline.encode_results = []
            mock_pipeline.organize.return_value = [output_file]
            mock_cls.return_value = mock_pipeline

            with patch("diskripr.cli._select", return_value=(title, [])):
                with patch("diskripr.cli._classify", return_value=selection):
                    result = runner.invoke(
                        cli,
                        [
                            "movie", "rip",
                            "-n", "Test Movie",
                            "-y", "2001",
                            "-o", str(tmp_path / "output"),
                            "--eject",
                        ],
                    )

        assert result.exit_code == 0, result.output
        # Check MovieConfig was constructed with the right movie name
        constructed_cfg = mock_cls.call_args[0][0]
        assert isinstance(constructed_cfg, MovieConfig)
        assert constructed_cfg.movie_name == "Test Movie"
        assert constructed_cfg.movie_year == 2001

    def test_invalid_year_exits_non_zero(self, tmp_path: Path) -> None:
        """movie rip exits non-zero when the movie year is out of range."""
        runner = CliRunner()
        with patch("diskripr.cli.MoviePipeline"):
            result = runner.invoke(
                cli,
                [
                    "movie", "rip",
                    "-n", "Test Movie",
                    "-y", "1800",
                    "-o", str(tmp_path / "output"),
                ],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 3. diskripr movie organize
# ---------------------------------------------------------------------------


class TestMovieOrganize:
    def test_organizes_from_temp_dir(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """movie organize scans temp dir and calls pipeline.organize()."""
        # Create a fake MKV in temp dir
        temp_dir = tmp_path / "output" / ".tmp" / ".tmp"
        temp_dir.mkdir(parents=True)
        mkv = temp_dir / "title_t00.mkv"
        mkv.write_bytes(b"\x00" * 100)

        output_file = tmp_path / "output" / "movies" / "Test Movie (2001)" / "Test Movie (2001).mkv"
        runner = CliRunner()

        with patch("diskripr.cli.MoviePipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.organize.return_value = [output_file]
            mock_cls.return_value = mock_pipeline

            result = runner.invoke(
                cli,
                [
                    "movie", "organize",
                    "-n", "Test Movie",
                    "-y", "2001",
                    "-o", str(tmp_path / "output"),
                    "--no-eject",
                ],
            )

        assert result.exit_code == 0, result.output
        assert mock_pipeline.organize.called


# ---------------------------------------------------------------------------
# 4. diskripr show scan
# ---------------------------------------------------------------------------


class TestShowScan:
    def test_prints_episode_cluster_summary(
        self, make_title: Callable[..., Title]
    ) -> None:
        """show scan prints 'Episode cluster guess' section."""
        ep1 = _make_title(make_title, index=0, duration="00:45:00", name="Title_01")
        ep2 = _make_title(make_title, index=1, duration="00:44:30", name="Title_02")
        disc_info = _make_disc([ep1, ep2])
        runner = CliRunner()

        with patch("diskripr.cli.ShowPipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.disc_info = disc_info
            mock_pipeline.discover.return_value = disc_info
            mock_pipeline.lsdvd_disc = None
            mock_pipeline.ifo_map = None
            mock_pipeline._build_signals_map.return_value = {}
            mock_cls.return_value = mock_pipeline

            with patch("diskripr.cli.cluster_episodes", return_value=([ep1, ep2], [])):
                result = runner.invoke(cli, ["show", "scan"])

        assert result.exit_code == 0, result.output
        assert "Episode cluster guess" in result.output

    def test_output_json_includes_scan_hint(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """show scan --output-json writes a job with _scan_hint and null show metadata."""
        ep1 = _make_title(make_title, index=0, duration="00:45:00", name="Title_01")
        disc_info = _make_disc([ep1])
        output_file = tmp_path / "scan.json"
        runner = CliRunner()

        with patch("diskripr.cli.ShowPipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.disc_info = disc_info
            mock_pipeline.discover.return_value = disc_info
            mock_pipeline._build_signals_map.return_value = {}
            mock_cls.return_value = mock_pipeline

            with patch("diskripr.cli.cluster_episodes", return_value=([ep1], [])):
                result = runner.invoke(
                    cli,
                    ["show", "scan", "--output-json", str(output_file)],
                )

        assert result.exit_code == 0, result.output
        envelope = json.loads(output_file.read_text())
        assert envelope["version"] == "1.0"
        job = envelope["jobs"][0]
        assert job["type"] == "show"
        assert job["show"]["name"] is None
        assert job["show"]["season"] is None
        assert job["show"]["start_episode"] is None
        assert "_scan_hint" in job
        assert "episode candidate" in job["_scan_hint"].lower()


# ---------------------------------------------------------------------------
# 5. diskripr show rip
# ---------------------------------------------------------------------------


class TestShowRip:
    def test_invokes_show_pipeline_with_correct_show_config(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """show rip constructs a ShowConfig and delegates to ShowPipeline.run()."""
        output_file = tmp_path / "output" / "Shows" / "My Show" / "Season 01" / "ep.mkv"
        runner = CliRunner()

        with patch("diskripr.cli.ShowPipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.run.return_value = [output_file]
            mock_cls.return_value = mock_pipeline

            result = runner.invoke(
                cli,
                [
                    "show", "rip",
                    "--show", "My Show",
                    "--season", "1",
                    "--start-episode", "3",
                    "-o", str(tmp_path / "output"),
                    "--no-eject",
                ],
            )

        assert result.exit_code == 0, result.output
        constructed_cfg = mock_cls.call_args[0][0]
        assert isinstance(constructed_cfg, ShowConfig)
        assert constructed_cfg.show_name == "My Show"
        assert constructed_cfg.season_number == 1
        assert constructed_cfg.start_episode == 3

    def test_invalid_season_number_exits_non_zero(self, tmp_path: Path) -> None:
        """show rip exits non-zero when season_number is negative."""
        runner = CliRunner()
        with patch("diskripr.cli.ShowPipeline"):
            result = runner.invoke(
                cli,
                [
                    "show", "rip",
                    "--show", "My Show",
                    "--season", "-1",
                    "--start-episode", "1",
                    "-o", str(tmp_path / "output"),
                ],
            )
        assert result.exit_code != 0

    def test_invalid_start_episode_exits_non_zero(self, tmp_path: Path) -> None:
        """show rip exits non-zero when start_episode is 0."""
        runner = CliRunner()
        with patch("diskripr.cli.ShowPipeline"):
            result = runner.invoke(
                cli,
                [
                    "show", "rip",
                    "--show", "My Show",
                    "--season", "1",
                    "--start-episode", "0",
                    "-o", str(tmp_path / "output"),
                ],
            )
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 6. diskripr show organize
# ---------------------------------------------------------------------------


class TestShowOrganize:
    def test_organizes_episodes_from_temp_dir(
        self, make_title: Callable[..., Title], tmp_path: Path
    ) -> None:
        """show organize scans temp dir and routes files as episodes."""
        temp_dir = tmp_path / "output" / ".tmp" / ".tmp"
        temp_dir.mkdir(parents=True)
        (temp_dir / "title_t00.mkv").write_bytes(b"\x00" * 200)
        (temp_dir / "title_t01.mkv").write_bytes(b"\x00" * 180)

        output_files = [
            tmp_path / "output" / "Shows" / "Test Show" / "Season 01" / "Test Show - S01E05.mkv",
            tmp_path / "output" / "Shows" / "Test Show" / "Season 01" / "Test Show - S01E06.mkv",
        ]
        runner = CliRunner()

        with patch("diskripr.cli.ShowPipeline") as mock_cls:
            mock_pipeline = MagicMock()
            mock_pipeline.organize.return_value = output_files
            mock_cls.return_value = mock_pipeline

            result = runner.invoke(
                cli,
                [
                    "show", "organize",
                    "--show", "Test Show",
                    "--season", "1",
                    "--start-episode", "5",
                    "-o", str(tmp_path / "output"),
                    "--no-eject",
                ],
            )

        assert result.exit_code == 0, result.output
        # Verify ShowSelection was set with correct episode numbering
        selection = mock_pipeline.selection
        assert selection is not None
        episode_numbers = [entry.episode_number for entry in selection.episodes]
        assert episode_numbers == [5, 6]

    def test_no_mkv_files_exits_non_zero(self, tmp_path: Path) -> None:
        """show organize exits non-zero when no MKV files are in temp dir."""
        temp_dir = tmp_path / "output" / ".tmp" / ".tmp"
        temp_dir.mkdir(parents=True)
        runner = CliRunner()

        with patch("diskripr.cli.ShowPipeline"):
            result = runner.invoke(
                cli,
                [
                    "show", "organize",
                    "--show", "Test Show",
                    "--season", "1",
                    "--start-episode", "1",
                    "-o", str(tmp_path / "output"),
                    "--no-eject",
                ],
            )

        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 7. diskripr queue check
# ---------------------------------------------------------------------------


def _write_queue_file(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _valid_queue_file(tmp_path: Path) -> Path:
    return _write_queue_file(
        tmp_path / "queue.json",
        {
            "version": "1.0",
            "jobs": [
                {"type": "movie", "movie": {"name": "The Matrix", "year": 1999}},
                {
                    "type": "show",
                    "show": {"name": "Breaking Bad", "season": 1, "start_episode": 1},
                },
            ],
        },
    )


class TestQueueCheck:
    def test_valid_file_exits_zero(self, tmp_path: Path) -> None:
        filepath = _valid_queue_file(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert result.exit_code == 0, result.output

    def test_valid_file_prints_job_summary(self, tmp_path: Path) -> None:
        filepath = _valid_queue_file(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert "movie" in result.output
        assert "The Matrix" in result.output
        assert "Breaking Bad" in result.output

    def test_valid_file_prints_job_count(self, tmp_path: Path) -> None:
        filepath = _valid_queue_file(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert "2 job" in result.output

    def test_invalid_file_exits_non_zero(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "bad.json",
            {"version": "1.0", "jobs": [{"type": "movie", "movie": {"year": 1999}}]},
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert result.exit_code != 0

    def test_invalid_file_prints_errors(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "bad.json",
            {"version": "1.0", "jobs": [{"type": "movie", "movie": {"year": 1999}}]},
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert "movie.name" in result.output

    def test_missing_file_exits_non_zero(self, tmp_path: Path) -> None:
        runner = CliRunner()
        result = runner.invoke(
            cli, ["queue", "check", "--file", str(tmp_path / "missing.json")]
        )
        assert result.exit_code != 0

    def test_job_index_shown_in_summary(self, tmp_path: Path) -> None:
        filepath = _valid_queue_file(tmp_path)
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert "[0]" in result.output
        assert "[1]" in result.output

    def test_show_summary_includes_season(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "queue.json",
            {
                "version": "1.0",
                "jobs": [
                    {
                        "type": "show",
                        "show": {"name": "Breaking Bad", "season": 3, "start_episode": 7},
                    }
                ],
            },
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert "S03" in result.output
        assert "ep7" in result.output

    def test_job_id_shown_when_present(self, tmp_path: Path) -> None:
        uid = "test-uid-abc"
        filepath = _write_queue_file(
            tmp_path / "queue.json",
            {
                "version": "1.0",
                "jobs": [
                    {
                        "id": uid,
                        "type": "movie",
                        "movie": {"name": "Alien", "year": 1979},
                    }
                ],
            },
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "check", "--file", str(filepath)])
        assert uid in result.output


# ---------------------------------------------------------------------------
# 8. diskripr queue run
# ---------------------------------------------------------------------------


class TestQueueRun:
    def test_valid_file_invokes_queue_runner(self, tmp_path: Path) -> None:
        filepath = _valid_queue_file(tmp_path)
        runner = CliRunner()
        mock_runner_instance = MagicMock()
        with patch("diskripr.queue.QueueRunner", return_value=mock_runner_instance):
            result = runner.invoke(
                cli,
                ["queue", "run", "--file", str(filepath)],
            )
        assert result.exit_code == 0, result.output
        mock_runner_instance.run.assert_called_once()

    def test_invalid_file_exits_non_zero(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "bad.json",
            {"version": "1.0", "jobs": [{"type": "movie", "movie": {"year": 1999}}]},
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "run", "--file", str(filepath)])
        assert result.exit_code != 0

    def test_invalid_file_prints_errors(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "bad.json",
            {"version": "1.0", "jobs": [{"type": "movie", "movie": {"year": 1999}}]},
        )
        runner = CliRunner()
        result = runner.invoke(cli, ["queue", "run", "--file", str(filepath)])
        assert "movie.name" in result.output

    def test_global_device_override_applied(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "queue.json",
            {"version": "1.0", "jobs": [
                {"type": "movie", "movie": {"name": "The Matrix", "year": 1999}}
            ]},
        )
        runner = CliRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = runner.invoke(
                cli,
                ["queue", "run", "--file", str(filepath), "--device", "/dev/sr2"],
            )
        assert result.exit_code == 0, result.output
        config_arg = mock_cls.call_args[0][0]
        assert config_arg.device == "/dev/sr2"

    def test_global_min_length_override_applied(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "queue.json",
            {"version": "1.0", "jobs": [
                {"type": "movie", "movie": {"name": "The Matrix", "year": 1999}}
            ]},
        )
        runner = CliRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = runner.invoke(
                cli,
                ["queue", "run", "--file", str(filepath), "--min-length", "60"],
            )
        assert result.exit_code == 0, result.output
        config_arg = mock_cls.call_args[0][0]
        assert config_arg.min_length == 60

    def test_ask_rip_mode_warns(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "queue.json",
            {
                "version": "1.0",
                "jobs": [
                    {
                        "type": "movie",
                        "movie": {"name": "The Matrix", "year": 1999},
                        "options": {"rip_mode": "ask"},
                    }
                ],
            },
        )
        runner = CliRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = runner.invoke(cli, ["queue", "run", "--file", str(filepath)])
        assert "ask" in result.output

    def test_queue_complete_message_on_success(self, tmp_path: Path) -> None:
        filepath = _write_queue_file(
            tmp_path / "queue.json",
            {"version": "1.0", "jobs": [
                {"type": "movie", "movie": {"name": "The Matrix", "year": 1999}}
            ]},
        )
        runner = CliRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = runner.invoke(cli, ["queue", "run", "--file", str(filepath)])
        assert result.exit_code == 0, result.output
        assert "complete" in result.output.lower()

    def test_disc_swap_timeout_exits_non_zero(self, tmp_path: Path) -> None:
        filepath = _valid_queue_file(tmp_path)
        runner = CliRunner()
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.side_effect = TimeoutError("disc swap timed out")
        with patch("diskripr.queue.QueueRunner", return_value=mock_runner_instance):
            result = runner.invoke(
                cli,
                ["queue", "run", "--file", str(filepath)],
            )
        assert result.exit_code != 0
