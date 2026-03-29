"""Tests for ``diskripr.drivers.handbrake``.

Strategy:
- Static helpers (``_parse_progress``, ``_handle_progress_line``,
  ``_build_args``) are tested directly.
- ``encode`` is tested by mocking ``require_available`` and ``stream``; real
  filesystem paths are created in ``tmp_path`` so the size/existence checks
  in the driver work correctly.

Fixture files used:
- ``tests/data/handbrake/encode_progress.txt``

Note on the fixture format:
  HandBrake writes progress lines using ``\\r`` (carriage return), so when the
  output is captured to a file the ``Encoding:`` lines appear concatenated on
  a single ``\\n``-terminated line.  This is exactly what the driver's
  ``stream()`` method sees from a live process, so the fixture is realistic.
  ``_parse_progress`` uses ``re.match`` and will correctly extract only the
  first percentage value from such a concatenated line.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from conftest import load_fixture
from diskripr.drivers.handbrake import HandBrakeDriver
from diskripr.util.progress import ProgressEvent


# ---------------------------------------------------------------------------
# _parse_progress — static helper
# ---------------------------------------------------------------------------

class TestParseProgress:
    def test_valid_progress_line(self) -> None:
        line = "Encoding: task 1 of 1, 45.67 % (150.23 fps, avg 148.90 fps, ETA 00h01m23s)"
        result = HandBrakeDriver._parse_progress(line)
        assert result == pytest.approx(45.67)

    def test_zero_percent(self) -> None:
        assert HandBrakeDriver._parse_progress("Encoding: task 1 of 1, 0.00 %") == pytest.approx(0.0)

    def test_hundred_percent(self) -> None:
        assert HandBrakeDriver._parse_progress("Encoding: task 1 of 1, 100.00 %") == pytest.approx(100.0)

    def test_non_encoding_line_returns_none(self) -> None:
        assert HandBrakeDriver._parse_progress("[21:22:03] libhb: scan thread found 1 valid title(s)") is None

    def test_empty_line_returns_none(self) -> None:
        assert HandBrakeDriver._parse_progress("") is None

    def test_concatenated_progress_lines_match_first(self) -> None:
        # Real captured output: multiple \r-separated Encoding: lines in one string.
        line = "Encoding: task 1 of 1, 20.00 %Encoding: task 1 of 1, 43.15 %"
        result = HandBrakeDriver._parse_progress(line)
        assert result == pytest.approx(20.00)


# ---------------------------------------------------------------------------
# _handle_progress_line — static helper
# ---------------------------------------------------------------------------

class TestHandleProgressLine:
    def test_fires_callback_on_encoding_line(self) -> None:
        events: list[ProgressEvent] = []
        HandBrakeDriver._handle_progress_line(
            "Encoding: task 1 of 1, 50.00 %", events.append
        )
        assert len(events) == 1
        evt = events[0]
        assert evt.stage == "encode"
        assert evt.current == 50
        assert evt.total == 100

    def test_no_callback_does_not_raise(self) -> None:
        HandBrakeDriver._handle_progress_line(
            "Encoding: task 1 of 1, 50.00 %", None
        )

    def test_non_encoding_line_does_not_fire_callback(self) -> None:
        events: list[ProgressEvent] = []
        HandBrakeDriver._handle_progress_line("[21:22:03] some log line", events.append)
        assert events == []


# ---------------------------------------------------------------------------
# _build_args — static helper
# ---------------------------------------------------------------------------

class TestBuildArgs:
    def _get_args(
        self,
        encoder: str = "h264",
        quality: int = 20,
        input_path: Path = Path("input.mkv"),
        output_path: Path = Path("output.mkv"),
    ) -> list[str]:
        return HandBrakeDriver._build_args(input_path, output_path, encoder, quality)

    def test_h264_encoder_name(self) -> None:
        args = self._get_args(encoder="h264")
        assert "x264" in args

    def test_h265_encoder_name(self) -> None:
        args = self._get_args(encoder="h265")
        assert "x265" in args

    def test_quality_value_present(self) -> None:
        args = self._get_args(quality=22)
        quality_idx = args.index("-q")
        assert args[quality_idx + 1] == "22"

    def test_copy_audio_flags(self) -> None:
        args = self._get_args()
        assert "--all-audio" in args
        assert "--aencoder" in args
        idx = args.index("--aencoder")
        assert args[idx + 1] == "copy"

    def test_subtitle_flags(self) -> None:
        args = self._get_args()
        assert "--all-subtitles" in args
        assert "--subtitle-burned=none" in args

    def test_chapter_markers_flag(self) -> None:
        args = self._get_args()
        assert "--markers" in args

    def test_optimize_flag(self) -> None:
        args = self._get_args()
        assert "--optimize" in args

    def test_input_and_output_paths(self, tmp_path: Path) -> None:
        inp = tmp_path / "in.mkv"
        out = tmp_path / "out.mkv"
        args = self._get_args(input_path=inp, output_path=out)
        assert str(inp) in args
        assert str(out) in args


# ---------------------------------------------------------------------------
# encode() — mocked stream with fixture
# ---------------------------------------------------------------------------

class TestEncode:
    def test_progress_events_from_fixture(self, tmp_path: Path) -> None:
        input_path = tmp_path / "A1_t01.mkv"
        output_path = tmp_path / "encoded.mkv"
        input_path.write_bytes(b"x" * 1024)  # fake input for stat()
        output_path.write_bytes(b"x" * 512)  # fake output to simulate success

        fixture_text = load_fixture("handbrake", "encode_progress.txt")
        lines = fixture_text.splitlines()

        events: list[ProgressEvent] = []
        driver = HandBrakeDriver()
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter(lines)):
                result = driver.encode(
                    title_index=1,
                    input_path=input_path,
                    output_path=output_path,
                    encoder="h264",
                    quality=20,
                    on_progress=events.append,
                )

        assert result.success is True
        assert result.title_index == 1
        # The fixture has at least 2 lines with "Encoding:" prefixes.
        assert len(events) >= 2
        assert all(evt.stage == "encode" for evt in events)
        assert all(evt.total == 100 for evt in events)

    def test_encode_returns_file_sizes(self, tmp_path: Path) -> None:
        input_path = tmp_path / "input.mkv"
        output_path = tmp_path / "output.mkv"
        input_path.write_bytes(b"x" * 2048)
        output_path.write_bytes(b"x" * 1024)

        driver = HandBrakeDriver()
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter([])):
                result = driver.encode(
                    title_index=0,
                    input_path=input_path,
                    output_path=output_path,
                    encoder="h264",
                    quality=20,
                )

        assert result.original_size_bytes == 2048
        assert result.encoded_size_bytes == 1024

    def test_encode_returns_failure_when_no_output_file(self, tmp_path: Path) -> None:
        input_path = tmp_path / "input.mkv"
        output_path = tmp_path / "output.mkv"
        input_path.write_bytes(b"data")
        # output_path deliberately not created

        driver = HandBrakeDriver()
        with patch.object(driver, "require_available"):
            with patch.object(driver, "stream", return_value=iter([])):
                result = driver.encode(
                    title_index=0,
                    input_path=input_path,
                    output_path=output_path,
                    encoder="h264",
                    quality=20,
                )

        assert result.success is False
        assert result.output_path is None
        assert result.error_message is not None
