"""Tests for ``diskripr.schema``.

Covers:
- ``MovieJob`` round-trip construction and validation.
- ``ShowJob`` round-trip construction and validation.
- Missing required fields produce structured ``ValidationError``.
- ``id`` field is optional and stored when present.
- ``version`` field must be ``"1.0"``; other values are rejected.
- ``export_json_schema()`` writes a valid JSON file with the ``$schema`` key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from diskripr.schema import (
    JobFile,
    JobOptions,
    MovieJob,
    MovieMeta,
    ShowJob,
    ShowMeta,
    export_json_schema,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_movie_job(**overrides: object) -> dict:
    base = {
        "type": "movie",
        "movie": {"name": "The Matrix", "year": 1999},
    }
    base.update(overrides)
    return base


def _make_show_job(**overrides: object) -> dict:
    base = {
        "type": "show",
        "show": {"name": "Breaking Bad", "season": 1, "start_episode": 1},
    }
    base.update(overrides)
    return base


def _make_job_file(*jobs: dict) -> dict:
    return {"version": "1.0", "jobs": list(jobs)}


# ---------------------------------------------------------------------------
# MovieJob round-trip
# ---------------------------------------------------------------------------

class TestMovieJobRoundTrip:
    def test_minimal_movie_job(self) -> None:
        data = _make_movie_job()
        job = MovieJob.model_validate(data)
        assert job.type == "movie"
        assert job.movie.name == "The Matrix"
        assert job.movie.year == 1999
        assert job.id is None
        assert job.options is None

    def test_movie_job_with_id(self) -> None:
        uid = "550e8400-e29b-41d4-a716-446655440000"
        job = MovieJob.model_validate(_make_movie_job(id=uid))
        assert job.id == uid

    def test_movie_job_with_options(self) -> None:
        data = _make_movie_job(options={"device": "/dev/sr1", "min_length": 30})
        job = MovieJob.model_validate(data)
        assert job.options is not None
        assert job.options.device == "/dev/sr1"
        assert job.options.min_length == 30

    def test_movie_job_options_all_optional(self) -> None:
        opts = JobOptions.model_validate({})
        assert opts.device is None
        assert opts.output_dir is None
        assert opts.rip_mode is None
        assert opts.encode_format is None
        assert opts.quality is None

    def test_movie_meta_year_boundary_low(self) -> None:
        meta = MovieMeta.model_validate({"name": "Arrival", "year": 1888})
        assert meta.year == 1888

    def test_movie_meta_year_boundary_high(self) -> None:
        meta = MovieMeta.model_validate({"name": "Future Film", "year": 2100})
        assert meta.year == 2100

    def test_movie_job_serialises_back(self) -> None:
        data = _make_movie_job(id="abc-123")
        job = MovieJob.model_validate(data)
        dumped = job.model_dump(exclude_none=True)
        assert dumped["type"] == "movie"
        assert dumped["movie"]["name"] == "The Matrix"
        assert dumped["id"] == "abc-123"


# ---------------------------------------------------------------------------
# ShowJob round-trip
# ---------------------------------------------------------------------------

class TestShowJobRoundTrip:
    def test_minimal_show_job(self) -> None:
        job = ShowJob.model_validate(_make_show_job())
        assert job.type == "show"
        assert job.show.name == "Breaking Bad"
        assert job.show.season == 1
        assert job.show.start_episode == 1
        assert job.id is None
        assert job.options is None

    def test_show_job_season_zero(self) -> None:
        data = _make_show_job(show={"name": "Extras", "season": 0, "start_episode": 1})
        job = ShowJob.model_validate(data)
        assert job.show.season == 0

    def test_show_job_with_id(self) -> None:
        uid = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        job = ShowJob.model_validate(_make_show_job(id=uid))
        assert job.id == uid

    def test_show_job_with_options(self) -> None:
        data = _make_show_job(options={"eject_on_complete": False})
        job = ShowJob.model_validate(data)
        assert job.options is not None
        assert job.options.eject_on_complete is False

    def test_show_job_serialises_back(self) -> None:
        job = ShowJob.model_validate(_make_show_job())
        dumped = job.model_dump(exclude_none=True)
        assert dumped["type"] == "show"
        assert dumped["show"]["season"] == 1


# ---------------------------------------------------------------------------
# JobFile round-trip
# ---------------------------------------------------------------------------

class TestJobFileRoundTrip:
    def test_single_movie_job(self) -> None:
        data = _make_job_file(_make_movie_job())
        jf = JobFile.model_validate(data)
        assert jf.version == "1.0"
        assert len(jf.jobs) == 1
        assert jf.jobs[0].type == "movie"  # type: ignore[union-attr]

    def test_single_show_job(self) -> None:
        data = _make_job_file(_make_show_job())
        jf = JobFile.model_validate(data)
        assert jf.jobs[0].type == "show"  # type: ignore[union-attr]

    def test_mixed_job_types(self) -> None:
        data = _make_job_file(_make_movie_job(), _make_show_job())
        jf = JobFile.model_validate(data)
        assert len(jf.jobs) == 2
        assert jf.jobs[0].type == "movie"  # type: ignore[union-attr]
        assert jf.jobs[1].type == "show"  # type: ignore[union-attr]

    def test_empty_jobs_list(self) -> None:
        jf = JobFile.model_validate({"version": "1.0", "jobs": []})
        assert jf.jobs == []


# ---------------------------------------------------------------------------
# Validation errors — missing required fields
# ---------------------------------------------------------------------------

class TestValidationErrors:
    def test_missing_movie_name_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MovieMeta.model_validate({"year": 1999})
        errors = exc_info.value.errors()
        assert any(err["loc"] == ("name",) for err in errors)

    def test_missing_movie_year_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            MovieMeta.model_validate({"name": "The Matrix"})
        errors = exc_info.value.errors()
        assert any(err["loc"] == ("year",) for err in errors)

    def test_movie_year_below_minimum_raises(self) -> None:
        with pytest.raises(ValidationError):
            MovieMeta.model_validate({"name": "Old Film", "year": 1887})

    def test_movie_year_above_maximum_raises(self) -> None:
        with pytest.raises(ValidationError):
            MovieMeta.model_validate({"name": "Future Film", "year": 2101})

    def test_missing_show_name_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ShowMeta.model_validate({"season": 1, "start_episode": 1})
        errors = exc_info.value.errors()
        assert any(err["loc"] == ("name",) for err in errors)

    def test_missing_show_season_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ShowMeta.model_validate({"name": "Breaking Bad", "start_episode": 1})
        errors = exc_info.value.errors()
        assert any(err["loc"] == ("season",) for err in errors)

    def test_missing_show_start_episode_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ShowMeta.model_validate({"name": "Breaking Bad", "season": 1})
        errors = exc_info.value.errors()
        assert any(err["loc"] == ("start_episode",) for err in errors)

    def test_show_negative_season_raises(self) -> None:
        with pytest.raises(ValidationError):
            ShowMeta.model_validate({"name": "Bad", "season": -1, "start_episode": 1})

    def test_show_start_episode_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            ShowMeta.model_validate({"name": "Bad", "season": 1, "start_episode": 0})

    def test_version_wrong_raises(self) -> None:
        with pytest.raises(ValidationError):
            JobFile.model_validate({"version": "2.0", "jobs": []})

    def test_version_missing_raises(self) -> None:
        with pytest.raises(ValidationError):
            JobFile.model_validate({"jobs": []})

    def test_unknown_type_raises(self) -> None:
        data = _make_job_file({"type": "podcast", "podcast": {}})
        with pytest.raises(ValidationError):
            JobFile.model_validate(data)

    def test_missing_type_discriminator_raises(self) -> None:
        with pytest.raises(ValidationError):
            JobFile.model_validate({"version": "1.0", "jobs": [{"movie": {"name": "X", "year": 2000}}]})


# ---------------------------------------------------------------------------
# id field
# ---------------------------------------------------------------------------

class TestIdField:
    def test_id_absent_is_none(self) -> None:
        job = MovieJob.model_validate(_make_movie_job())
        assert job.id is None

    def test_id_present_is_stored(self) -> None:
        uid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        job = MovieJob.model_validate(_make_movie_job(id=uid))
        assert job.id == uid

    def test_id_in_job_file(self) -> None:
        uid = "00000000-0000-0000-0000-000000000001"
        data = _make_job_file(_make_movie_job(id=uid))
        jf = JobFile.model_validate(data)
        assert jf.jobs[0].id == uid  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# JSON Schema export
# ---------------------------------------------------------------------------

class TestExportJsonSchema:
    def test_creates_file(self, tmp_path: Path) -> None:
        dest = tmp_path / "schema.json"
        export_json_schema(dest)
        assert dest.exists()

    def test_output_is_valid_json(self, tmp_path: Path) -> None:
        dest = tmp_path / "schema.json"
        export_json_schema(dest)
        data = json.loads(dest.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_schema_header_present(self, tmp_path: Path) -> None:
        dest = tmp_path / "schema.json"
        export_json_schema(dest)
        data = json.loads(dest.read_text(encoding="utf-8"))
        assert data.get("$schema") == "https://json-schema.org/draft/2020-12/schema"

    def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        dest = tmp_path / "nested" / "dir" / "schema.json"
        export_json_schema(dest)
        assert dest.exists()

    def test_schema_contains_jobs_field(self, tmp_path: Path) -> None:
        dest = tmp_path / "schema.json"
        export_json_schema(dest)
        raw = dest.read_text(encoding="utf-8")
        assert "jobs" in raw

    def test_schema_contains_version_field(self, tmp_path: Path) -> None:
        dest = tmp_path / "schema.json"
        export_json_schema(dest)
        raw = dest.read_text(encoding="utf-8")
        assert "version" in raw
