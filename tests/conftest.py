"""Shared pytest fixtures and helpers for the diskripr test suite.

Fixture hierarchy:
- Session-scoped: ``data_dir`` — path to ``tests/data/``
- Function-scoped: ``make_title``, ``sample_title``, ``sample_config``

Module-level helper ``load_fixture(subdir, filename)`` is kept as a plain
function so it can be called from within fixture definitions or directly
inside test functions that do not need the full fixture injection machinery.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from diskripr.config import MovieConfig
from diskripr.models import Title

# Absolute path to the tests/data directory, computed once at import time.
DATA_DIR = Path(__file__).parent / "data"


# ---------------------------------------------------------------------------
# Module-level helper (usable without fixture injection)
# ---------------------------------------------------------------------------

def load_fixture(subdir: str, filename: str) -> str:
    """Return the text content of ``tests/data/<subdir>/<filename>``."""
    return (DATA_DIR / subdir / filename).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Path fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def data_dir() -> Path:
    """Root of the test fixture data tree (``tests/data/``)."""
    return DATA_DIR


# ---------------------------------------------------------------------------
# Model factories
# ---------------------------------------------------------------------------

@pytest.fixture
def make_title() -> Callable[..., Title]:
    """Return a factory that builds valid ``Title`` objects.

    Callers override only the fields they care about; all others default to
    realistic values from the ROSENCRANTZ_AND_GUILDENSTERN disc scan.

    Example::

        def test_something(make_title):
            short = make_title(duration="00:05:00", title_type="extra")
    """
    def _factory(**overrides: object) -> Title:
        kwargs: dict[str, object] = dict(
            index=0,
            name="Test Title",
            duration="01:57:25",
            size_bytes=6_979_534_848,
            chapter_count=13,
            stream_summary="",
            title_type="main",
        )
        kwargs.update(overrides)
        return Title(**kwargs)  # type: ignore[arg-type]

    return _factory


@pytest.fixture
def sample_title(make_title: Callable[..., Title]) -> Title:
    """A single ready-to-use main-feature ``Title``."""
    return make_title()


# ---------------------------------------------------------------------------
# Config fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_config(tmp_path: Path) -> MovieConfig:
    """A minimal valid ``MovieConfig`` using a temporary output directory."""
    return MovieConfig(
        movie_name="Rosencrantz And Guildenstern Are Dead",
        movie_year=1990,
        output_dir=tmp_path / "output",
    )
