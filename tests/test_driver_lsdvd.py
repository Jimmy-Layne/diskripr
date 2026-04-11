"""Tests for ``diskripr.drivers.lsdvd``.

Strategy: the parsing logic (``LsdvdDriver._parse``) is a pure static method
that accepts a string, so the majority of tests call it directly against
fixture text or inline strings without any subprocess involvement.

Tests for ``read_disc`` mock ``is_available`` and ``run`` to avoid requiring
a physical drive or the lsdvd binary.

Fixture files used:
- ``tests/data/lsdvd/disc_normal.txt``  — real lsdvd output; 2 titles on VTS 1 and 2.
- ``tests/data/lsdvd/disc_encrypted.txt`` — lsdvd error output; no disc title.
- ``tests/data/lsdvd/multi_vts.txt``    — 3 titles; extras on VTS 2.
- ``tests/data/lsdvd/shared_vts.txt``   — 4 titles sharing VTS 2 for titles 2–4.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest

from conftest import DATA_DIR, load_fixture
from diskripr.drivers.base import ToolError
from diskripr.drivers.lsdvd import LsdvdDisc, LsdvdDriver, LsdvdTitle


# ---------------------------------------------------------------------------
# _parse() — normal disc fixture
# ---------------------------------------------------------------------------

class TestParseNormalDisc:
    def test_returns_lsdvd_disc(self) -> None:
        text = load_fixture("lsdvd", "disc_normal.txt")
        result = LsdvdDriver._parse(text)
        assert isinstance(result, LsdvdDisc)

    def test_disc_title(self) -> None:
        text = load_fixture("lsdvd", "disc_normal.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.disc_title == "ROSENCRANTZ_AND_GUILDENSTERN"

    def test_two_titles_parsed(self) -> None:
        text = load_fixture("lsdvd", "disc_normal.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert len(result.titles) == 2

    def test_main_feature_title(self) -> None:
        text = load_fixture("lsdvd", "disc_normal.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        main_title = result.titles[0]
        assert main_title.index == 1
        assert main_title.duration == "01:57:30"
        assert main_title.vts_number == 1
        assert main_title.ttn == 1
        assert main_title.audio_stream_count == 3
        assert main_title.cell_count == 13

    def test_short_title(self) -> None:
        text = load_fixture("lsdvd", "disc_normal.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        short_title = result.titles[1]
        assert short_title.index == 2
        assert short_title.duration == "00:00:32"
        assert short_title.vts_number == 2
        assert short_title.ttn == 1
        assert short_title.audio_stream_count == 1
        assert short_title.cell_count == 1

    def test_main_vts_from_longest_track(self) -> None:
        text = load_fixture("lsdvd", "disc_normal.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        # Longest track: 01, which is on VTS 01.
        assert result.main_vts == 1


# ---------------------------------------------------------------------------
# _parse() — encrypted / failure cases
# ---------------------------------------------------------------------------

class TestParseEncryptedDisc:
    def test_returns_none_when_no_disc_title(self) -> None:
        # disc_encrypted.txt contains libdvdread error lines but no Disc Title.
        text = load_fixture("lsdvd", "disc_encrypted.txt")
        result = LsdvdDriver._parse(text)
        assert result is None

    def test_empty_string_returns_none(self) -> None:
        result = LsdvdDriver._parse("")
        assert result is None

    def test_whitespace_only_returns_none(self) -> None:
        result = LsdvdDriver._parse("   \n  \n")
        assert result is None


# ---------------------------------------------------------------------------
# _parse() — edge cases
# ---------------------------------------------------------------------------

class TestParseEdgeCases:
    # Single-digit hour: lsdvd may emit "2:11:37.00" instead of "02:11:37.00".
    _SINGLE_DIGIT_HOUR_OUTPUT = (
        "Disc Title: SOME_DISC\n"
        "Title: 01, Length: 2:11:37.00 Chapters: 5, Cells: 5, "
        "Audio streams: 1, Subpictures: 0\n"
    )

    def test_single_digit_hour_normalised(self) -> None:
        result = LsdvdDriver._parse(self._SINGLE_DIGIT_HOUR_OUTPUT)
        assert result is not None
        title = result.titles[0]
        assert title.duration == "02:11:37"
        assert title.audio_stream_count == 1
        assert title.cell_count == 5

    def test_disc_title_only_no_titles(self) -> None:
        text = "Disc Title: EMPTY_DISC\n"
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.disc_title == "EMPTY_DISC"
        assert result.titles == []
        assert result.main_vts is None

    def test_main_vts_none_when_longest_track_absent(self) -> None:
        text = (
            "Disc Title: NO_LONGEST\n"
            "Title: 01, Length: 01:00:00.000 Chapters: 10, Cells: 10, "
            "Audio streams: 02, Subpictures: 00\n"
            "\tVTS: 01, TTN: 01, FPS: 29.97, Format: NTSC, Aspect ratio: 16/9, "
            "Width: 720, Height: 480, DF: Letterbox\n"
        )
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.main_vts is None

    def test_unparseable_title_line_is_skipped(self) -> None:
        text = (
            "Disc Title: SOME_DISC\n"
            "Title: XX, Length: invalid\n"  # malformed — should be skipped
            "Title: 01, Length: 01:30:00.00 Chapters: 5, Cells: 5, "
            "Audio streams: 1, Subpictures: 0\n"
        )
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert len(result.titles) == 1
        assert result.titles[0].index == 1

    def test_disc_title_with_leading_and_trailing_spaces(self) -> None:
        text = "Disc Title:   SPACED_DISC   \n"
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.disc_title == "SPACED_DISC"


# ---------------------------------------------------------------------------
# _parse() — multi_vts fixture (extras on VTS 2)
# ---------------------------------------------------------------------------

class TestParseMultiVts:
    def test_three_titles_parsed(self) -> None:
        text = load_fixture("lsdvd", "multi_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert len(result.titles) == 3

    def test_main_title_on_vts_1(self) -> None:
        text = load_fixture("lsdvd", "multi_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.titles[0].vts_number == 1
        assert result.titles[0].ttn == 1

    def test_extras_on_vts_2(self) -> None:
        text = load_fixture("lsdvd", "multi_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.titles[1].vts_number == 2
        assert result.titles[1].ttn == 1
        assert result.titles[2].vts_number == 2
        assert result.titles[2].ttn == 2

    def test_main_vts_is_1(self) -> None:
        text = load_fixture("lsdvd", "multi_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.main_vts == 1

    def test_audio_and_cell_counts(self) -> None:
        text = load_fixture("lsdvd", "multi_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.titles[0].audio_stream_count == 2
        assert result.titles[0].cell_count == 15
        assert result.titles[1].audio_stream_count == 1
        assert result.titles[1].cell_count == 3


# ---------------------------------------------------------------------------
# _parse() — shared_vts fixture (titles 2–4 share VTS 2)
# ---------------------------------------------------------------------------

class TestParseSharedVts:
    def test_four_titles_parsed(self) -> None:
        text = load_fixture("lsdvd", "shared_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert len(result.titles) == 4

    def test_title_1_on_vts_1(self) -> None:
        text = load_fixture("lsdvd", "shared_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.titles[0].vts_number == 1

    def test_titles_2_through_4_share_vts_2(self) -> None:
        text = load_fixture("lsdvd", "shared_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.titles[1].vts_number == 2
        assert result.titles[1].ttn == 1
        assert result.titles[2].vts_number == 2
        assert result.titles[2].ttn == 2
        assert result.titles[3].vts_number == 2
        assert result.titles[3].ttn == 3

    def test_main_vts_is_1(self) -> None:
        text = load_fixture("lsdvd", "shared_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.main_vts == 1

    def test_disc_title(self) -> None:
        text = load_fixture("lsdvd", "shared_vts.txt")
        result = LsdvdDriver._parse(text)
        assert result is not None
        assert result.disc_title == "SHARED_VTS_DISC"


# ---------------------------------------------------------------------------
# read_disc() — subprocess boundary
# ---------------------------------------------------------------------------

class TestReadDisc:
    def test_returns_none_when_lsdvd_not_on_path(self) -> None:
        driver = LsdvdDriver()
        with patch.object(driver, "is_available", return_value=False):
            result = driver.read_disc("/dev/sr0")
        assert result is None

    def test_returns_none_on_tool_error(self) -> None:
        driver = LsdvdDriver()
        with patch.object(driver, "is_available", return_value=True):
            with patch.object(
                driver,
                "run",
                side_effect=ToolError(["lsdvd", "-x", "/dev/sr0"], 1, "error"),
            ):
                result = driver.read_disc("/dev/sr0")
        assert result is None

    def test_returns_none_when_output_has_no_disc_title(self) -> None:
        driver = LsdvdDriver()
        completed = subprocess.CompletedProcess(
            ["lsdvd"], 0, "libdvdread: some error\n", ""
        )
        with patch.object(driver, "is_available", return_value=True):
            with patch.object(driver, "run", return_value=completed):
                result = driver.read_disc("/dev/sr0")
        assert result is None

    def test_returns_lsdvd_disc_on_success(self) -> None:
        driver = LsdvdDriver()
        fixture_text = load_fixture("lsdvd", "disc_normal.txt")
        completed = subprocess.CompletedProcess(["lsdvd"], 0, fixture_text, "")
        with patch.object(driver, "is_available", return_value=True):
            with patch.object(driver, "run", return_value=completed):
                result = driver.read_disc("/dev/sr0")
        assert result is not None
        assert result.disc_title == "ROSENCRANTZ_AND_GUILDENSTERN"
        assert len(result.titles) == 2
