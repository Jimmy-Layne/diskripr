"""Tests for ``diskripr.drivers.makemkv``.

Strategy:
- Module-level helpers (``_duration_to_seconds``, ``_classify_title_type``)
  and static parsing methods (``_collect_tinfo``, ``_build_title``,
  ``_handle_prgv``, ``_handle_msg``, ``_handle_tsav``) are tested directly
  without subprocess involvement.
- ``scan_drives`` and ``scan_titles`` are tested by mocking ``run`` to return
  fixture file content as stdout.
- ``rip_title`` is tested by mocking ``stream`` to yield lines from a
  constructed inline fixture.

Fixture files used:
- ``tests/data/makemkv/scan_drives.txt``
- ``tests/data/makemkv/scan_titles.txt``
- ``tests/data/makemkv/rip_progress.txt``

MSG error detection note:
  The driver uses the MSG flags field (second CSV field) to distinguish errors
  from informational status messages.  MakeMKV uses flags=0 for informational
  5xxx codes (e.g. 5011 "Operation successfully completed", 5036 "Copy
  complete") and non-zero flags (bit 1 set) for real error messages.  Success
  is also confirmed by the presence of an output MKV file; error_message is
  only surfaced when no output file was produced.
"""

from __future__ import annotations

import subprocess
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from conftest import load_fixture
from diskripr.drivers.makemkv import (
    MakeMKVDriver,
    _classify_title_type,
    _duration_to_seconds,
)
from diskripr.util.progress import ProgressEvent

