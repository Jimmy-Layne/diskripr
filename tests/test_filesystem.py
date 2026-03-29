"""Tests for ``diskripr.util.filesystem``.

Covers every exported function:
- ``sanitize_filename``
- ``build_jellyfin_tree``
- ``build_main_feature_filename``
- ``build_extra_filename``
- ``scan_existing_extras``
- ``safe_move``
- ``make_temp_dir``
- ``cleanup``
- ``format_size``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from diskripr.util.filesystem import (
    build_extra_filename,
    build_jellyfin_tree,
    build_main_feature_filename,
    cleanup,
    format_size,
    make_temp_dir,
    safe_move,
    sanitize_filename,
    scan_existing_extras,
)


# ---------------------------------------------------------------------------
# sanitize_filename
# ---------------------------------------------------------------------------

class TestSanitizeFilename:
    def test_strips_colon(self) -> None:
        assert sanitize_filename("Alien: Covenant") == "Alien Covenant"

    def test_strips_forward_slash(self) -> None:
        assert sanitize_filename("AC/DC") == "ACDC"

    def test_strips_backslash(self) -> None:
        assert sanitize_filename("foo\\bar") == "foobar"

    def test_strips_question_mark(self) -> None:
        assert sanitize_filename("Who Am I?") == "Who Am I"

    def test_strips_asterisk(self) -> None:
        assert sanitize_filename("Star*Wars") == "StarWars"

    def test_strips_double_quote(self) -> None:
        assert sanitize_filename('Say "hello"') == "Say hello"

    def test_strips_angle_brackets(self) -> None:
        assert sanitize_filename("<Movie>") == "Movie"

    def test_strips_pipe(self) -> None:
        assert sanitize_filename("left|right") == "leftright"

    def test_collapses_multiple_spaces(self) -> None:
        assert sanitize_filename("too   many   spaces") == "too many spaces"

    def test_plain_name_unchanged(self) -> None:
        assert sanitize_filename("Lawrence of Arabia") == "Lawrence of Arabia"

    def test_empty_string(self) -> None:
        assert sanitize_filename("") == ""


# ---------------------------------------------------------------------------
# build_jellyfin_tree
# ---------------------------------------------------------------------------

class TestBuildJellyfinTree:
    def test_creates_movie_and_extras_dirs(self, tmp_path: Path) -> None:
        movie_dir, extras_dir = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert movie_dir.is_dir()
        assert extras_dir.is_dir()

    def test_movie_dir_path_structure(self, tmp_path: Path) -> None:
        movie_dir, _ = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert movie_dir == tmp_path / "Movies" / "Test Movie (2000)"

    def test_extras_dir_is_inside_movie_dir(self, tmp_path: Path) -> None:
        movie_dir, extras_dir = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert extras_dir == movie_dir / "extras"

    def test_sanitizes_movie_name(self, tmp_path: Path) -> None:
        movie_dir, _ = build_jellyfin_tree(tmp_path, "Alien: Covenant", 2017)
        assert movie_dir.name == "Alien Covenant (2017)"

    def test_idempotent_when_dirs_exist(self, tmp_path: Path) -> None:
        build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        movie_dir, extras_dir = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert movie_dir.is_dir()
        assert extras_dir.is_dir()


# ---------------------------------------------------------------------------
# build_main_feature_filename
# ---------------------------------------------------------------------------

class TestBuildMainFeatureFilename:
    def test_single_disc(self) -> None:
        name = build_main_feature_filename("Test Movie", 2000)
        assert name == "Test Movie (2000).mkv"

    def test_multi_disc_part_one(self) -> None:
        name = build_main_feature_filename("Test Movie", 2000, disc_number=1)
        assert name == "Test Movie (2000) - Part1.mkv"

    def test_multi_disc_part_two(self) -> None:
        name = build_main_feature_filename("Test Movie", 2000, disc_number=2)
        assert name == "Test Movie (2000) - Part2.mkv"

    def test_sanitizes_illegal_chars(self) -> None:
        name = build_main_feature_filename("Alien: Covenant", 2017)
        assert name == "Alien Covenant (2017).mkv"

    def test_none_disc_number_produces_single_disc_name(self) -> None:
        name = build_main_feature_filename("Test Movie", 2000, disc_number=None)
        assert name == "Test Movie (2000).mkv"


# ---------------------------------------------------------------------------
# build_extra_filename
# ---------------------------------------------------------------------------

class TestBuildExtraFilename:
    def test_behindthescenes(self) -> None:
        assert build_extra_filename("behindthescenes", 1) == \
            "Behind the Scenes 1-behindthescenes.mkv"

    def test_deletedscene(self) -> None:
        assert build_extra_filename("deletedscene", 2) == \
            "Deleted Scene 2-deletedscene.mkv"

    def test_featurette(self) -> None:
        assert build_extra_filename("featurette", 1) == \
            "Featurette 1-featurette.mkv"

    def test_interview(self) -> None:
        assert build_extra_filename("interview", 3) == \
            "Interview 3-interview.mkv"

    def test_trailer(self) -> None:
        assert build_extra_filename("trailer", 1) == \
            "Trailer 1-trailer.mkv"

    def test_extra(self) -> None:
        assert build_extra_filename("extra", 5) == \
            "Extra 5-extra.mkv"

    def test_counter_increments_in_filename(self) -> None:
        first = build_extra_filename("featurette", 1)
        second = build_extra_filename("featurette", 2)
        assert "1-featurette" in first
        assert "2-featurette" in second


# ---------------------------------------------------------------------------
# scan_existing_extras
# ---------------------------------------------------------------------------

class TestScanExistingExtras:
    def test_empty_dir_returns_empty_dict(self, tmp_path: Path) -> None:
        result = scan_existing_extras(tmp_path)
        assert result == {}

    def test_nonexistent_dir_returns_empty_dict(self, tmp_path: Path) -> None:
        result = scan_existing_extras(tmp_path / "does_not_exist")
        assert result == {}

    def test_single_file_returns_its_counter(self, tmp_path: Path) -> None:
        (tmp_path / "Behind the Scenes 1-behindthescenes.mkv").touch()
        result = scan_existing_extras(tmp_path)
        assert result == {"behindthescenes": 1}

    def test_picks_highest_counter_for_type(self, tmp_path: Path) -> None:
        (tmp_path / "Featurette 1-featurette.mkv").touch()
        (tmp_path / "Featurette 2-featurette.mkv").touch()
        (tmp_path / "Featurette 3-featurette.mkv").touch()
        result = scan_existing_extras(tmp_path)
        assert result["featurette"] == 3

    def test_multiple_types_tracked_independently(self, tmp_path: Path) -> None:
        (tmp_path / "Behind the Scenes 2-behindthescenes.mkv").touch()
        (tmp_path / "Featurette 1-featurette.mkv").touch()
        (tmp_path / "Trailer 1-trailer.mkv").touch()
        result = scan_existing_extras(tmp_path)
        assert result["behindthescenes"] == 2
        assert result["featurette"] == 1
        assert result["trailer"] == 1

    def test_non_mkv_files_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "readme.txt").touch()
        (tmp_path / "Featurette 1-featurette.mkv").touch()
        result = scan_existing_extras(tmp_path)
        assert "txt" not in result
        assert result == {"featurette": 1}

    def test_unrecognised_mkv_name_ignored(self, tmp_path: Path) -> None:
        (tmp_path / "random_video.mkv").touch()
        result = scan_existing_extras(tmp_path)
        assert result == {}


# ---------------------------------------------------------------------------
# safe_move
# ---------------------------------------------------------------------------

class TestSafeMove:
    def test_moves_file_to_destination(self, tmp_path: Path) -> None:
        src = tmp_path / "source.mkv"
        src.write_bytes(b"data")
        dest = tmp_path / "subdir" / "dest.mkv"

        safe_move(src, dest)

        assert dest.exists()
        assert not src.exists()
        assert dest.read_bytes() == b"data"

    def test_creates_parent_directories(self, tmp_path: Path) -> None:
        src = tmp_path / "source.mkv"
        src.write_bytes(b"x")
        dest = tmp_path / "a" / "b" / "c" / "dest.mkv"

        safe_move(src, dest)

        assert dest.exists()

    def test_raises_if_destination_exists(self, tmp_path: Path) -> None:
        src = tmp_path / "source.mkv"
        dest = tmp_path / "dest.mkv"
        src.write_bytes(b"src")
        dest.write_bytes(b"existing")

        with pytest.raises(FileExistsError):
            safe_move(src, dest)

        # Original file should still be in place.
        assert dest.read_bytes() == b"existing"


# ---------------------------------------------------------------------------
# make_temp_dir / cleanup
# ---------------------------------------------------------------------------

class TestMakeTempDir:
    def test_creates_tmp_subdir(self, tmp_path: Path) -> None:
        result = make_temp_dir(tmp_path)
        assert result == tmp_path / ".tmp"
        assert result.is_dir()

    def test_idempotent_when_dir_exists(self, tmp_path: Path) -> None:
        make_temp_dir(tmp_path)
        result = make_temp_dir(tmp_path)
        assert result.is_dir()


class TestCleanup:
    def test_removes_dir_and_contents(self, tmp_path: Path) -> None:
        target = tmp_path / "work"
        target.mkdir()
        (target / "file.mkv").write_bytes(b"x")

        cleanup(target)

        assert not target.exists()

    def test_no_op_when_dir_absent(self, tmp_path: Path) -> None:
        absent = tmp_path / "nonexistent"
        cleanup(absent)  # should not raise


# ---------------------------------------------------------------------------
# format_size
# ---------------------------------------------------------------------------

class TestFormatSize:
    def test_bytes_range(self) -> None:
        assert format_size(512) == "512.0 B"

    def test_kilobytes_range(self) -> None:
        assert format_size(1024) == "1.0 KB"

    def test_megabytes_range(self) -> None:
        assert format_size(1024 ** 2) == "1.0 MB"

    def test_gigabytes_range(self) -> None:
        assert format_size(1024 ** 3) == "1.0 GB"

    def test_terabytes_range(self) -> None:
        assert format_size(1024 ** 4) == "1.0 TB"

    def test_realistic_dvd_size(self) -> None:
        # 6.5 GB disc — value from scan fixture.
        result = format_size(6_979_534_848)
        assert result.endswith("GB")
