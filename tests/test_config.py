"""Tests for ``diskripr.config``.

Covers:
- ``BaseConfig.__post_init__``: Path coercion, temp_dir defaulting, quality
  defaults for each encoder, ``DISKRIPR_TEMP_DIR`` environment variable.
- ``BaseConfig.validate()``: shared error conditions (disc_number, min_length,
  quality range, HandBrakeCLI availability).
- ``MovieConfig.validate()``: movie_name blank, movie_year out of range,
  multiple errors reported together.
- ``ShowConfig.validate()``: show_name blank, season_number < 0,
  start_episode < 1.
- ``MovieConfig.from_click_params()``: round-trip construction.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from diskripr.config import BaseConfig, ConfigError, MovieConfig, ShowConfig


# ---------------------------------------------------------------------------
# BaseConfig.__post_init__ — coercion and defaults
# ---------------------------------------------------------------------------

class TestBaseConfigPostInit:
    def test_string_output_dir_coerced_to_path(self, tmp_path: Path) -> None:
        cfg = BaseConfig(output_dir=str(tmp_path / "output"))  # type: ignore[arg-type]
        assert isinstance(cfg.output_dir, Path)

    def test_temp_dir_defaults_to_output_dir_dotmp(self, tmp_path: Path) -> None:
        output = tmp_path / "output"
        cfg = BaseConfig(output_dir=output)
        assert cfg.temp_dir == output / ".tmp"

    def test_diskripr_temp_dir_env_var_overrides_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom_tmp = str(tmp_path / "custom_tmp")
        monkeypatch.setenv("DISKRIPR_TEMP_DIR", custom_tmp)
        cfg = BaseConfig(output_dir=tmp_path)
        assert cfg.temp_dir == Path(custom_tmp)

    def test_explicit_temp_dir_not_overridden_by_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        explicit = tmp_path / "my_tmp"
        monkeypatch.setenv("DISKRIPR_TEMP_DIR", str(tmp_path / "env_tmp"))
        cfg = BaseConfig(output_dir=tmp_path, temp_dir=explicit)
        assert cfg.temp_dir == explicit

    def test_h264_sets_default_quality_20(self, tmp_path: Path) -> None:
        cfg = BaseConfig(output_dir=tmp_path, encode_format="h264")
        assert cfg.quality == 20

    def test_h265_sets_default_quality_22(self, tmp_path: Path) -> None:
        cfg = BaseConfig(output_dir=tmp_path, encode_format="h265")
        assert cfg.quality == 22

    def test_none_encode_format_leaves_quality_as_none(self, tmp_path: Path) -> None:
        cfg = BaseConfig(output_dir=tmp_path, encode_format="none")
        assert cfg.quality is None

    def test_explicit_quality_not_overridden(self, tmp_path: Path) -> None:
        cfg = BaseConfig(output_dir=tmp_path, encode_format="h264", quality=18)
        assert cfg.quality == 18


# ---------------------------------------------------------------------------
# BaseConfig.validate() — shared error conditions
# ---------------------------------------------------------------------------

class TestBaseConfigValidate:
    def _base(self, tmp_path: Path) -> BaseConfig:
        return BaseConfig(output_dir=tmp_path)

    def test_valid_config_does_not_raise(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.validate()  # should not raise

    def test_disc_number_zero_raises(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.disc_number = 0
        with pytest.raises(ConfigError, match="disc_number"):
            cfg.validate()

    def test_disc_number_negative_raises(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.disc_number = -1
        with pytest.raises(ConfigError, match="disc_number"):
            cfg.validate()

    def test_disc_number_one_is_valid(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.disc_number = 1
        cfg.validate()  # should not raise

    def test_min_length_zero_raises(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.min_length = 0
        with pytest.raises(ConfigError, match="min_length"):
            cfg.validate()

    def test_min_length_negative_raises(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.min_length = -10
        with pytest.raises(ConfigError, match="min_length"):
            cfg.validate()

    def test_quality_above_51_raises(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.encode_format = "h264"
        cfg.quality = 52
        with pytest.raises(ConfigError, match="quality"):
            cfg.validate()

    def test_quality_at_51_is_valid(self, tmp_path: Path) -> None:
        cfg = self._base(tmp_path)
        cfg.encode_format = "h264"
        cfg.quality = 51
        with patch(
            "diskripr.drivers.handbrake.HandBrakeDriver.is_available",
            return_value=True,
        ):
            cfg.validate()  # should not raise

    def test_encoding_requested_without_handbrake_raises(
        self, tmp_path: Path
    ) -> None:
        cfg = self._base(tmp_path)
        cfg.encode_format = "h264"
        cfg.quality = 20
        with patch(
            "diskripr.drivers.handbrake.HandBrakeDriver.is_available",
            return_value=False,
        ):
            with pytest.raises(ConfigError, match="HandBrakeCLI"):
                cfg.validate()


# ---------------------------------------------------------------------------
# MovieConfig — construction and validation
# ---------------------------------------------------------------------------

class TestMovieConfigValidate:
    def _base_movie(self, tmp_path: Path) -> MovieConfig:
        return MovieConfig(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=tmp_path,
        )

    def test_valid_movie_config_does_not_raise(self, tmp_path: Path) -> None:
        cfg = self._base_movie(tmp_path)
        cfg.validate()  # should not raise

    def test_blank_movie_name_raises(self, tmp_path: Path) -> None:
        cfg = self._base_movie(tmp_path)
        cfg.movie_name = "   "
        with pytest.raises(ConfigError, match="movie_name"):
            cfg.validate()

    def test_year_below_minimum_raises(self, tmp_path: Path) -> None:
        cfg = self._base_movie(tmp_path)
        cfg.movie_year = 1887
        with pytest.raises(ConfigError, match="movie_year"):
            cfg.validate()

    def test_year_above_maximum_raises(self, tmp_path: Path) -> None:
        cfg = self._base_movie(tmp_path)
        cfg.movie_year = 2100
        with pytest.raises(ConfigError, match="movie_year"):
            cfg.validate()

    def test_multiple_errors_reported_together(self, tmp_path: Path) -> None:
        cfg = self._base_movie(tmp_path)
        cfg.movie_name = ""
        cfg.movie_year = 1800
        with pytest.raises(ConfigError) as exc_info:
            cfg.validate()
        message = str(exc_info.value)
        assert "movie_name" in message
        assert "movie_year" in message

    def test_shared_disc_number_check_applies(self, tmp_path: Path) -> None:
        cfg = self._base_movie(tmp_path)
        cfg.disc_number = 0
        with pytest.raises(ConfigError, match="disc_number"):
            cfg.validate()


# ---------------------------------------------------------------------------
# ShowConfig — construction and validation
# ---------------------------------------------------------------------------

class TestShowConfigValidate:
    def _base_show(self, tmp_path: Path) -> ShowConfig:
        return ShowConfig(
            show_name="Test Show",
            season_number=1,
            start_episode=1,
            output_dir=tmp_path,
        )

    def test_valid_show_config_does_not_raise(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.validate()  # should not raise

    def test_blank_show_name_raises(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.show_name = "   "
        with pytest.raises(ConfigError, match="show_name"):
            cfg.validate()

    def test_season_number_negative_raises(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.season_number = -1
        with pytest.raises(ConfigError, match="season_number"):
            cfg.validate()

    def test_season_number_zero_is_valid(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.season_number = 0
        cfg.validate()  # should not raise; 0 = specials season

    def test_start_episode_zero_raises(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.start_episode = 0
        with pytest.raises(ConfigError, match="start_episode"):
            cfg.validate()

    def test_start_episode_negative_raises(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.start_episode = -5
        with pytest.raises(ConfigError, match="start_episode"):
            cfg.validate()

    def test_multiple_show_errors_reported_together(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.season_number = -1
        cfg.start_episode = 0
        with pytest.raises(ConfigError) as exc_info:
            cfg.validate()
        message = str(exc_info.value)
        assert "season_number" in message
        assert "start_episode" in message

    def test_shared_min_length_check_applies(self, tmp_path: Path) -> None:
        cfg = self._base_show(tmp_path)
        cfg.min_length = 0
        with pytest.raises(ConfigError, match="min_length"):
            cfg.validate()


# ---------------------------------------------------------------------------
# MovieConfig.from_click_params()
# ---------------------------------------------------------------------------

class TestFromClickParams:
    def test_round_trip_defaults(self, tmp_path: Path) -> None:
        cfg = MovieConfig.from_click_params(
            movie_name="Lawrence of Arabia",
            movie_year=1962,
            output_dir=tmp_path,
        )
        assert cfg.movie_name == "Lawrence of Arabia"
        assert cfg.movie_year == 1962
        assert cfg.rip_mode == "main"
        assert cfg.encode_format == "none"
        assert cfg.device == "/dev/sr0"
        assert cfg.min_length == 10
        assert cfg.keep_original is False
        assert cfg.eject_on_complete is True

    def test_disc_number_passed_through(self, tmp_path: Path) -> None:
        cfg = MovieConfig.from_click_params(
            movie_name="Lawrence of Arabia",
            movie_year=1962,
            output_dir=tmp_path,
            disc_number=2,
        )
        assert cfg.disc_number == 2