# Inline fixtures for rip_title tests.
# _CLEAN_RIP_OUTPUT includes realistic 5xxx status codes (flags=0) that MakeMKV
# emits on a successful rip.  These should NOT be treated as errors.
_CLEAN_RIP_OUTPUT = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
MSG:3007,0,0,"Using direct disc access mode","Using direct disc access mode"
PRGV:0,0,65536
PRGV:32768,32768,65536
PRGV:65536,65536,65536
TSAV:1,"A1_t01.mkv"
MSG:5011,0,0,"Operation successfully completed","Operation successfully completed"
MSG:5036,0,0,"Copy complete. 1 titles saved.","Copy complete. %1 titles saved.","1"
"""

# Real MakeMKV error messages use non-zero flags (bit 1 set).
_ERROR_RIP_OUTPUT = """\
MSG:1005,0,1,"MakeMKV v1.18.3 linux(x64-release) started","%1 started","MakeMKV v1.18.3 linux(x64-release)"
MSG:6003,2,0,"Cannot decrypt title","Cannot decrypt title"
"""


# ---------------------------------------------------------------------------
# _duration_to_seconds
# ---------------------------------------------------------------------------

class TestDurationToSeconds:
    def test_zero(self) -> None:
        assert _duration_to_seconds("00:00:00") == 0

    def test_seconds_only(self) -> None:
        assert _duration_to_seconds("00:00:32") == 32

    def test_minutes_and_seconds(self) -> None:
        assert _duration_to_seconds("00:10:00") == 600

    def test_full_duration(self) -> None:
        assert _duration_to_seconds("01:57:25") == 7045  # from fixture

    def test_single_digit_hours(self) -> None:
        assert _duration_to_seconds("2:11:37") == 7897

    def test_invalid_returns_zero(self) -> None:
        assert _duration_to_seconds("not-a-time") == 0


# ---------------------------------------------------------------------------
# _classify_title_type
# ---------------------------------------------------------------------------

class TestClassifyTitleType:
    def test_longest_title_is_main(self) -> None:
        assert _classify_title_type(7045, is_longest=True) == "main"

    def test_longest_also_applies_at_any_duration(self) -> None:
        # Even a 10-second title is "main" if it is the only/longest title.
        assert _classify_title_type(10, is_longest=True) == "main"

    def test_feature_length_threshold(self) -> None:
        # Exactly 45 minutes → "feature-length".
        assert _classify_title_type(45 * 60, is_longest=False) == "feature-length"

    def test_above_feature_length_threshold(self) -> None:
        assert _classify_title_type(120 * 60, is_longest=False) == "feature-length"

    def test_extra_threshold(self) -> None:
        # Exactly 10 minutes → "extra".
        assert _classify_title_type(10 * 60, is_longest=False) == "extra"

    def test_between_extra_and_feature_length(self) -> None:
        assert _classify_title_type(20 * 60, is_longest=False) == "extra"

    def test_short_below_extra_threshold(self) -> None:
        assert _classify_title_type(9 * 60 + 59, is_longest=False) == "short"

    def test_zero_duration_is_short(self) -> None:
        assert _classify_title_type(0, is_longest=False) == "short"


# ---------------------------------------------------------------------------
# _collect_tinfo — static parsing helper
# ---------------------------------------------------------------------------

class TestCollectTinfo:
    def test_parses_fixture_attrs(self) -> None:
        text = load_fixture("makemkv", "scan_titles.txt")
        attrs = MakeMKVDriver._collect_tinfo(text)
        title_zero = attrs[0]
        # Chapter count attribute (8).
        assert title_zero[8] == "13"
        # Duration attribute (9) — single-digit hour form from MakeMKV.
        assert title_zero[9] == "1:57:25"
        # Size in bytes attribute (11).
        assert title_zero[11] == "6979534848"

    def test_ignores_non_tinfo_lines(self) -> None:
        text = (
            "MSG:1005,0,1,\"started\"\n"
            "DRV:0,2,999,1,\"\",\"\",\"/dev/sr0\"\n"
            "TINFO:0,9,0,\"01:00:00\"\n"
        )
        attrs = MakeMKVDriver._collect_tinfo(text)
        assert 0 in attrs
        assert len(attrs) == 1

    def test_skips_unparseable_line(self) -> None:
        text = "TINFO:bad-line\nTINFO:1,9,0,\"00:30:00\"\n"
        attrs = MakeMKVDriver._collect_tinfo(text)
        assert 1 in attrs

    def test_multiple_titles(self) -> None:
        text = (
            "TINFO:0,9,0,\"02:00:00\"\n"
            "TINFO:1,9,0,\"00:15:00\"\n"
            "TINFO:2,9,0,\"00:03:00\"\n"
        )
        attrs = MakeMKVDriver._collect_tinfo(text)
        assert len(attrs) == 3


# ---------------------------------------------------------------------------
# _handle_prgv
# ---------------------------------------------------------------------------

class TestHandlePrgv:
    def test_fires_callback_with_correct_event(self) -> None:
        events: list[ProgressEvent] = []
        MakeMKVDriver._handle_prgv("PRGV:32768,32768,65536", "some msg", events.append)
        assert len(events) == 1
        evt = events[0]
        assert evt.stage == "rip"
        assert evt.current == 32768
        assert evt.total == 65536
        assert evt.message == "some msg"

    def test_100_percent_event(self) -> None:
        events: list[ProgressEvent] = []
        MakeMKVDriver._handle_prgv("PRGV:65536,65536,65536", None, events.append)
        assert events[0].current == events[0].total

    def test_no_callback_does_not_raise(self) -> None:
        MakeMKVDriver._handle_prgv("PRGV:0,0,65536", None, None)

    def test_malformed_line_does_not_raise(self) -> None:
        events: list[ProgressEvent] = []
        MakeMKVDriver._handle_prgv("PRGV:bad", None, events.append)
        assert events == []


# ---------------------------------------------------------------------------
# _handle_msg
# ---------------------------------------------------------------------------

class TestHandleMsg:
    def test_low_code_sets_last_message_only(self) -> None:
        last_msg, err_msg = MakeMKVDriver._handle_msg(
            'MSG:3007,0,0,"Using direct disc access mode","Using direct disc access mode"',
            None, None,
        )
        assert last_msg == "Using direct disc access mode"
        assert err_msg is None

    def test_nonzero_flags_sets_error_message(self) -> None:
        last_msg, err_msg = MakeMKVDriver._handle_msg(
            'MSG:6003,2,0,"Cannot decrypt title","Cannot decrypt title"',
            None, None,
        )
        assert err_msg == "Cannot decrypt title"

    def test_5xxx_code_with_zero_flags_is_not_an_error(self) -> None:
        """Informational 5xxx completion codes (flags=0) must not set error_message."""
        _, err_msg = MakeMKVDriver._handle_msg(
            'MSG:5011,0,0,"Operation successfully completed","Operation successfully completed"',
            None, None,
        )
        assert err_msg is None

    def test_error_message_preserved_across_later_informational_msg(self) -> None:
        _, first_err = MakeMKVDriver._handle_msg(
            'MSG:6003,2,0,"Decrypt error","Decrypt error"', None, None,
        )
        _, second_err = MakeMKVDriver._handle_msg(
            'MSG:5036,0,0,"Copy complete. 1 titles saved.","Copy complete. %1 titles saved.","1"',
            None, first_err,
        )
        assert second_err == "Decrypt error"

    def test_malformed_line_returns_unchanged_state(self) -> None:
        last_msg, err_msg = MakeMKVDriver._handle_msg("MSG:bad", "prev", "prev_err")
        assert last_msg == "prev"
        assert err_msg == "prev_err"


# ---------------------------------------------------------------------------
# _handle_tsav
# ---------------------------------------------------------------------------

class TestHandleTsav:
    def test_parses_filename(self) -> None:
        result = MakeMKVDriver._handle_tsav('TSAV:1,"A1_t01.mkv"')
        assert result == "A1_t01.mkv"

    def test_returns_none_on_missing_field(self) -> None:
        result = MakeMKVDriver._handle_tsav("TSAV:1")
        assert result is None

    def test_returns_none_on_empty_payload(self) -> None:
        result = MakeMKVDriver._handle_tsav("TSAV:")
        assert result is None


# ---------------------------------------------------------------------------
# scan_drives() — mocked subprocess
# ---------------------------------------------------------------------------

class TestScanDrives:
    def _run_with_fixture(self, driver: MakeMKVDriver) -> list:
        fixture_text = load_fixture("makemkv", "scan_drives.txt")
        completed = subprocess.CompletedProcess(
            ["makemkvcon"], 0, fixture_text, ""
        )
        with patch.object(driver, "require_available"):
            with patch.object(driver, "run", return_value=completed):
                return driver.scan_drives()

    def test_returns_one_accessible_drive(self) -> None:
        driver = MakeMKVDriver()
        drives = self._run_with_fixture(driver)
        assert len(drives) == 1

    def test_drive_device_path(self) -> None:
        driver = MakeMKVDriver()
        drives = self._run_with_fixture(driver)
        assert drives[0].device == "/dev/sr0"

    def test_drive_index(self) -> None:
        driver = MakeMKVDriver()
        drives = self._run_with_fixture(driver)
        assert drives[0].drive_index == 0

    def test_inaccessible_drives_excluded(self) -> None:
        # Slots 1–15 have flag=256 in the fixture and must be filtered out.
        driver = MakeMKVDriver()
        drives = self._run_with_fixture(driver)
        assert all(drv.device != "" for drv in drives)


# ---------------------------------------------------------------------------
# scan_titles() — mocked subprocess
# ---------------------------------------------------------------------------

class TestScanTitles:
    def _run_with_fixture(self, driver: MakeMKVDriver, drive_index: int = 0) -> list:
        fixture_text = load_fixture("makemkv", "scan_titles.txt")
        completed = subprocess.CompletedProcess(
            ["makemkvcon"], 0, fixture_text, ""
        )
        with patch.object(driver, "require_available"):
            with patch.object(driver, "run", return_value=completed):
                return driver.scan_titles(drive_index)

    def test_returns_one_title(self) -> None:
        driver = MakeMKVDriver()
        titles = self._run_with_fixture(driver)
        assert len(titles) == 1

    def test_title_index(self) -> None:
        driver = MakeMKVDriver()
        titles = self._run_with_fixture(driver)
        assert titles[0].index == 0

    def test_duration_normalised_to_two_digit_hours(self) -> None:
        # Fixture has "1:57:25" (single-digit hour) — driver should normalise.
        driver = MakeMKVDriver()
        titles = self._run_with_fixture(driver)
        assert titles[0].duration == "01:57:25"

    def test_chapter_count(self) -> None:
        driver = MakeMKVDriver()
        titles = self._run_with_fixture(driver)
        assert titles[0].chapter_count == 13

    def test_size_bytes(self) -> None:
        driver = MakeMKVDriver()
        titles = self._run_with_fixture(driver)
        assert titles[0].size_bytes == 6_979_534_848

    def test_title_type_main_when_only_title(self) -> None:
        # The single title is necessarily the longest, so it is "main".
        driver = MakeMKVDriver()
        titles = self._run_with_fixture(driver)
        assert titles[0].title_type == "main"

    def test_name_falls_back_when_attr_2_absent(self) -> None:
        # scan_titles.txt has no TINFO:0,2 line → name defaults to "Title_00".
        driver = MakeMKVDriver()
        titles = self._run_with_fixture(driver)
        assert titles[0].name == "Title_00"

    def test_empty_output_returns_empty_list(self) -> None:
        driver = MakeMKVDriver()
        completed = subprocess.CompletedProcess(["makemkvcon"], 0, "", "")
        with patch.object(driver, "require_available"):
            with patch.object(driver, "run", return_value=completed):
                titles = driver.scan_titles(0)
        assert titles == []

    def test_multi_title_longest_is_main(self) -> None:
        # Build inline TINFO with two titles to exercise classification.
        inline = (
            "TINFO:0,9,0,\"02:00:00\"\n"
            "TINFO:1,9,0,\"00:15:00\"\n"
        )
        driver = MakeMKVDriver()
        completed = subprocess.CompletedProcess(["makemkvcon"], 0, inline, "")
        with patch.object(driver, "require_available"):
            with patch.object(driver, "run", return_value=completed):
                titles = driver.scan_titles(0)
        main_titles = [title for title in titles if title.title_type == "main"]
        assert len(main_titles) == 1
        assert main_titles[0].duration == "02:00:00"


# ---------------------------------------------------------------------------
# rip_title() — mocked stream
# ---------------------------------------------------------------------------

class TestRipTitle:
    def test_success_with_tsav_line(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # Pre-create the file named in the TSAV line so _resolve_output_path finds it.
        (tmp_path / "A1_t01.mkv").touch()

        driver = MakeMKVDriver()
        lines = _CLEAN_RIP_OUTPUT.splitlines()
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter(lines)):
                result = driver.rip_title(0, 1, tmp_path)

        assert result.success is True
        assert result.output_path == tmp_path / "A1_t01.mkv"
        assert result.title_index == 1
        assert result.error_message is None

    def test_error_msg_code_causes_failure(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        driver = MakeMKVDriver()
        lines = _ERROR_RIP_OUTPUT.splitlines()
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter(lines)):
                result = driver.rip_title(0, 1, tmp_path)

        assert result.success is False
        assert result.error_message == "Cannot decrypt title"

    def test_progress_callbacks_fired(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        (tmp_path / "A1_t01.mkv").touch()
        events: list[ProgressEvent] = []

        driver = MakeMKVDriver()
        lines = _CLEAN_RIP_OUTPUT.splitlines()
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter(lines)):
                driver.rip_title(0, 1, tmp_path, on_progress=events.append)

        # _CLEAN_RIP_OUTPUT has 3 PRGV lines.
        assert len(events) == 3
        assert all(evt.stage == "rip" for evt in events)
        assert events[-1].current == 65536

    def test_fallback_to_glob_when_no_tsav(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # No TSAV in output but an MKV exists in the output dir.
        (tmp_path / "title_t00.mkv").touch()

        driver = MakeMKVDriver()
        # Use an output that has no TSAV line.
        lines = [
            'MSG:1005,0,1,"MakeMKV started","MakeMKV started"',
            "PRGV:65536,65536,65536",
        ]
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter(lines)):
                result = driver.rip_title(0, 0, tmp_path)

        assert result.success is True
        assert result.output_path is not None
        assert result.output_path.suffix == ".mkv"

    def test_no_output_file_returns_failure(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        # No TSAV and no MKV in the output dir.
        driver = MakeMKVDriver()
        lines = ['MSG:3007,0,0,"Disc access mode","Disc access mode"']
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter(lines)):
                result = driver.rip_title(0, 0, tmp_path)

        assert result.success is False
        assert result.output_path is None
