"""Tests for ``diskripr.config``.

Covers:
- ``Config.__post_init__``: Path coercion, temp_dir defaulting, quality
  defaults for each encoder, ``DISKRIPR_TEMP_DIR`` environment variable.
- ``Config.validate()``: all documented error conditions.
- ``Config.from_click_params()``: round-trip construction.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from diskripr.config import Config, ConfigError


# ---------------------------------------------------------------------------
# __post_init__ — coercion and defaults
# ---------------------------------------------------------------------------

class TestConfigPostInit:
    def test_string_output_dir_coerced_to_path(self, tmp_path: Path) -> None:
        cfg = Config(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=str(tmp_path / "output"),  # type: ignore[arg-type]
        )
        assert isinstance(cfg.output_dir, Path)

    def test_temp_dir_defaults_to_output_dir_dotmp(self, tmp_path: Path) -> None:
        output = tmp_path / "output"
        cfg = Config(movie_name="Test Movie", movie_year=2000, output_dir=output)
        assert cfg.temp_dir == output / ".tmp"

    def test_diskripr_temp_dir_env_var_overrides_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        custom_tmp = str(tmp_path / "custom_tmp")
        monkeypatch.setenv("DISKRIPR_TEMP_DIR", custom_tmp)
        cfg = Config(movie_name="Test Movie", movie_year=2000, output_dir=tmp_path)
        assert cfg.temp_dir == Path(custom_tmp)

    def test_explicit_temp_dir_not_overridden_by_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        explicit = tmp_path / "my_tmp"
        monkeypatch.setenv("DISKRIPR_TEMP_DIR", str(tmp_path / "env_tmp"))
        cfg = Config(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=tmp_path,
            temp_dir=explicit,
        )
        assert cfg.temp_dir == explicit

    def test_h264_sets_default_quality_20(self, tmp_path: Path) -> None:
        cfg = Config(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=tmp_path,
            encode_format="h264",
        )
        assert cfg.quality == 20

    def test_h265_sets_default_quality_22(self, tmp_path: Path) -> None:
        cfg = Config(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=tmp_path,
            encode_format="h265",
        )
        assert cfg.quality == 22

    def test_none_encode_format_leaves_quality_as_none(self, tmp_path: Path) -> None:
        cfg = Config(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=tmp_path,
            encode_format="none",
        )
        assert cfg.quality is None

    def test_explicit_quality_not_overridden(self, tmp_path: Path) -> None:
        cfg = Config(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=tmp_path,
            encode_format="h264",
            quality=18,
        )
        assert cfg.quality == 18


# ---------------------------------------------------------------------------
# validate() — error conditions
# ---------------------------------------------------------------------------

class TestConfigValidate:
    def _base_config(self, tmp_path: Path) -> Config:
        """Return a valid Config; tests mutate individual fields."""
        return Config(
            movie_name="Test Movie",
            movie_year=2000,
            output_dir=tmp_path,
        )

    def test_valid_config_does_not_raise(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.validate()  # should not raise

    def test_blank_movie_name_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.movie_name = "   "
        with pytest.raises(ConfigError, match="movie_name"):
            cfg.validate()

    def test_year_below_minimum_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.movie_year = 1887
        with pytest.raises(ConfigError, match="movie_year"):
            cfg.validate()

    def test_year_above_maximum_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.movie_year = 2100
        with pytest.raises(ConfigError, match="movie_year"):
            cfg.validate()

    def test_disc_number_zero_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.disc_number = 0
        with pytest.raises(ConfigError, match="disc_number"):
            cfg.validate()

    def test_disc_number_negative_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.disc_number = -1
        with pytest.raises(ConfigError, match="disc_number"):
            cfg.validate()

    def test_disc_number_one_is_valid(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.disc_number = 1
        cfg.validate()  # should not raise

    def test_min_length_zero_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.min_length = 0
        with pytest.raises(ConfigError, match="min_length"):
            cfg.validate()

    def test_min_length_negative_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.min_length = -10
        with pytest.raises(ConfigError, match="min_length"):
            cfg.validate()

    def test_quality_above_51_raises(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.encode_format = "h264"
        cfg.quality = 52
        with pytest.raises(ConfigError, match="quality"):
            cfg.validate()

    def test_quality_at_51_is_valid(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
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
        cfg = self._base_config(tmp_path)
        cfg.encode_format = "h264"
        cfg.quality = 20
        with patch(
            "diskripr.drivers.handbrake.HandBrakeDriver.is_available",
            return_value=False,
        ):
            with pytest.raises(ConfigError, match="HandBrakeCLI"):
                cfg.validate()

    def test_multiple_errors_reported_together(self, tmp_path: Path) -> None:
        cfg = self._base_config(tmp_path)
        cfg.movie_name = ""
        cfg.movie_year = 1800
        with pytest.raises(ConfigError) as exc_info:
            cfg.validate()
        message = str(exc_info.value)
        assert "movie_name" in message
        assert "movie_year" in message


# ---------------------------------------------------------------------------
# from_click_params()
# ---------------------------------------------------------------------------

class TestFromClickParams:
    def test_round_trip_defaults(self, tmp_path: Path) -> None:
        cfg = Config.from_click_params(
            movie_name="Lawrence of Arabia",
            movie_year=1962,
            output_dir=tmp_path,
        )
        assert cfg.movie_name == "Lawrence of Arabia"
        assert cfg.movie_year == 1962
        assert cfg.rip_mode == "main"
        assert cfg.encode_format == "none"
        assert cfg.device == "/dev/sr0"
        assert cfg.min_length == 30
        assert cfg.keep_original is False
        assert cfg.eject_on_complete is True

    def test_disc_number_passed_through(self, tmp_path: Path) -> None:
        cfg = Config.from_click_params(
            movie_name="Lawrence of Arabia",
            movie_year=1962,
            output_dir=tmp_path,
            disc_number=2,
        )
        assert cfg.disc_number == 2
