"""Tests for ``diskripr.util.jellyfin_filesystem``.

Covers all exported functions:
- ``sanitize_filename``
- ``build_jellyfin_tree`` — creates all eight extra-type subdirs, returns dict
- ``build_main_feature_filename``
- ``build_extra_filename`` — with/without title_name, generic vs. descriptive
- ``scan_existing_extras`` — scans all eight subdirs, highest counter per type
- ``build_tv_tree`` — season path structure including Season 00
- ``build_episode_filename`` — zero-padding, with/without episode title
- ``safe_move``
- ``make_temp_dir``
- ``cleanup``
- ``format_size``
"""

from __future__ import annotations

from pathlib import Path

import pytest

from diskripr.util.jellyfin_filesystem import (
    build_episode_filename,
    build_extra_filename,
    build_jellyfin_tree,
    build_main_feature_filename,
    build_tv_tree,
    cleanup,
    format_size,
    make_temp_dir,
    safe_move,
    sanitize_filename,
    scan_existing_extras,
)

# All Jellyfin extra-type subdirectory names, in no particular order.
_ALL_SUBDIRS = {
    "behind the scenes",
    "deleted scenes",
    "featurettes",
    "interviews",
    "scenes",
    "shorts",
    "trailers",
    "extras",
}


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
    def test_returns_movie_dir_and_dict(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert movie_dir.is_dir()
        assert isinstance(extra_dirs, dict)

    def test_movie_dir_path_structure(self, tmp_path: Path) -> None:
        movie_dir, _ = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert movie_dir == tmp_path / "movies" / "Test Movie (2000)"

    def test_sanitizes_movie_name(self, tmp_path: Path) -> None:
        movie_dir, _ = build_jellyfin_tree(tmp_path, "Alien: Covenant", 2017)
        assert movie_dir.name == "Alien Covenant (2017)"

    def test_creates_all_eight_extra_type_subdirs(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        created_names = {path.name for path in movie_dir.iterdir() if path.is_dir()}
        assert created_names == _ALL_SUBDIRS

    def test_dict_keys_cover_all_eight_types(self, tmp_path: Path) -> None:
        _, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert set(extra_dirs.keys()) == {
            "behindthescenes", "deletedscene", "featurette", "interview",
            "scene", "short", "trailer", "extra",
        }

    def test_dict_values_are_subdirs_of_movie_dir(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        for path in extra_dirs.values():
            assert path.parent == movie_dir
            assert path.is_dir()

    def test_behind_the_scenes_subdir_correct_name(self, tmp_path: Path) -> None:
        _, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert extra_dirs["behindthescenes"].name == "behind the scenes"

    def test_deletedscene_subdir_correct_name(self, tmp_path: Path) -> None:
        _, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert extra_dirs["deletedscene"].name == "deleted scenes"

    def test_idempotent_when_dirs_exist(self, tmp_path: Path) -> None:
        build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        assert movie_dir.is_dir()
        assert all(path.is_dir() for path in extra_dirs.values())


# ---------------------------------------------------------------------------
# build_main_feature_filename
# ---------------------------------------------------------------------------

class TestBuildMainFeatureFilename:
    def test_single_disc(self) -> None:
        assert build_main_feature_filename("Test Movie", 2000) == "Test Movie (2000).mkv"

    def test_multi_disc_part_one(self) -> None:
        assert build_main_feature_filename("Test Movie", 2000, disc_number=1) == \
            "Test Movie (2000) - Part1.mkv"

    def test_multi_disc_part_two(self) -> None:
        assert build_main_feature_filename("Test Movie", 2000, disc_number=2) == \
            "Test Movie (2000) - Part2.mkv"

    def test_sanitizes_illegal_chars(self) -> None:
        assert build_main_feature_filename("Alien: Covenant", 2017) == \
            "Alien Covenant (2017).mkv"

    def test_none_disc_number_produces_single_disc_name(self) -> None:
        assert build_main_feature_filename("Test Movie", 2000, disc_number=None) == \
            "Test Movie (2000).mkv"


# ---------------------------------------------------------------------------
# build_extra_filename
# ---------------------------------------------------------------------------

class TestBuildExtraFilename:
    def test_without_title_name_uses_counter_format(self) -> None:
        assert build_extra_filename("behindthescenes", 1) == "Behind the Scenes 1.mkv"

    def test_counter_format_all_types(self) -> None:
        assert build_extra_filename("deletedscene", 2) == "Deleted Scene 2.mkv"
        assert build_extra_filename("featurette", 1) == "Featurette 1.mkv"
        assert build_extra_filename("interview", 3) == "Interview 3.mkv"
        assert build_extra_filename("scene", 1) == "Scene 1.mkv"
        assert build_extra_filename("short", 1) == "Short 1.mkv"
        assert build_extra_filename("trailer", 1) == "Trailer 1.mkv"
        assert build_extra_filename("extra", 5) == "Extra 5.mkv"

    def test_descriptive_title_name_used_as_stem(self) -> None:
        result = build_extra_filename(
            "behindthescenes", 1, title_name="The Making of Rosencrantz"
        )
        assert result == "The Making of Rosencrantz.mkv"

    def test_descriptive_title_name_sanitized(self) -> None:
        result = build_extra_filename(
            "deletedscene", 1, title_name="Deleted: The Library Scene"
        )
        assert result == "Deleted The Library Scene.mkv"

    def test_generic_title_name_falls_back_to_counter(self) -> None:
        # Title_01 is generic — should use counter format
        result = build_extra_filename("featurette", 2, title_name="Title_01")
        assert result == "Featurette 2.mkv"

    def test_generic_bare_t_number_falls_back_to_counter(self) -> None:
        result = build_extra_filename("trailer", 1, title_name="t03")
        assert result == "Trailer 1.mkv"

    def test_none_title_name_uses_counter(self) -> None:
        result = build_extra_filename("interview", 4, title_name=None)
        assert result == "Interview 4.mkv"

    def test_counter_increments_reflected_in_filename(self) -> None:
        first = build_extra_filename("featurette", 1)
        second = build_extra_filename("featurette", 2)
        assert "1" in first
        assert "2" in second

    def test_no_type_suffix_in_filename(self) -> None:
        # The old format appended "-behindthescenes"; new format must not
        result = build_extra_filename("behindthescenes", 1)
        assert "-behindthescenes" not in result


# ---------------------------------------------------------------------------
# scan_existing_extras
# ---------------------------------------------------------------------------

class TestScanExistingExtras:
    def test_empty_movie_dir_returns_empty_dict(self, tmp_path: Path) -> None:
        movie_dir, _ = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        result = scan_existing_extras(movie_dir)
        assert result == {}

    def test_nonexistent_movie_dir_returns_empty_dict(self, tmp_path: Path) -> None:
        result = scan_existing_extras(tmp_path / "no_such_movie")
        assert result == {}

    def test_single_counter_file_detected(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        (extra_dirs["behindthescenes"] / "Behind the Scenes 1.mkv").touch()
        result = scan_existing_extras(movie_dir)
        assert result["behindthescenes"] == 1

    def test_highest_counter_returned_for_type(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        for num in range(1, 4):
            (extra_dirs["featurette"] / f"Featurette {num}.mkv").touch()
        result = scan_existing_extras(movie_dir)
        assert result["featurette"] == 3

    def test_multiple_types_tracked_independently(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        (extra_dirs["behindthescenes"] / "Behind the Scenes 2.mkv").touch()
        (extra_dirs["trailer"] / "Trailer 1.mkv").touch()
        result = scan_existing_extras(movie_dir)
        assert result["behindthescenes"] == 2
        assert result["trailer"] == 1

    def test_descriptive_named_files_not_counted(self, tmp_path: Path) -> None:
        # Files with descriptive names (no counter pattern) should be ignored
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        (extra_dirs["behindthescenes"] / "The Making of Rosencrantz.mkv").touch()
        result = scan_existing_extras(movie_dir)
        assert "behindthescenes" not in result

    def test_non_mkv_files_ignored(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        (extra_dirs["featurette"] / "readme.txt").touch()
        result = scan_existing_extras(movie_dir)
        assert result == {}

    def test_scans_all_eight_subdirs(self, tmp_path: Path) -> None:
        movie_dir, extra_dirs = build_jellyfin_tree(tmp_path, "Test Movie", 2000)
        (extra_dirs["deletedscene"] / "Deleted Scene 1.mkv").touch()
        (extra_dirs["interview"] / "Interview 1.mkv").touch()
        (extra_dirs["scene"] / "Scene 1.mkv").touch()
        result = scan_existing_extras(movie_dir)
        assert result["deletedscene"] == 1
        assert result["interview"] == 1
        assert result["scene"] == 1


# ---------------------------------------------------------------------------
# build_tv_tree
# ---------------------------------------------------------------------------

class TestBuildTvTree:
    def test_returns_season_dir_and_dict(self, tmp_path: Path) -> None:
        season_dir, extra_dirs = build_tv_tree(tmp_path, "The Wire", 1)
        assert season_dir.is_dir()
        assert isinstance(extra_dirs, dict)

    def test_season_dir_path_structure(self, tmp_path: Path) -> None:
        season_dir, _ = build_tv_tree(tmp_path, "The Wire", 1)
        assert season_dir == tmp_path / "Shows" / "The Wire" / "Season 01"

    def test_season_zero_produces_season_00(self, tmp_path: Path) -> None:
        season_dir, _ = build_tv_tree(tmp_path, "Some Show", 0)
        assert season_dir.name == "Season 00"

    def test_season_number_zero_padded_to_two_digits(self, tmp_path: Path) -> None:
        season_dir, _ = build_tv_tree(tmp_path, "Some Show", 5)
        assert season_dir.name == "Season 05"

    def test_season_number_above_nine_not_padded_further(self, tmp_path: Path) -> None:
        season_dir, _ = build_tv_tree(tmp_path, "Some Show", 12)
        assert season_dir.name == "Season 12"

    def test_sanitizes_show_name(self, tmp_path: Path) -> None:
        season_dir, _ = build_tv_tree(tmp_path, "Alien: The Series", 1)
        assert "Shows" in str(season_dir)
        assert "Alien The Series" in str(season_dir)

    def test_creates_all_eight_extra_type_subdirs(self, tmp_path: Path) -> None:
        season_dir, extra_dirs = build_tv_tree(tmp_path, "The Wire", 1)
        created_names = {path.name for path in season_dir.iterdir() if path.is_dir()}
        assert created_names == _ALL_SUBDIRS

    def test_dict_keys_cover_all_eight_types(self, tmp_path: Path) -> None:
        _, extra_dirs = build_tv_tree(tmp_path, "The Wire", 1)
        assert set(extra_dirs.keys()) == {
            "behindthescenes", "deletedscene", "featurette", "interview",
            "scene", "short", "trailer", "extra",
        }

    def test_dict_values_are_subdirs_of_season_dir(self, tmp_path: Path) -> None:
        season_dir, extra_dirs = build_tv_tree(tmp_path, "The Wire", 1)
        for path in extra_dirs.values():
            assert path.parent == season_dir
            assert path.is_dir()

    def test_idempotent_when_dirs_exist(self, tmp_path: Path) -> None:
        build_tv_tree(tmp_path, "The Wire", 1)
        season_dir, extra_dirs = build_tv_tree(tmp_path, "The Wire", 1)
        assert season_dir.is_dir()
        assert all(path.is_dir() for path in extra_dirs.values())


# ---------------------------------------------------------------------------
# build_episode_filename
# ---------------------------------------------------------------------------

class TestBuildEpisodeFilename:
    def test_basic_episode_no_title(self) -> None:
        assert build_episode_filename("The Wire", 1, 3) == "The Wire S01E03.mkv"

    def test_with_episode_title(self) -> None:
        assert build_episode_filename("The Wire", 1, 3, "The Buys") == \
            "The Wire S01E03 - The Buys.mkv"

    def test_season_zero_padded(self) -> None:
        result = build_episode_filename("Some Show", 1, 1)
        assert "S01E01" in result

    def test_episode_zero_padded(self) -> None:
        result = build_episode_filename("Some Show", 1, 5)
        assert "S01E05" in result

    def test_season_above_nine_not_padded_further(self) -> None:
        result = build_episode_filename("Some Show", 12, 1)
        assert "S12E01" in result

    def test_episode_above_nine_not_padded_further(self) -> None:
        result = build_episode_filename("Some Show", 1, 10)
        assert "S01E10" in result

    def test_season_zero_produces_s00(self) -> None:
        result = build_episode_filename("Some Show", 0, 1)
        assert "S00E01" in result

    def test_sanitizes_show_name(self) -> None:
        result = build_episode_filename("Alien: The Series", 1, 1)
        assert "Alien The Series" in result
        assert ":" not in result

    def test_sanitizes_episode_title(self) -> None:
        result = build_episode_filename("Show", 1, 1, "Pilot: Part 1")
        assert "Pilot Part 1" in result
        assert ":" not in result

    def test_none_episode_title_omitted(self) -> None:
        result = build_episode_filename("Show", 1, 1, episode_title=None)
        assert " - " not in result

    def test_returns_mkv_extension(self) -> None:
        assert build_episode_filename("Show", 1, 1).endswith(".mkv")
        assert build_episode_filename("Show", 1, 1, "Episode").endswith(".mkv")


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
        dest = tmp_path / "alpha" / "beta" / "dest.mkv"
        safe_move(src, dest)
        assert dest.exists()

    def test_raises_if_destination_exists(self, tmp_path: Path) -> None:
        src = tmp_path / "source.mkv"
        dest = tmp_path / "dest.mkv"
        src.write_bytes(b"src")
        dest.write_bytes(b"existing")
        with pytest.raises(FileExistsError):
            safe_move(src, dest)
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
        cleanup(tmp_path / "nonexistent")  # must not raise


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
        result = format_size(6_979_534_848)
        assert result.endswith("GB")
