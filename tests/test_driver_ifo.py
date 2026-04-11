"""Tests for ``diskripr.drivers.ifo``.

Strategy: ``IfoDriver.read_disc()`` is tested by mocking
``diskripr.drivers.ifo.load_vts_pgci`` at the pyparsedvd boundary so no real
IFO binary files are required.  Real ``VTS_XX_0.IFO`` filenames are created as
empty files in ``tmp_path`` to satisfy the filesystem checks; the parse result
is provided entirely by the mock.

Module-level helpers ``_playtime_to_seconds`` and ``_parse_vts_index`` are
tested directly without any filesystem or mock involvement.

Fixture files: none (mocking is used instead of binary IFO fixtures).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pyparsedvd.vts_ifo import PlaybackTime, ProgramChain

from diskripr.drivers.ifo import (
    IfoDriver,
    IfoVts,
    _parse_vts_index,
    _playtime_to_seconds,
)


# ---------------------------------------------------------------------------
# _playtime_to_seconds — pure conversion helper
# ---------------------------------------------------------------------------

class TestPlaytimeToSeconds:
    def test_zero(self) -> None:
        time = PlaybackTime(fps=1, hours=0, minutes=0, seconds=0, frames=0)
        assert _playtime_to_seconds(time) == 0

    def test_seconds_only(self) -> None:
        time = PlaybackTime(fps=1, hours=0, minutes=0, seconds=32, frames=0)
        assert _playtime_to_seconds(time) == 32

    def test_minutes_and_seconds(self) -> None:
        time = PlaybackTime(fps=1, hours=0, minutes=2, seconds=30, frames=0)
        assert _playtime_to_seconds(time) == 150

    def test_full_duration(self) -> None:
        time = PlaybackTime(fps=1, hours=1, minutes=57, seconds=25, frames=0)
        assert _playtime_to_seconds(time) == 7045

    def test_frames_are_discarded(self) -> None:
        # Frames do not contribute to the integer-second result.
        time_no_frames = PlaybackTime(fps=1, hours=0, minutes=1, seconds=0, frames=0)
        time_with_frames = PlaybackTime(fps=1, hours=0, minutes=1, seconds=0, frames=15)
        assert _playtime_to_seconds(time_no_frames) == _playtime_to_seconds(time_with_frames)


# ---------------------------------------------------------------------------
# _parse_vts_index — filename parsing helper
# ---------------------------------------------------------------------------

class TestParseVtsIndex:
    def test_standard_filename(self) -> None:
        assert _parse_vts_index("VTS_01_0.IFO") == 1

    def test_two_digit_index(self) -> None:
        assert _parse_vts_index("VTS_12_0.IFO") == 12

    def test_lowercase_extension(self) -> None:
        # Case-insensitive match.
        assert _parse_vts_index("VTS_01_0.ifo") == 1

    def test_non_matching_filename_returns_none(self) -> None:
        assert _parse_vts_index("VIDEO_TS.IFO") is None

    def test_vts_title_ifo_not_matched(self) -> None:
        # VTS_01_1.IFO is a title IFO, not the VTS info IFO — should not match.
        assert _parse_vts_index("VTS_01_1.IFO") is None

    def test_empty_string_returns_none(self) -> None:
        assert _parse_vts_index("") is None


# ---------------------------------------------------------------------------
# Helpers for building mock pyparsedvd objects
# ---------------------------------------------------------------------------

def _make_playtime(hours: int = 0, minutes: int = 0, seconds: int = 0) -> PlaybackTime:
    return PlaybackTime(fps=1, hours=hours, minutes=minutes, seconds=seconds, frames=0)


def _make_pgc(
    hours: int,
    minutes: int,
    seconds: int,
    cell_seconds: list[int],
) -> ProgramChain:
    duration = _make_playtime(hours, minutes, seconds)
    cell_times = [_make_playtime(seconds=sec) for sec in cell_seconds]
    return ProgramChain(
        duration=duration,
        nb_program=len(cell_seconds),
        playback_times=cell_times,
    )


def _make_vtspgci(pgcs: list[ProgramChain]) -> MagicMock:
    mock = MagicMock()
    mock.nb_program_chains = len(pgcs)
    mock.program_chains = pgcs
    return mock


# ---------------------------------------------------------------------------
# read_disc() — directory and filesystem checks
# ---------------------------------------------------------------------------

class TestReadDiscFilesystem:
    def test_returns_none_when_path_does_not_exist(self, tmp_path: Path) -> None:
        driver = IfoDriver()
        result = driver.read_disc(tmp_path / "nonexistent")
        assert result is None

    def test_returns_none_when_path_is_a_file(self, tmp_path: Path) -> None:
        fake_file = tmp_path / "not_a_dir"
        fake_file.touch()
        driver = IfoDriver()
        result = driver.read_disc(fake_file)
        assert result is None

    def test_returns_none_when_no_vts_ifo_files(self, tmp_path: Path) -> None:
        (tmp_path / "VIDEO_TS.IFO").touch()  # present but not a VTS IFO
        driver = IfoDriver()
        result = driver.read_disc(tmp_path)
        assert result is None


# ---------------------------------------------------------------------------
# read_disc() — correct IfoVts structure for a valid VIDEO_TS path
# ---------------------------------------------------------------------------

class TestReadDiscParsing:
    def test_single_vts_correct_structure(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        pgc = _make_pgc(hours=1, minutes=57, seconds=25, cell_seconds=[300, 478, 420])
        mock_vtspgci = _make_vtspgci([pgc])

        driver = IfoDriver()
        with patch("diskripr.drivers.ifo.load_vts_pgci", return_value=mock_vtspgci):
            result = driver.read_disc(tmp_path)

        assert result is not None
        assert 1 in result
        vts = result[1]
        assert isinstance(vts, IfoVts)
        assert vts.vts_index == 1
        assert vts.pgc_count == 1

    def test_pgc_duration_converted_correctly(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        pgc = _make_pgc(hours=1, minutes=57, seconds=25, cell_seconds=[300])
        mock_vtspgci = _make_vtspgci([pgc])

        driver = IfoDriver()
        with patch("diskripr.drivers.ifo.load_vts_pgci", return_value=mock_vtspgci):
            result = driver.read_disc(tmp_path)

        assert result is not None
        assert result[1].pgcs[0].duration_seconds == 7045  # 1*3600 + 57*60 + 25

    def test_cell_durations_converted_correctly(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        pgc = _make_pgc(hours=0, minutes=10, seconds=0, cell_seconds=[120, 180, 300])
        mock_vtspgci = _make_vtspgci([pgc])

        driver = IfoDriver()
        with patch("diskripr.drivers.ifo.load_vts_pgci", return_value=mock_vtspgci):
            result = driver.read_disc(tmp_path)

        assert result is not None
        assert result[1].pgcs[0].cell_durations == [120, 180, 300]

    def test_nb_program_stored(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        pgc = _make_pgc(hours=0, minutes=45, seconds=0, cell_seconds=[100, 200, 300, 100])
        mock_vtspgci = _make_vtspgci([pgc])

        driver = IfoDriver()
        with patch("diskripr.drivers.ifo.load_vts_pgci", return_value=mock_vtspgci):
            result = driver.read_disc(tmp_path)

        assert result is not None
        assert result[1].pgcs[0].nb_program == 4

    def test_multiple_vts_files_parsed(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        (tmp_path / "VTS_02_0.IFO").touch()
        pgc1 = _make_pgc(hours=1, minutes=45, seconds=0, cell_seconds=[600, 600, 600])
        pgc2 = _make_pgc(hours=0, minutes=15, seconds=0, cell_seconds=[300, 300, 300])
        mock1 = _make_vtspgci([pgc1])
        mock2 = _make_vtspgci([pgc2])

        driver = IfoDriver()
        with patch("diskripr.drivers.ifo.load_vts_pgci", side_effect=[mock1, mock2]):
            result = driver.read_disc(tmp_path)

        assert result is not None
        assert set(result.keys()) == {1, 2}
        assert result[1].vts_index == 1
        assert result[2].vts_index == 2

    def test_multiple_pgcs_in_one_vts(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        pgc1 = _make_pgc(hours=0, minutes=20, seconds=0, cell_seconds=[600, 600])
        pgc2 = _make_pgc(hours=0, minutes=18, seconds=0, cell_seconds=[540, 540])
        mock_vtspgci = _make_vtspgci([pgc1, pgc2])

        driver = IfoDriver()
        with patch("diskripr.drivers.ifo.load_vts_pgci", return_value=mock_vtspgci):
            result = driver.read_disc(tmp_path)

        assert result is not None
        vts = result[1]
        assert vts.pgc_count == 2
        assert len(vts.pgcs) == 2
        assert vts.pgcs[0].duration_seconds == 1200
        assert vts.pgcs[1].duration_seconds == 1080


# ---------------------------------------------------------------------------
# read_disc() — graceful failure paths
# ---------------------------------------------------------------------------

class TestReadDiscGracefulFailure:
    def test_returns_none_when_parse_raises(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        driver = IfoDriver()
        with patch(
            "diskripr.drivers.ifo.load_vts_pgci",
            side_effect=ValueError("malformed IFO"),
        ):
            result = driver.read_disc(tmp_path)
        assert result is None

    def test_skips_failed_ifo_keeps_successful(self, tmp_path: Path) -> None:
        (tmp_path / "VTS_01_0.IFO").touch()
        (tmp_path / "VTS_02_0.IFO").touch()
        pgc = _make_pgc(hours=0, minutes=10, seconds=0, cell_seconds=[300, 300])
        good_mock = _make_vtspgci([pgc])

        driver = IfoDriver()
        with patch(
            "diskripr.drivers.ifo.load_vts_pgci",
            side_effect=[ValueError("bad IFO"), good_mock],
        ):
            result = driver.read_disc(tmp_path)

        assert result is not None
        assert 1 not in result
        assert 2 in result

    def test_is_available_returns_true(self) -> None:
        driver = IfoDriver()
        assert driver.is_available() is True
