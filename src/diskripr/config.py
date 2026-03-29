"""Runtime configuration for the diskripr pipeline.

Provides the ``Config`` dataclass, which holds all parameters that control
pipeline behaviour:

- ``output_dir``        — Base output directory (default: ``./dvd_output``).
- ``temp_dir``          — Temporary working directory for ripped files;
                          defaults to ``<output_dir>/.tmp``. Can be overridden
                          via the ``DISKRIPR_TEMP_DIR`` environment variable.
- ``device``            — Optical drive block device path (default: ``/dev/sr0``).
- ``movie_name``        — Movie title for Jellyfin naming (required).
- ``movie_year``        — Release year for Jellyfin naming (required).
- ``disc_number``       — Disc number for multi-disc movies; ``None`` for
                          single-disc behaviour.
- ``media_type``        — Media type; only ``"movie"`` is supported in v1.
- ``rip_mode``          — Title selection mode: ``"main"``, ``"all"``, or
                          ``"ask"``.
- ``encode_format``     — Encoding format: ``"h264"``, ``"h265"``, ``"none"``,
                          or ``"ask"``.
- ``quality``           — HandBrake RF quality value (default: 20 for h264,
                          22 for h265).
- ``min_length``        — Minimum title duration in seconds (default: 30).
- ``keep_original``     — Retain pre-encode MKVs in an ``originals/``
                          subdirectory (default: ``False``).
- ``eject_on_complete`` — Eject disc when the pipeline finishes
                          (default: ``True``).

Also exposes a ``validate()`` method that checks for impossible combinations
(e.g. encoding requested but HandBrakeCLI absent) and a factory classmethod
for constructing a ``Config`` from Click parameter values.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

RipMode = Literal["main", "all", "ask"]
EncodeFormat = Literal["h264", "h265", "none", "ask"]
MediaType = Literal["movie"]

_DEFAULT_QUALITY: dict[str, int] = {"h264": 20, "h265": 22}
_ENCODING_FORMATS = frozenset({"h264", "h265"})

# First commercially exhibited film (Lumière brothers, 1895) gives a safe lower bound.
_MIN_YEAR = 1888
_MAX_YEAR = 2099
_MAX_RF_QUALITY = 51


class ConfigError(ValueError):
    """Raised by ``Config.validate()`` for invalid or impossible configurations."""


@dataclass
class Config:  # pylint: disable=too-many-instance-attributes
    """All parameters that control a single diskripr pipeline run."""

    movie_name: str
    movie_year: int
    output_dir: Path = field(default_factory=lambda: Path("dvd_output"))
    temp_dir: Optional[Path] = None
    device: str = "/dev/sr0"
    disc_number: Optional[int] = None
    media_type: MediaType = "movie"
    rip_mode: RipMode = "main"
    encode_format: EncodeFormat = "none"
    quality: Optional[int] = None
    min_length: int = 30
    keep_original: bool = False
    eject_on_complete: bool = True

    def __post_init__(self) -> None:
        # Coerce plain strings passed programmatically.
        if not isinstance(self.output_dir, Path):
            self.output_dir = Path(self.output_dir)

        # Apply DISKRIPR_TEMP_DIR only when temp_dir was not set explicitly.
        if self.temp_dir is None:
            env_temp = os.environ.get("DISKRIPR_TEMP_DIR")
            self.temp_dir = Path(env_temp) if env_temp else self.output_dir / ".tmp"
        elif not isinstance(self.temp_dir, Path):
            self.temp_dir = Path(self.temp_dir)

        # Fill in the encoder-specific quality default.
        if self.quality is None and self.encode_format in _DEFAULT_QUALITY:
            self.quality = _DEFAULT_QUALITY[self.encode_format]

    def validate(self) -> None:
        """Raise ``ConfigError`` if the configuration is invalid or impossible.

        Checks performed:

        - ``movie_name`` must not be blank.
        - ``movie_year`` must be in the range 1888–2099.
        - ``disc_number`` must be >= 1 when set.
        - ``min_length`` must be > 0.
        - ``quality`` must be in 0–51 when encoding is requested.
        - HandBrakeCLI must be on PATH when ``encode_format`` is ``"h264"`` or
          ``"h265"``.
        """
        # Import here to avoid a circular import; driver modules do not import config.
        from diskripr.drivers.handbrake import HandBrakeDriver  # pylint: disable=import-outside-toplevel

        errors: list[str] = []

        if not self.movie_name.strip():
            errors.append("movie_name must not be blank")

        if not _MIN_YEAR <= self.movie_year <= _MAX_YEAR:
            errors.append(
                f"movie_year must be between {_MIN_YEAR} and {_MAX_YEAR}, "
                f"got {self.movie_year}"
            )

        if self.disc_number is not None and self.disc_number < 1:
            errors.append(
                f"disc_number must be >= 1 when set, got {self.disc_number}"
            )

        if self.min_length <= 0:
            errors.append(
                f"min_length must be > 0, got {self.min_length}"
            )

        if self.encode_format in _ENCODING_FORMATS:
            if self.quality is not None and not 0 <= self.quality <= _MAX_RF_QUALITY:
                errors.append(
                    f"quality must be 0–{_MAX_RF_QUALITY}, got {self.quality}"
                )
            if not HandBrakeDriver().is_available():
                errors.append(
                    f"encode_format is {self.encode_format!r} but HandBrakeCLI "
                    "was not found on PATH"
                )

        if errors:
            raise ConfigError("; ".join(errors))

    @classmethod
    def from_click_params(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        cls,
        movie_name: str,
        movie_year: int,
        output_dir: Path,
        device: str = "/dev/sr0",
        disc_number: Optional[int] = None,
        rip_mode: RipMode = "main",
        encode_format: EncodeFormat = "none",
        quality: Optional[int] = None,
        min_length: int = 30,
        keep_original: bool = False,
        eject_on_complete: bool = True,
    ) -> Config:
        """Construct a ``Config`` from Click CLI parameter values.

        Reads ``DISKRIPR_TEMP_DIR`` from the environment (handled in
        ``__post_init__``). All parameters map 1-to-1 to Click options of the
        same name.
        """
        return cls(
            movie_name=movie_name,
            movie_year=movie_year,
            output_dir=output_dir,
            device=device,
            disc_number=disc_number,
            rip_mode=rip_mode,
            encode_format=encode_format,
            quality=quality,
            min_length=min_length,
            keep_original=keep_original,
            eject_on_complete=eject_on_complete,
        )
