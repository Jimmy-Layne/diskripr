"""Tests for ``diskripr.pipeline``.

The pipeline module is currently a docstring stub — none of its stages are
implemented yet.  This file scaffolds the expected test coverage so that
tests can be filled in as each stage is implemented.

All tests are marked ``skip`` with the reason "pipeline not yet implemented".
Remove the skip marker for a test (or its enclosing class) as the
corresponding stage is completed.

Test organisation mirrors the pipeline stages:
1. discover()   — drive detection + title scan
2. _select()    — title selection logic (main / all modes)
3. _classify()  — Jellyfin extra type assignment
4. rip()        — MakeMKV title extraction
5. encode()     — HandBrake re-encoding (optional stage)
6. organize()   — Jellyfin directory tree construction
7. run()        — full pipeline integration path
"""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Stage 1: discover
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="pipeline not yet implemented")
class TestDiscover:
    def test_returns_disc_info_with_drive(self) -> None:
        raise NotImplementedError

    def test_fails_when_device_not_found(self) -> None:
        raise NotImplementedError

    def test_lsdvd_failure_is_non_fatal(self) -> None:
        raise NotImplementedError

    def test_falls_back_to_drive_index_zero_when_device_not_matched(self) -> None:
        raise NotImplementedError

    def test_fails_with_diagnostic_when_no_titles_after_filtering(self) -> None:
        raise NotImplementedError

    def test_filters_titles_below_min_length(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Title selection logic (_select / internal)
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="pipeline not yet implemented")
class TestTitleSelection:
    def test_main_mode_returns_only_longest_title(self) -> None:
        raise NotImplementedError

    def test_all_mode_returns_all_titles(self) -> None:
        raise NotImplementedError

    def test_main_title_is_excluded_from_extras_list(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Extras classification logic (_classify / internal)
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="pipeline not yet implemented")
class TestExtrasClassification:
    def test_all_mode_defaults_extras_to_extra_type(self) -> None:
        raise NotImplementedError

    def test_auto_numbers_extras_of_same_type(self) -> None:
        raise NotImplementedError

    def test_numbering_continues_from_existing_extras_in_multi_disc(self) -> None:
        raise NotImplementedError

    def test_output_filename_matches_jellyfin_format(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Stage 2: rip
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="pipeline not yet implemented")
class TestRipStage:
    def test_rips_main_title_to_temp_dir(self) -> None:
        raise NotImplementedError

    def test_rips_extras_when_present_in_selection(self) -> None:
        raise NotImplementedError

    def test_per_title_rip_error_does_not_abort_remaining_titles(self) -> None:
        raise NotImplementedError

    def test_returns_list_of_rip_results(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Stage 3: encode (optional)
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="pipeline not yet implemented")
class TestEncodeStage:
    def test_skipped_when_encode_format_is_none(self) -> None:
        raise NotImplementedError

    def test_skipped_when_handbrake_not_installed(self) -> None:
        raise NotImplementedError

    def test_encode_failure_keeps_original_mkv(self) -> None:
        raise NotImplementedError

    def test_keep_original_flag_moves_source_to_originals_subdir(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Stage 4: organize
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="pipeline not yet implemented")
class TestOrganizeStage:
    def test_main_feature_placed_at_correct_jellyfin_path(self) -> None:
        raise NotImplementedError

    def test_extras_placed_in_extras_subdir(self) -> None:
        raise NotImplementedError

    def test_multi_disc_main_feature_gets_part_suffix(self) -> None:
        raise NotImplementedError

    def test_warns_when_single_disc_movie_dir_already_exists(self) -> None:
        raise NotImplementedError

    def test_multi_disc_adds_files_alongside_existing_without_warning(self) -> None:
        raise NotImplementedError

    def test_temp_dir_cleaned_up_after_organize(self) -> None:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Full pipeline run()
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="pipeline not yet implemented")
class TestPipelineRun:
    def test_full_pipeline_main_only_no_encode(self) -> None:
        raise NotImplementedError

    def test_full_pipeline_with_encode(self) -> None:
        raise NotImplementedError

    def test_full_pipeline_all_mode_with_extras(self) -> None:
        raise NotImplementedError

    def test_multi_disc_second_run_merges_extras_without_collision(self) -> None:
        raise NotImplementedError
