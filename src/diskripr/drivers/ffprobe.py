"""Driver for ``ffprobe`` — stream inspection of output MKV files.

Exposes one method on :class:`FfprobeDriver`:

- ``inspect(mkv_path)``
    Run ``ffprobe -v quiet -print_format json -show_streams <path>`` and parse the JSON
    response into a ``StreamReport`` containing:
    - Video tracks: codec name and resolution.
    - Audio tracks: codec name, language code, channel count.
    - Subtitle tracks: codec name, language code, track title, forced flag.

    Returns ``None`` if ``ffprobe`` is not available on PATH. Stream inspection
    is informational — its absence never gates any pipeline step.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from diskripr.drivers.base import BaseDriver, ToolError
from diskripr.models import AudioTrack, StreamReport, SubtitleTrack, VideoTrack

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Stream-parsing helpers (module-level — pure data transformation)
# ---------------------------------------------------------------------------

def _parse_video_stream(stream: dict[str, Any]) -> VideoTrack:
    """Build a :class:`~diskripr.models.VideoTrack` from one ffprobe stream dict."""
    codec = stream.get("codec_name", "unknown")
    width = stream.get("width", 0)
    height = stream.get("height", 0)
    resolution = f"{width}x{height}" if width and height else "unknown"
    return VideoTrack(codec=codec, resolution=resolution)


def _parse_audio_stream(stream: dict[str, Any]) -> AudioTrack:
    """Build an :class:`~diskripr.models.AudioTrack` from one ffprobe stream dict."""
    codec = stream.get("codec_name", "unknown")
    tags = stream.get("tags", {})
    language = tags.get("language", "und")
    channels = stream.get("channels", 0)
    return AudioTrack(codec=codec, language=language, channels=channels)


def _parse_subtitle_stream(stream: dict[str, Any]) -> SubtitleTrack:
    """Build a :class:`~diskripr.models.SubtitleTrack` from one ffprobe stream dict."""
    codec = stream.get("codec_name", "unknown")
    tags = stream.get("tags", {})
    language = tags.get("language", "und")
    track_title = tags.get("title", "")
    forced = bool(stream.get("disposition", {}).get("forced", 0))
    return SubtitleTrack(
        codec=codec,
        language=language,
        track_title=track_title,
        forced=forced,
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

class FfprobeDriver(BaseDriver):
    """Driver for ``ffprobe`` stream inspection.

    Inherits subprocess management from
    :class:`~diskripr.drivers.base.BaseDriver`.

    Unlike the rip and encode drivers, ``inspect`` is designed to be
    non-fatal: it returns ``None`` rather than raising on any failure
    (binary absent, non-zero exit, or malformed JSON) because stream
    inspection is informational and must never gate a pipeline step.

    Usage::

        driver = FfprobeDriver()
        report = driver.inspect(Path("movie.mkv"))
        if report is not None:
            for track in report.audio_tracks:
                print(track.language, track.codec, track.channels)
    """

    binary = "ffprobe"

    def inspect(self, mkv_path: Path) -> Optional[StreamReport]:
        """Inspect *mkv_path* and return a :class:`~diskripr.models.StreamReport`.

        Runs::

            ffprobe -v quiet -print_format json -show_streams <mkv_path>

        and parses the JSON output into typed track objects.

        Args:
            mkv_path: Path to the MKV file to inspect.

        Returns:
            A :class:`~diskripr.models.StreamReport` on success, or ``None``
            if ffprobe is unavailable, exits with an error, or returns
            malformed JSON.
        """
        if not self.is_available():
            log.debug("ffprobe not on PATH — skipping stream inspection")
            return None

        try:
            result = self.run([
                self.binary,
                "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                str(mkv_path),
            ])
        except ToolError as exc:
            log.warning("ffprobe failed on %s: %s", mkv_path, exc)
            return None

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            log.warning("ffprobe returned invalid JSON for %s: %s", mkv_path, exc)
            return None

        return self._parse_streams(data.get("streams", []))

    @staticmethod
    def _parse_streams(streams: list[dict[str, Any]]) -> StreamReport:
        """Build a :class:`~diskripr.models.StreamReport` from ffprobe's stream list."""
        report = StreamReport()
        for stream in streams:
            codec_type = stream.get("codec_type", "")
            if codec_type == "video":
                report.video_tracks.append(_parse_video_stream(stream))
            elif codec_type == "audio":
                report.audio_tracks.append(_parse_audio_stream(stream))
            elif codec_type == "subtitle":
                report.subtitle_tracks.append(_parse_subtitle_stream(stream))
        return report
