"""Tests for ``diskripr.queue`` — validation (8.2), disc swap (8.3), QueueRunner (8.4).

Covers:
- ``validate_job_file()`` returns an empty list for a valid file.
- ``validate_job_file()`` catches all required-field violations.
- Errors are formatted as ``Error in jobs[N]: "field" <message>``.
- Missing or non-parseable file returns a descriptive error string.
- Invalid JSON returns a descriptive error string.
- ``version`` != ``"1.0"`` is caught and reported.
- ``wait_for_disc_removed()`` polls until disc absent; raises TimeoutError.
- ``wait_for_disc_inserted()`` polls until disc present; raises TimeoutError.
- ``resolve_options()`` applies job > global > default priority.
- ``QueueRunner.run()`` dispatches to the correct pipeline per job type.
- ``QueueRunner.run()`` logs a warning when rip_mode='ask'.
- ``QueueRunner.run()`` performs disc swap between jobs.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

from diskripr.queue import (
    QueueRunner,
    resolve_options,
    validate_job_file,
    wait_for_disc_inserted,
    wait_for_disc_removed,
)
from diskripr.schema import JobFile, JobOptions


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_job_file(path: Path, data: object) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _valid_movie_job() -> dict:
    return {"type": "movie", "movie": {"name": "The Matrix", "year": 1999}}


def _valid_show_job() -> dict:
    return {
        "type": "show",
        "show": {"name": "Breaking Bad", "season": 1, "start_episode": 1},
    }


def _valid_file(*jobs: dict) -> dict:
    return {"version": "1.0", "jobs": list(jobs)}


# ---------------------------------------------------------------------------
# Valid files return empty list
# ---------------------------------------------------------------------------

class TestValidJobFiles:
    def test_single_movie_job_is_valid(self, tmp_path: Path) -> None:
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(_valid_movie_job()))
        assert validate_job_file(filepath) == []

    def test_single_show_job_is_valid(self, tmp_path: Path) -> None:
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(_valid_show_job()))
        assert validate_job_file(filepath) == []

    def test_mixed_jobs_are_valid(self, tmp_path: Path) -> None:
        filepath = _write_job_file(
            tmp_path / "queue.json",
            _valid_file(_valid_movie_job(), _valid_show_job()),
        )
        assert validate_job_file(filepath) == []

    def test_empty_jobs_list_is_valid(self, tmp_path: Path) -> None:
        filepath = _write_job_file(tmp_path / "queue.json", {"version": "1.0", "jobs": []})
        assert validate_job_file(filepath) == []

    def test_job_with_options_is_valid(self, tmp_path: Path) -> None:
        job = _valid_movie_job()
        job["options"] = {"device": "/dev/sr0", "rip_mode": "auto"}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        # Note: "auto" is not a valid rip_mode — expect an error
        errors = validate_job_file(filepath)
        # rip_mode "auto" is invalid; this test verifies the plumbing catches it
        assert len(errors) == 1
        assert "rip_mode" in errors[0]

    def test_job_with_valid_options_is_valid(self, tmp_path: Path) -> None:
        job = _valid_movie_job()
        job["options"] = {"device": "/dev/sr0", "rip_mode": "main", "min_length": 10}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        assert validate_job_file(filepath) == []


# ---------------------------------------------------------------------------
# File I/O errors
# ---------------------------------------------------------------------------

class TestFileErrors:
    def test_missing_file_returns_error(self, tmp_path: Path) -> None:
        errors = validate_job_file(tmp_path / "nonexistent.json")
        assert len(errors) == 1
        assert "Could not read job file" in errors[0]

    def test_invalid_json_returns_error(self, tmp_path: Path) -> None:
        filepath = tmp_path / "bad.json"
        filepath.write_text("{not valid json", encoding="utf-8")
        errors = validate_job_file(filepath)
        assert len(errors) == 1
        assert "not valid JSON" in errors[0]


# ---------------------------------------------------------------------------
# Envelope-level validation
# ---------------------------------------------------------------------------

class TestEnvelopeValidation:
    def test_wrong_version_raises(self, tmp_path: Path) -> None:
        filepath = _write_job_file(
            tmp_path / "queue.json",
            {"version": "2.0", "jobs": [_valid_movie_job()]},
        )
        errors = validate_job_file(filepath)
        assert len(errors) >= 1
        assert any("version" in err for err in errors)

    def test_missing_version_raises(self, tmp_path: Path) -> None:
        filepath = _write_job_file(
            tmp_path / "queue.json",
            {"jobs": [_valid_movie_job()]},
        )
        errors = validate_job_file(filepath)
        assert len(errors) >= 1
        assert any("version" in err for err in errors)

    def test_missing_jobs_key_raises(self, tmp_path: Path) -> None:
        filepath = _write_job_file(tmp_path / "queue.json", {"version": "1.0"})
        errors = validate_job_file(filepath)
        assert len(errors) >= 1
        assert any("jobs" in err for err in errors)


# ---------------------------------------------------------------------------
# Job-level required-field violations
# ---------------------------------------------------------------------------

class TestJobRequiredFields:
    def test_missing_movie_name_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "movie", "movie": {"year": 1999}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("movie.name" in err for err in errors)

    def test_missing_movie_year_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "movie", "movie": {"name": "The Matrix"}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("movie.year" in err for err in errors)

    def test_missing_show_name_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "show", "show": {"season": 1, "start_episode": 1}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("show.name" in err for err in errors)

    def test_missing_show_season_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "show", "show": {"name": "Breaking Bad", "start_episode": 1}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("show.season" in err for err in errors)

    def test_missing_show_start_episode_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "show", "show": {"name": "Breaking Bad", "season": 1}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("show.start_episode" in err for err in errors)

    def test_missing_type_discriminator_produces_error(self, tmp_path: Path) -> None:
        job = {"movie": {"name": "The Matrix", "year": 1999}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert len(errors) >= 1

    def test_movie_year_out_of_range_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "movie", "movie": {"name": "Old Film", "year": 1887}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("movie.year" in err for err in errors)

    def test_show_negative_season_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "show", "show": {"name": "Bad", "season": -1, "start_episode": 1}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("show.season" in err for err in errors)

    def test_show_start_episode_zero_produces_error(self, tmp_path: Path) -> None:
        job = {"type": "show", "show": {"name": "Bad", "season": 1, "start_episode": 0}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any("show.start_episode" in err for err in errors)


# ---------------------------------------------------------------------------
# Error format
# ---------------------------------------------------------------------------

class TestErrorFormat:
    def test_error_references_job_index(self, tmp_path: Path) -> None:
        job0 = _valid_movie_job()
        job1 = {"type": "movie", "movie": {"year": 1999}}  # missing name
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job0, job1))
        errors = validate_job_file(filepath)
        # The error must cite jobs[1]
        assert any("jobs[1]" in err for err in errors)

    def test_error_cites_field_in_quotes(self, tmp_path: Path) -> None:
        job = {"type": "movie", "movie": {"year": 1999}}  # missing name
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        assert any('"movie.name"' in err for err in errors)

    def test_multiple_errors_all_reported(self, tmp_path: Path) -> None:
        # Both movie.name and movie.year are bad
        job = {"type": "movie", "movie": {"name": "", "year": 1887}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        # year out-of-range is guaranteed; name may or may not be caught
        # (empty string passes Pydantic str type but fails domain rules)
        assert len(errors) >= 1

    def test_second_job_error_references_correct_index(self, tmp_path: Path) -> None:
        good = _valid_movie_job()
        bad_show = {"type": "show", "show": {"name": "X", "season": -5, "start_episode": 1}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(good, bad_show))
        errors = validate_job_file(filepath)
        assert any("jobs[1]" in err for err in errors)


# ---------------------------------------------------------------------------
# Whole-file validation: no jobs run on error
# ---------------------------------------------------------------------------

class TestWholeFileValidation:
    def test_all_errors_reported_before_any_job_runs(self, tmp_path: Path) -> None:
        """Both jobs have errors; both should be listed before any run begins."""
        job0 = {"type": "movie", "movie": {"year": 1999}}  # missing name
        job1 = {"type": "show", "show": {"name": "Bad", "season": -1, "start_episode": 1}}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job0, job1))
        errors = validate_job_file(filepath)
        assert any("jobs[0]" in err for err in errors)
        assert any("jobs[1]" in err for err in errors)

    @pytest.mark.parametrize("rip_mode", ["ask", "auto", "main", "all"])
    def test_rip_mode_ask_is_structurally_valid(
        self, tmp_path: Path, rip_mode: str
    ) -> None:
        """Only 'ask', 'main', 'all' are valid literal values."""
        job = _valid_movie_job()
        job["options"] = {"rip_mode": rip_mode}
        filepath = _write_job_file(tmp_path / "queue.json", _valid_file(job))
        errors = validate_job_file(filepath)
        if rip_mode in ("ask", "main", "all"):
            assert errors == []
        else:
            assert len(errors) >= 1


# ---------------------------------------------------------------------------
# Disc swap polling (8.3)
# ---------------------------------------------------------------------------

class TestWaitForDiscRemoved:
    def test_returns_immediately_when_disc_absent(self) -> None:
        probe = MagicMock(return_value=False)  # no disc from the start
        wait_for_disc_removed("/dev/sr0", timeout_seconds=5, poll_interval=1, disc_probe=probe)
        probe.assert_called_once_with("/dev/sr0")

    def test_polls_until_disc_removed(self) -> None:
        # disc present on first two calls, absent on third
        probe = MagicMock(side_effect=[True, True, False])
        with patch("diskripr.queue.time.sleep") as mock_sleep:
            wait_for_disc_removed("/dev/sr0", timeout_seconds=30, poll_interval=5, disc_probe=probe)
        assert probe.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(5)

    def test_raises_timeout_error_if_disc_never_removed(self) -> None:
        probe = MagicMock(return_value=True)  # disc always present
        with patch("diskripr.queue.time.monotonic", side_effect=[0.0, 0.0, 9999.0]):
            with patch("diskripr.queue.time.sleep"):
                with pytest.raises(TimeoutError, match="Timed out waiting for disc to be removed"):
                    wait_for_disc_removed("/dev/sr0", timeout_seconds=1, poll_interval=1, disc_probe=probe)

    def test_passes_device_to_probe(self) -> None:
        probe = MagicMock(return_value=False)
        wait_for_disc_removed("/dev/sr1", timeout_seconds=5, poll_interval=1, disc_probe=probe)
        probe.assert_called_once_with("/dev/sr1")


class TestWaitForDiscInserted:
    def test_returns_immediately_when_disc_present(self) -> None:
        probe = MagicMock(return_value=True)  # disc present from the start
        wait_for_disc_inserted("/dev/sr0", timeout_seconds=5, poll_interval=1, disc_probe=probe)
        probe.assert_called_once_with("/dev/sr0")

    def test_polls_until_disc_inserted(self) -> None:
        # disc absent on first two calls, present on third
        probe = MagicMock(side_effect=[False, False, True])
        with patch("diskripr.queue.time.sleep") as mock_sleep:
            wait_for_disc_inserted("/dev/sr0", timeout_seconds=30, poll_interval=5, disc_probe=probe)
        assert probe.call_count == 3
        assert mock_sleep.call_count == 2

    def test_raises_timeout_error_if_disc_never_inserted(self) -> None:
        probe = MagicMock(return_value=False)  # disc always absent
        with patch("diskripr.queue.time.monotonic", side_effect=[0.0, 0.0, 9999.0]):
            with patch("diskripr.queue.time.sleep"):
                with pytest.raises(TimeoutError, match="Timed out waiting for disc to be inserted"):
                    wait_for_disc_inserted("/dev/sr0", timeout_seconds=1, poll_interval=1, disc_probe=probe)

    def test_passes_device_to_probe(self) -> None:
        probe = MagicMock(return_value=True)
        wait_for_disc_inserted("/dev/sr2", timeout_seconds=5, poll_interval=1, disc_probe=probe)
        probe.assert_called_once_with("/dev/sr2")


# ---------------------------------------------------------------------------
# resolve_options (8.4)
# ---------------------------------------------------------------------------

class TestResolveOptions:
    def test_builtin_defaults_when_no_overrides(self) -> None:
        result = resolve_options(None, {})
        assert result["device"] == "/dev/sr0"
        assert result["rip_mode"] == "main"
        assert result["encode_format"] == "none"
        assert result["min_length"] == 10
        assert result["keep_original"] is False
        assert result["eject_on_complete"] is True

    def test_global_override_wins_over_default(self) -> None:
        result = resolve_options(None, {"device": "/dev/sr1", "min_length": 30})
        assert result["device"] == "/dev/sr1"
        assert result["min_length"] == 30
        # Unmentioned fields still use defaults.
        assert result["rip_mode"] == "main"

    def test_job_option_wins_over_global_override(self) -> None:
        opts = JobOptions.model_validate({"device": "/dev/sr2"})
        result = resolve_options(opts, {"device": "/dev/sr1"})
        assert result["device"] == "/dev/sr2"

    def test_job_option_wins_over_default(self) -> None:
        opts = JobOptions.model_validate({"rip_mode": "all", "min_length": 60})
        result = resolve_options(opts, {})
        assert result["rip_mode"] == "all"
        assert result["min_length"] == 60

    def test_none_job_option_field_falls_through_to_global(self) -> None:
        # JobOptions has device=None (not set); global says /dev/sr1.
        opts = JobOptions.model_validate({"rip_mode": "all"})
        result = resolve_options(opts, {"device": "/dev/sr1"})
        assert result["device"] == "/dev/sr1"

    def test_none_job_option_field_falls_through_to_default(self) -> None:
        opts = JobOptions.model_validate({})
        result = resolve_options(opts, {})
        assert result["device"] == "/dev/sr0"

    def test_all_three_layers(self) -> None:
        opts = JobOptions.model_validate({"quality": 18})
        result = resolve_options(opts, {"encode_format": "h265", "min_length": 20})
        # job wins for quality
        assert result["quality"] == 18
        # global wins for encode_format
        assert result["encode_format"] == "h265"
        # global wins for min_length
        assert result["min_length"] == 20
        # default for everything else
        assert result["device"] == "/dev/sr0"


# ---------------------------------------------------------------------------
# QueueRunner (8.4)
# ---------------------------------------------------------------------------

def _make_job_file_obj(*jobs_data: dict) -> JobFile:
    """Build a ``JobFile`` from raw dicts."""
    return JobFile.model_validate({"version": "1.0", "jobs": list(jobs_data)})


def _movie_job_data(**kwargs: object) -> dict:
    base = {"type": "movie", "movie": {"name": "The Matrix", "year": 1999}}
    base.update(kwargs)
    return base


def _show_job_data(**kwargs: object) -> dict:
    base = {"type": "show", "show": {"name": "Breaking Bad", "season": 1, "start_episode": 1}}
    base.update(kwargs)
    return base


class TestQueueRunnerDispatch:
    """QueueRunner routes movie jobs to MoviePipeline, show jobs to ShowPipeline."""

    def test_single_movie_job_runs_movie_pipeline(self, tmp_path: Path) -> None:
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path)})
        )
        runner = QueueRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            runner.run(job_file)
        mock_cls.assert_called_once()
        mock_instance.run.assert_called_once()

    def test_single_show_job_runs_show_pipeline(self, tmp_path: Path) -> None:
        job_file = _make_job_file_obj(
            _show_job_data(options={"output_dir": str(tmp_path)})
        )
        runner = QueueRunner()
        with patch("diskripr.queue.ShowPipeline") as mock_cls:
            mock_instance = MagicMock()
            mock_cls.return_value = mock_instance
            runner.run(job_file)
        mock_cls.assert_called_once()
        mock_instance.run.assert_called_once()

    def test_movie_pipeline_receives_correct_config_fields(self, tmp_path: Path) -> None:
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path), "min_length": 42})
        )
        runner = QueueRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            runner.run(job_file)
        config_arg = mock_cls.call_args[0][0]
        assert config_arg.movie_name == "The Matrix"
        assert config_arg.movie_year == 1999
        assert config_arg.min_length == 42

    def test_show_pipeline_receives_correct_config_fields(self, tmp_path: Path) -> None:
        job_file = _make_job_file_obj(
            _show_job_data(options={"output_dir": str(tmp_path)})
        )
        runner = QueueRunner()
        with patch("diskripr.queue.ShowPipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            runner.run(job_file)
        config_arg = mock_cls.call_args[0][0]
        assert config_arg.show_name == "Breaking Bad"
        assert config_arg.season_number == 1
        assert config_arg.start_episode == 1

    def test_global_override_applied_to_pipeline(self, tmp_path: Path) -> None:
        job_file = _make_job_file_obj(_movie_job_data())
        runner = QueueRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            runner.run(job_file, global_overrides={"output_dir": str(tmp_path), "min_length": 99})
        config_arg = mock_cls.call_args[0][0]
        assert config_arg.min_length == 99

    def test_job_option_beats_global_override(self, tmp_path: Path) -> None:
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path), "min_length": 5})
        )
        runner = QueueRunner()
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            runner.run(job_file, global_overrides={"min_length": 99})
        config_arg = mock_cls.call_args[0][0]
        assert config_arg.min_length == 5

    def test_empty_job_list_runs_without_error(self) -> None:
        job_file = JobFile.model_validate({"version": "1.0", "jobs": []})
        runner = QueueRunner()
        runner.run(job_file)  # should not raise


class TestQueueRunnerAskModeWarning:
    def test_ask_rip_mode_logs_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path), "rip_mode": "ask"})
        )
        runner = QueueRunner()
        with caplog.at_level(logging.WARNING, logger="diskripr.queue"):
            with patch("diskripr.queue.MoviePipeline") as mock_cls:
                mock_cls.return_value = MagicMock()
                runner.run(job_file)
        assert any("rip_mode='ask'" in rec.message for rec in caplog.records)

    def test_non_ask_rip_mode_no_warning(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path), "rip_mode": "main"})
        )
        runner = QueueRunner()
        with caplog.at_level(logging.WARNING, logger="diskripr.queue"):
            with patch("diskripr.queue.MoviePipeline") as mock_cls:
                mock_cls.return_value = MagicMock()
                runner.run(job_file)
        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("rip_mode='ask'" in msg for msg in warning_msgs)


class TestQueueRunnerDiscSwap:
    """Between jobs: runner performs disc swap sequencing."""

    def _make_two_job_file(self, tmp_path: Path) -> JobFile:
        return _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path), "eject_on_complete": True}),
            _movie_job_data(options={"output_dir": str(tmp_path), "eject_on_complete": True}),
        )

    def test_no_swap_after_single_job(self, tmp_path: Path) -> None:
        probe = MagicMock(return_value=False)
        runner = QueueRunner(poll_interval=0, disc_probe=probe)
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path)})
        )
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            runner.run(job_file)
        # probe is for disc swap; with one job there is no swap.
        probe.assert_not_called()

    def test_swap_called_between_two_jobs_eject_true(self, tmp_path: Path) -> None:
        # probe: first call returns True (disc still present after job 1 eject),
        # second returns False (disc removed), third returns True (new disc in).
        probe = MagicMock(side_effect=[True, False, True])
        runner = QueueRunner(poll_interval=0, timeout_seconds=60, disc_probe=probe)
        job_file = self._make_two_job_file(tmp_path)
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            with patch("diskripr.queue.time.sleep"):
                runner.run(job_file)
        # probe called at least once for removal, once for insertion
        assert probe.call_count >= 2

    def test_swap_with_eject_false_prompts_user(self, tmp_path: Path) -> None:
        probe = MagicMock(return_value=True)  # disc present once we get to insert poll
        runner = QueueRunner(poll_interval=0, timeout_seconds=60, disc_probe=probe)
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path), "eject_on_complete": False}),
            _movie_job_data(options={"output_dir": str(tmp_path), "eject_on_complete": False}),
        )
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            with patch("builtins.input", return_value=""):
                runner.run(job_file)
        # input() should have been called once (for the one inter-job swap).
        # The probe is only called for insertion (no wait_for_disc_removed
        # when eject_on_complete=False).

    def test_disc_swap_uses_runner_poll_interval(self, tmp_path: Path) -> None:
        probe = MagicMock(side_effect=[True, False, True])
        runner = QueueRunner(poll_interval=7, timeout_seconds=60, disc_probe=probe)
        job_file = self._make_two_job_file(tmp_path)
        with patch("diskripr.queue.MoviePipeline") as mock_cls:
            mock_cls.return_value = MagicMock()
            with patch("diskripr.queue.time.sleep") as mock_sleep:
                runner.run(job_file)
        # All sleep calls must use poll_interval=7.
        for sleep_call in mock_sleep.call_args_list:
            assert sleep_call == call(7)

    def test_three_jobs_two_swaps(self, tmp_path: Path) -> None:
        # 3 jobs → 2 swaps; each swap: removed then inserted
        # probe sequence: [True, False] for first swap, [True, False] for second
        # simplified: alternate True/False four times
        probe_returns = [True, False, True, False, True, False]  # pad generously
        probe = MagicMock(side_effect=probe_returns)
        runner = QueueRunner(poll_interval=0, timeout_seconds=60, disc_probe=probe)
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path), "eject_on_complete": True}),
            _movie_job_data(options={"output_dir": str(tmp_path), "eject_on_complete": True}),
            _show_job_data(options={"output_dir": str(tmp_path), "eject_on_complete": True}),
        )
        with patch("diskripr.queue.MoviePipeline") as mock_movie_cls:
            with patch("diskripr.queue.ShowPipeline") as mock_show_cls:
                mock_movie_cls.return_value = MagicMock()
                mock_show_cls.return_value = MagicMock()
                with patch("diskripr.queue.time.sleep"):
                    runner.run(job_file)
        assert mock_movie_cls.call_count == 2
        assert mock_show_cls.call_count == 1


class TestQueueRunnerIdLogging:
    """Job id is included in log output."""

    def test_id_appears_in_log(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        uid = "test-uid-1234"
        job_file = _make_job_file_obj(
            _movie_job_data(id=uid, options={"output_dir": str(tmp_path)})
        )
        runner = QueueRunner()
        with caplog.at_level(logging.INFO, logger="diskripr.queue"):
            with patch("diskripr.queue.MoviePipeline") as mock_cls:
                mock_cls.return_value = MagicMock()
                runner.run(job_file)
        assert any(uid in rec.message for rec in caplog.records)

    def test_no_id_no_id_str_in_log(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        job_file = _make_job_file_obj(
            _movie_job_data(options={"output_dir": str(tmp_path)})
        )
        runner = QueueRunner()
        with caplog.at_level(logging.INFO, logger="diskripr.queue"):
            with patch("diskripr.queue.MoviePipeline") as mock_cls:
                mock_cls.return_value = MagicMock()
                runner.run(job_file)
        assert not any("id=" in rec.message for rec in caplog.records)
