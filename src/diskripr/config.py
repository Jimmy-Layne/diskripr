# pylint: disable=duplicate-code
"""Runtime configuration for the diskripr pipeline.

Provides three configuration dataclasses:

- ``BaseConfig``  — shared fields for all pipeline types (device, encoding,
                    output paths, etc.).
- ``MovieConfig`` — extends ``BaseConfig`` with ``movie_name`` and
                    ``movie_year`` for single-movie rips.
- ``ShowConfig``  — extends ``BaseConfig`` with ``show_name``,
                    ``season_number``, and ``start_episode`` for TV season
                    rips.

Each subclass exposes a ``validate()`` method that collects all invalid-field
errors and raises ``ConfigError`` with a single semicolon-separated message.
``MovieConfig`` and ``ShowConfig`` both check the shared encoding parameters
(via ``BaseConfig.validate()``) as well as their own type-specific fields.

Shared ``BaseConfig`` fields:

- ``output_dir``        — Base output directory (default: ``./dvd_output``).
- ``temp_dir``          — Temporary working directory; defaults to
                          ``<output_dir>/.tmp``. Overridable via
                          ``DISKRIPR_TEMP_DIR`` environment variable.
- ``device``            — Optical drive block device path (default: ``/dev/sr0``).
- ``disc_number``       — Disc number for multi-disc titles; ``None`` for
                          single-disc behaviour.
- ``rip_mode``          — Title selection mode: ``"main"``, ``"all"``, or
                          ``"ask"``.
- ``encode_format``     — Encoding format: ``"h264"``, ``"h265"``, ``"none"``,
                          or ``"ask"``.
- ``quality``           — HandBrake RF quality value (default: 20 for h264,
                          22 for h265).
- ``min_length``        — Minimum title duration in seconds (default: 10).
- ``keep_original``     — Retain pre-encode MKVs in an ``originals/``
                          subdirectory (default: ``False``).
- ``eject_on_complete`` — Eject disc when the pipeline finishes
                          (default: ``True``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

RipMode = Literal["main", "all", "ask"]
EncodeFormat = Literal["h264", "h265", "none", "ask"]

_DEFAULT_QUALITY: dict[str, int] = {"h264": 20, "h265": 22}
_ENCODING_FORMATS = frozenset({"h264", "h265"})

# First commercially exhibited film (Lumière brothers, 1895) gives a safe lower bound.
_MIN_YEAR = 1888
_MAX_YEAR = 2099
_MAX_RF_QUALITY = 51


class ConfigError(ValueError):
    """Raised by ``validate()`` for invalid or impossible configurations."""


@dataclass(kw_only=True)
class BaseConfig:  # pylint: disable=too-many-instance-attributes
    """Shared configuration fields common to all pipeline types."""

    output_dir: Path = field(default_factory=lambda: Path("dvd_output"))
    temp_dir: Optional[Path] = None
    device: str = "/dev/sr0"
    disc_number: Optional[int] = None
    rip_mode: RipMode = "main"
    encode_format: EncodeFormat = "none"
    quality: Optional[int] = None
    min_length: int = 10
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
        """Raise ``ConfigError`` for shared invalid configuration.

        Checks performed:

        - ``disc_number`` must be >= 1 when set.
        - ``min_length`` must be > 0.
        - ``quality`` must be in 0–51 when encoding is requested.
        - HandBrakeCLI must be on PATH when ``encode_format`` is ``"h264"`` or
          ``"h265"``.
        """
        # Import here to avoid circular import; driver modules do not import config.
        from diskripr.drivers.handbrake import HandBrakeDriver  # pylint: disable=import-outside-toplevel

        errors: list[str] = []

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


@dataclass(kw_only=True)
class MovieConfig(BaseConfig):
    """Configuration for a single movie pipeline run.

    Extends ``BaseConfig`` with:

    - ``movie_name`` — Movie title for Jellyfin naming (required).
    - ``movie_year`` — Release year for Jellyfin naming (required).
    """

    movie_name: str
    movie_year: int

    def validate(self) -> None:
        """Raise ``ConfigError`` if the movie configuration is invalid.

        Runs all shared ``BaseConfig`` checks first, then checks:

        - ``movie_name`` must not be blank.
        - ``movie_year`` must be in the range 1888–2099.
        """
        super().validate()

        errors: list[str] = []

        if not self.movie_name.strip():
            errors.append("movie_name must not be blank")

        if not _MIN_YEAR <= self.movie_year <= _MAX_YEAR:
            errors.append(
                f"movie_year must be between {_MIN_YEAR} and {_MAX_YEAR}, "
                f"got {self.movie_year}"
            )

        if errors:
            raise ConfigError("; ".join(errors))

    @classmethod
    def from_click_params(  # pylint: disable=too-many-arguments,too-many-positional-arguments,duplicate-code
        cls,
        movie_name: str,
        movie_year: int,
        output_dir: Path,
        device: str = "/dev/sr0",
        disc_number: Optional[int] = None,
        rip_mode: RipMode = "main",
        encode_format: EncodeFormat = "none",
        quality: Optional[int] = None,
        min_length: int = 10,
        keep_original: bool = False,
        eject_on_complete: bool = True,
    ) -> MovieConfig:
        """Construct a ``MovieConfig`` from Click CLI parameter values.

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


@dataclass(kw_only=True)
class ShowConfig(BaseConfig):
    """Configuration for a TV season pipeline run.

    Extends ``BaseConfig`` with:

    - ``show_name``      — Series title for Jellyfin naming (required).
    - ``season_number``  — Season index (required); 0 maps to ``Season 00``
                           (Jellyfin specials).
    - ``start_episode``  — Episode number assigned to the first title on this
                           disc (required); must be >= 1.
    """

    show_name: str
    season_number: int
    start_episode: int

    def validate(self) -> None:
        """Raise ``ConfigError`` if the show configuration is invalid.

        Runs all shared ``BaseConfig`` checks first, then checks:

        - ``show_name`` must not be blank.
        - ``season_number`` must be >= 0.
        - ``start_episode`` must be >= 1.
        """
        super().validate()

        errors: list[str] = []

        if not self.show_name.strip():
            errors.append("show_name must not be blank")

        if self.season_number < 0:
            errors.append(
                f"season_number must be >= 0, got {self.season_number}"
            )

        if self.start_episode < 1:
            errors.append(
                f"start_episode must be >= 1, got {self.start_episode}"
            )

        if errors:
            raise ConfigError("; ".join(errors))
