"""Tests for ``diskripr.util.heuristics``.

Covers:
- ``is_generic_title_name()`` — generic vs. descriptive title name detection
- ``classify_extra()`` — one test case per rule in the 13-rule signal chain
- Short-circuit behaviour: high-confidence early match suppresses later rules
- Confidence modifier accumulation for cumulative rules 7–12
- ``display_name`` is ``None`` for generic names; set for descriptive names
- ``cluster_episodes()`` — clean cluster, outlier extras, extended pilot rescue,
  VTS mismatch reclassification, and sort-order guarantee
"""

from __future__ import annotations

from diskripr.models import Title
from diskripr.util.heuristics import (
    TitleSignals,
    classify_extra,
    cluster_episodes,
    is_generic_title_name,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_title(  # noqa: PLR0913
    index: int = 0,
    name: str = "Test Title",
    duration: str = "00:30:00",
    size_bytes: int = 1_000_000_000,
    chapter_count: int = 5,
    stream_summary: str = "",
    title_type: str = "extra",
) -> Title:
    """Construct a ``Title`` with sensible defaults for extra-classification tests."""
    return Title(
        index=index,
        name=name,
        duration=duration,
        size_bytes=size_bytes,
        chapter_count=chapter_count,
        stream_summary=stream_summary,
        title_type=title_type,  # type: ignore[arg-type]
    )


def _signals(title: Title, **overrides: object) -> TitleSignals:
    """Wrap *title* in a ``TitleSignals`` with optional field overrides."""
    kwargs: dict[str, object] = dict(
        title=title,
        vts_number=None,
        ttn=None,
        audio_stream_count=None,
        cell_count=None,
        segment_count=None,
        segments_map=None,
        reference_vts=None,
        pgc_count_in_vts=None,
        cell_durations=None,
    )
    kwargs.update(overrides)
    return TitleSignals(**kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# is_generic_title_name
# ---------------------------------------------------------------------------

class TestIsGenericTitleName:
    def test_title_underscore_number_is_generic(self) -> None:
        assert is_generic_title_name("Title_01") is True

    def test_title_underscore_two_digits(self) -> None:
        assert is_generic_title_name("Title_12") is True

    def test_bare_t_number_is_generic(self) -> None:
        assert is_generic_title_name("t03") is True

    def test_bare_t_single_digit(self) -> None:
        assert is_generic_title_name("t1") is True

    def test_uppercase_t_number_is_generic(self) -> None:
        assert is_generic_title_name("T05") is True

    def test_descriptive_name_is_not_generic(self) -> None:
        assert is_generic_title_name("The Making of Rosencrantz") is False

    def test_name_with_digits_suffix_is_not_generic(self) -> None:
        # "Deleted Scene 1" has extra text beyond the pattern
        assert is_generic_title_name("Deleted Scene 1") is False

    def test_empty_string_is_not_generic(self) -> None:
        assert is_generic_title_name("") is False

    def test_title_without_underscore_is_not_generic(self) -> None:
        # "Title01" has no underscore — does not match Title_NN
        assert is_generic_title_name("Title01") is False

    def test_partial_pattern_embedded_in_longer_name_is_not_generic(self) -> None:
        # Pattern must match the full string
        assert is_generic_title_name("t03 Extended Cut") is False


# ---------------------------------------------------------------------------
# classify_extra — display_name logic
# ---------------------------------------------------------------------------

class TestDisplayName:
    def test_generic_title_name_yields_none_display_name(self) -> None:
        title = _make_title(name="Title_01", chapter_count=1, duration="00:01:30")
        result = classify_extra(_signals(title))
        assert result.display_name is None

    def test_descriptive_title_name_preserved_as_display_name(self) -> None:
        title = _make_title(
            name="The Making of Rosencrantz", chapter_count=8, duration="00:45:00"
        )
        result = classify_extra(_signals(title))
        assert result.display_name == "The Making of Rosencrantz"


# ---------------------------------------------------------------------------
# classify_extra — Rules 1–5: keyword matches (high confidence, short-circuit)
# ---------------------------------------------------------------------------

class TestKeywordRules:
    """One test per keyword rule; also verifies confidence and signals_used."""

    def test_rule1_trailer_keyword(self) -> None:
        title = _make_title(name="Original Theatrical Trailer", chapter_count=1)
        result = classify_extra(_signals(title))
        assert result.extra_type == "trailer"
        assert result.confidence == "high"
        assert "title_name_keyword:trailer_teaser" in result.signals_used

    def test_rule1_teaser_keyword(self) -> None:
        title = _make_title(name="Teaser Promo Reel", chapter_count=2)
        result = classify_extra(_signals(title))
        assert result.extra_type == "trailer"
        assert result.confidence == "high"

    def test_rule1_case_insensitive(self) -> None:
        title = _make_title(name="THEATRICAL TRAILER")
        result = classify_extra(_signals(title))
        assert result.extra_type == "trailer"

    def test_rule2_interview_keyword(self) -> None:
        title = _make_title(name="Cast Interview with Tom Stoppard", chapter_count=3)
        result = classify_extra(_signals(title))
        assert result.extra_type == "interview"
        assert result.confidence == "high"
        assert "title_name_keyword:interview" in result.signals_used

    def test_rule3_deleted_keyword(self) -> None:
        title = _make_title(name="Deleted Opening Scene", chapter_count=2)
        result = classify_extra(_signals(title))
        assert result.extra_type == "deletedscene"
        assert result.confidence == "high"
        assert "title_name_keyword:deleted_cut_scene" in result.signals_used

    def test_rule3_cut_scene_keyword(self) -> None:
        title = _make_title(name="Extended cut scene from Act 2", chapter_count=2)
        result = classify_extra(_signals(title))
        assert result.extra_type == "deletedscene"
        assert result.confidence == "high"

    def test_rule4_making_keyword(self) -> None:
        title = _make_title(name="The Making of Rosencrantz", chapter_count=12)
        result = classify_extra(_signals(title))
        assert result.extra_type == "behindthescenes"
        assert result.confidence == "high"
        assert "title_name_keyword:making_behind_production" in result.signals_used

    def test_rule4_behind_keyword(self) -> None:
        title = _make_title(name="Behind the Scenes: Rehearsals", chapter_count=8)
        result = classify_extra(_signals(title))
        assert result.extra_type == "behindthescenes"
        assert result.confidence == "high"

    def test_rule4_production_keyword(self) -> None:
        title = _make_title(name="Production Diaries", chapter_count=6)
        result = classify_extra(_signals(title))
        assert result.extra_type == "behindthescenes"
        assert result.confidence == "high"

    def test_rule5_featurette_keyword(self) -> None:
        title = _make_title(name="A Short Featurette on Stagecraft", chapter_count=4)
        result = classify_extra(_signals(title))
        assert result.extra_type == "featurette"
        assert result.confidence == "high"
        assert "title_name_keyword:featurette_short" in result.signals_used

    def test_rule5_short_standalone_keyword(self) -> None:
        # "short" appears as a whole word
        title = _make_title(name="The Short Film", chapter_count=3)
        result = classify_extra(_signals(title))
        assert result.extra_type == "featurette"
        assert result.confidence == "high"

    def test_rule5_short_embedded_does_not_match_standalone(self) -> None:
        # "shortened" does not contain the standalone word "short"
        title = _make_title(
            name="A shortened version", chapter_count=5, duration="00:20:00"
        )
        result = classify_extra(_signals(title))
        # Should NOT match rule 5 — falls through to later rules
        assert "title_name_keyword:featurette_short" not in result.signals_used


# ---------------------------------------------------------------------------
# classify_extra — Rule 6: short duration + single chapter (medium confidence)
# ---------------------------------------------------------------------------

class TestRule6ShortDurationSingleChapter:
    def test_below_3min_single_chapter_gives_deletedscene(self) -> None:
        # No keyword in name; should fall through to rule 6
        title = _make_title(
            name="Title_01", duration="00:02:30", chapter_count=1
        )
        result = classify_extra(_signals(title))
        assert result.extra_type == "deletedscene"
        assert result.confidence == "medium"
        assert "short_duration_single_chapter" in result.signals_used

    def test_below_3min_name_has_trailer_gives_trailer(self) -> None:
        title = _make_title(name="Some Trailer", duration="00:01:45", chapter_count=1)
        # Rule 1 fires first because "trailer" is in the name
        result = classify_extra(_signals(title))
        assert result.extra_type == "trailer"
        assert result.confidence == "high"

    def test_exactly_3min_does_not_trigger_rule6(self) -> None:
        # 3:00 = 180 s; rule 6 requires < 180 s
        title = _make_title(
            name="Title_02", duration="00:03:00", chapter_count=1
        )
        result = classify_extra(_signals(title))
        assert "short_duration_single_chapter" not in result.signals_used

    def test_below_3min_multiple_chapters_does_not_trigger(self) -> None:
        title = _make_title(
            name="Title_03", duration="00:02:00", chapter_count=3
        )
        result = classify_extra(_signals(title))
        assert "short_duration_single_chapter" not in result.signals_used


# ---------------------------------------------------------------------------
# classify_extra — Short-circuit: high-confidence match stops evaluation
# ---------------------------------------------------------------------------

class TestShortCircuit:
    def test_high_confidence_rule1_suppresses_all_later_rules(self) -> None:
        # "trailer" in name → rule 1 fires; VTS mismatch from rule 7 ignored
        title = _make_title(
            name="Theatrical Trailer", duration="00:02:30", chapter_count=1
        )
        sig = _signals(title, vts_number=2, reference_vts=1)
        result = classify_extra(sig)
        assert result.extra_type == "trailer"
        assert result.confidence == "high"
        # Rule 7 should NOT be in signals_used
        assert "vts_mismatch" not in result.signals_used

    def test_high_confidence_rule4_suppresses_rule6(self) -> None:
        # "behind" → rule 4 fires even though duration < 3 min
        title = _make_title(
            name="Behind the Scenes", duration="00:02:00", chapter_count=1
        )
        result = classify_extra(_signals(title))
        assert result.extra_type == "behindthescenes"
        assert result.confidence == "high"
        assert "short_duration_single_chapter" not in result.signals_used

    def test_medium_confidence_rule6_suppresses_cumulative_rules(self) -> None:
        # No keyword; rule 6 medium → cumulative rules must not fire
        title = _make_title(
            name="Title_05", duration="00:02:00", chapter_count=1
        )
        sig = _signals(title, vts_number=2, reference_vts=1)
        result = classify_extra(sig)
        assert result.confidence == "medium"
        assert "vts_mismatch" not in result.signals_used


# ---------------------------------------------------------------------------
# classify_extra — Cumulative rules 7–12 and confidence accumulation
# ---------------------------------------------------------------------------

class TestCumulativeRules:
    def test_rule7_vts_mismatch_assigns_extra(self) -> None:
        title = _make_title(name="Title_10", chapter_count=6, duration="00:20:00")
        sig = _signals(title, vts_number=2, reference_vts=1)
        result = classify_extra(sig)
        assert result.extra_type == "extra"
        assert "vts_mismatch" in result.signals_used

    def test_rule7_absent_when_vts_unknown(self) -> None:
        title = _make_title(name="Title_10", chapter_count=6, duration="00:20:00")
        result = classify_extra(_signals(title))  # no VTS data
        assert "vts_mismatch" not in result.signals_used

    def test_rule8_high_segment_count_assigns_featurette(self) -> None:
        # Same VTS as reference + 5 unique segments → featurette
        title = _make_title(name="Title_11", chapter_count=6, duration="00:20:00")
        sig = _signals(
            title,
            vts_number=1,
            reference_vts=1,
            segments_map="0,1,2,3,4",  # 5 unique
        )
        result = classify_extra(sig)
        assert result.extra_type == "featurette"
        assert "high_segment_count" in result.signals_used

    def test_rule8_not_triggered_when_different_vts(self) -> None:
        # Different VTS → rule 8 must not fire (requires same VTS)
        title = _make_title(name="Title_12", chapter_count=6, duration="00:20:00")
        sig = _signals(
            title,
            vts_number=2,
            reference_vts=1,
            segments_map="0,1,2,3,4",
        )
        result = classify_extra(sig)
        assert "high_segment_count" not in result.signals_used

    def test_rule8_not_triggered_with_four_or_fewer_unique_segments(self) -> None:
        title = _make_title(name="Title_13", chapter_count=6, duration="00:20:00")
        # Repeated segments: only 3 unique values
        sig = _signals(
            title,
            vts_number=1,
            reference_vts=1,
            segments_map="0,1,2,1,2",
        )
        result = classify_extra(sig)
        assert "high_segment_count" not in result.signals_used

    def test_rule9_low_chapter_count_assigns_featurette(self) -> None:
        title = _make_title(name="Title_14", chapter_count=2, duration="00:15:00")
        result = classify_extra(_signals(title))
        assert result.extra_type == "featurette"
        assert "low_chapter_count" in result.signals_used

    def test_rule9_exactly_three_chapters_triggers(self) -> None:
        title = _make_title(name="Title_15", chapter_count=3, duration="00:18:00")
        result = classify_extra(_signals(title))
        assert "low_chapter_count" in result.signals_used

    def test_rule9_four_chapters_does_not_trigger(self) -> None:
        title = _make_title(name="Title_16", chapter_count=4, duration="00:20:00")
        result = classify_extra(_signals(title))
        assert "low_chapter_count" not in result.signals_used

    def test_rule10_single_audio_track_adds_confidence(self) -> None:
        # Rule 9 (≤3 chapters) + rule 10 (1 audio) → two cumulative signals
        title = _make_title(name="Title_17", chapter_count=2, duration="00:10:00")
        sig = _signals(title, audio_stream_count=1)
        result = classify_extra(sig)
        assert "low_chapter_count" in result.signals_used
        assert "single_audio_track" in result.signals_used
        # Two cumulative hits → medium confidence
        assert result.confidence in ("medium", "high")

    def test_rule11_high_pgc_count_assigns_extra(self) -> None:
        title = _make_title(name="Title_18", chapter_count=6, duration="00:25:00")
        sig = _signals(title, pgc_count_in_vts=8)
        result = classify_extra(sig)
        assert result.extra_type == "extra"
        assert "high_pgc_count" in result.signals_used

    def test_rule11_fewer_than_eight_pgcs_does_not_trigger(self) -> None:
        title = _make_title(name="Title_19", chapter_count=6, duration="00:25:00")
        sig = _signals(title, pgc_count_in_vts=7)
        result = classify_extra(sig)
        assert "high_pgc_count" not in result.signals_used

    def test_rule12_short_cell_durations_assigns_deletedscene(self) -> None:
        title = _make_title(name="Title_20", chapter_count=6, duration="00:20:00")
        sig = _signals(title, cell_durations=[30, 45, 60, 10, 85])
        result = classify_extra(sig)
        assert result.extra_type == "deletedscene"
        assert "short_cell_durations" in result.signals_used

    def test_rule12_cell_durations_with_trailer_name_gives_trailer(self) -> None:
        # Rule 1 fires first because "trailer" is in the name
        title = _make_title(name="Trailer Reel", chapter_count=6)
        sig = _signals(title, cell_durations=[30, 45, 60])
        result = classify_extra(sig)
        assert result.extra_type == "trailer"
        assert result.confidence == "high"  # keyword rule, not cumulative

    def test_rule12_one_long_cell_does_not_trigger(self) -> None:
        title = _make_title(name="Title_21", chapter_count=6, duration="00:20:00")
        sig = _signals(title, cell_durations=[30, 45, 95])  # 95 ≥ 90
        result = classify_extra(sig)
        assert "short_cell_durations" not in result.signals_used

    def test_confidence_accumulates_across_multiple_rules(self) -> None:
        # Rules 7 + 10 + 11 all fire: VTS mismatch, single audio, high PGC count
        title = _make_title(name="Title_22", chapter_count=6, duration="00:20:00")
        sig = _signals(
            title,
            vts_number=2,
            reference_vts=1,
            audio_stream_count=1,
            pgc_count_in_vts=10,
        )
        result = classify_extra(sig)
        # Three cumulative signals → high confidence
        assert result.confidence == "high"
        assert "vts_mismatch" in result.signals_used
        assert "single_audio_track" in result.signals_used
        assert "high_pgc_count" in result.signals_used

    def test_two_cumulative_rules_give_medium_confidence(self) -> None:
        # Rules 7 + 10: VTS mismatch + single audio → score 2 → medium
        title = _make_title(name="Title_23", chapter_count=6, duration="00:20:00")
        sig = _signals(title, vts_number=2, reference_vts=1, audio_stream_count=1)
        result = classify_extra(sig)
        assert result.confidence == "medium"


# ---------------------------------------------------------------------------
# classify_extra — Rule 13: fallback
# ---------------------------------------------------------------------------

class TestRule13Fallback:
    def test_no_signals_gives_extra_low_confidence(self) -> None:
        # Generic name, long duration, many chapters, no structural signals
        title = _make_title(
            name="Title_99", chapter_count=10, duration="00:45:00"
        )
        result = classify_extra(_signals(title))
        assert result.extra_type == "extra"
        assert result.confidence == "low"
        assert "fallback" in result.signals_used

    def test_fallback_display_name_is_none_for_generic_title(self) -> None:
        title = _make_title(name="t99", chapter_count=10, duration="00:45:00")
        result = classify_extra(_signals(title))
        assert result.display_name is None

    def test_fallback_display_name_set_for_descriptive_title(self) -> None:
        title = _make_title(
            name="Odd Extra Segment", chapter_count=10, duration="00:45:00"
        )
        result = classify_extra(_signals(title))
        assert result.display_name == "Odd Extra Segment"


# ---------------------------------------------------------------------------
# cluster_episodes
# ---------------------------------------------------------------------------

def _episode_title(
    index: int,
    duration: str,
    vts: int | None = None,
) -> tuple[Title, TitleSignals]:
    """Return a (Title, TitleSignals) pair for cluster_episodes tests."""
    title = _make_title(index=index, duration=duration, title_type="main")  # type: ignore[call-arg]
    sig = _signals(title, vts_number=vts, reference_vts=None)
    return title, sig


class TestClusterEpisodes:
    def test_empty_input_returns_empty_lists(self) -> None:
        episodes, extras = cluster_episodes([], {})
        assert episodes == []
        assert extras == []

    def test_clean_cluster_all_same_duration(self) -> None:
        # Five titles with identical duration → all episodes, no extras
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx in range(5):
            title, sig = _episode_title(idx, "00:42:00", vts=1)
            titles.append(title)
            signals_map[idx] = sig

        episodes, extras = cluster_episodes(titles, signals_map)
        assert len(episodes) == 5
        assert extras == []

    def test_outlier_short_title_with_different_vts_is_extra(self) -> None:
        # Four 42-min episodes on VTS 1, one 5-min title on VTS 2
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx in range(4):
            title, sig = _episode_title(idx, "00:42:00", vts=1)
            titles.append(title)
            signals_map[idx] = sig
        extra_title, extra_sig = _episode_title(4, "00:05:00", vts=2)
        titles.append(extra_title)
        signals_map[4] = extra_sig

        episodes, extras = cluster_episodes(titles, signals_map)
        assert len(episodes) == 4
        assert len(extras) == 1
        assert extras[0].index == 4

    def test_extended_pilot_stays_as_episode_when_same_vts(self) -> None:
        # Four 42-min episodes + one 90-min title (>140 %) sharing VTS 1
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx in range(4):
            title, sig = _episode_title(idx, "00:42:00", vts=1)
            titles.append(title)
            signals_map[idx] = sig
        pilot_title, pilot_sig = _episode_title(4, "01:30:00", vts=1)
        titles.append(pilot_title)
        signals_map[4] = pilot_sig

        episodes, extras = cluster_episodes(titles, signals_map)
        # Pilot is > 140 % of 42-min median but shares VTS → kept as episode
        assert len(episodes) == 5
        assert extras == []

    def test_long_outlier_with_different_vts_is_extra(self) -> None:
        # Four 42-min episodes + one 90-min title on VTS 2 → extra
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx in range(4):
            title, sig = _episode_title(idx, "00:42:00", vts=1)
            titles.append(title)
            signals_map[idx] = sig
        outlier_title, outlier_sig = _episode_title(4, "01:30:00", vts=2)
        titles.append(outlier_title)
        signals_map[4] = outlier_sig

        episodes, extras = cluster_episodes(titles, signals_map)
        assert len(episodes) == 4
        assert len(extras) == 1
        assert extras[0].index == 4

    def test_vts_mismatch_reclassification(self) -> None:
        # Three 40-min episodes on VTS 1; two 10-min titles on VTS 2 (< 60 % of 40 min)
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx in range(3):
            title, sig = _episode_title(idx, "00:40:00", vts=1)
            titles.append(title)
            signals_map[idx] = sig
        for idx in range(3, 5):
            title, sig = _episode_title(idx, "00:10:00", vts=2)
            titles.append(title)
            signals_map[idx] = sig

        episodes, extras = cluster_episodes(titles, signals_map)
        episode_indices = {episode.index for episode in episodes}
        extra_indices = {extra.index for extra in extras}
        assert {0, 1, 2} == episode_indices
        assert {3, 4} == extra_indices

    def test_output_sorted_by_title_index_ascending(self) -> None:
        # Insert titles in non-sequential order to verify sort guarantee
        indices_and_durations = [
            (3, "00:42:00", 1),
            (1, "00:42:00", 1),
            (5, "00:05:00", 2),
            (0, "00:42:00", 1),
            (2, "00:42:00", 1),
            (4, "00:05:00", 2),
        ]
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx, dur, vts in indices_and_durations:
            title, sig = _episode_title(idx, dur, vts=vts)
            titles.append(title)
            signals_map[idx] = sig

        episodes, extras = cluster_episodes(titles, signals_map)
        assert [ep.index for ep in episodes] == [0, 1, 2, 3]
        assert [ext.index for ext in extras] == [4, 5]

    def test_no_vts_data_outer_titles_become_extras(self) -> None:
        # Without VTS signals, outer titles (< 60 % of median) cannot be rescued
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx in range(4):
            title, sig = _episode_title(idx, "00:42:00", vts=None)
            titles.append(title)
            signals_map[idx] = sig
        short_title, short_sig = _episode_title(4, "00:05:00", vts=None)
        titles.append(short_title)
        signals_map[4] = short_sig

        episodes, extras = cluster_episodes(titles, signals_map)
        assert len(episodes) == 4
        assert len(extras) == 1
        assert extras[0].index == 4

    def test_single_title_is_only_episode(self) -> None:
        title, sig = _episode_title(0, "00:42:00", vts=1)
        episodes, extras = cluster_episodes([title], {0: sig})
        assert len(episodes) == 1
        assert extras == []

    def test_missing_signals_entry_treated_as_no_vts(self) -> None:
        # signals_map has no entry for the outlier title → cannot rescue by VTS
        titles = []
        signals_map: dict[int, TitleSignals] = {}
        for idx in range(3):
            title, sig = _episode_title(idx, "00:42:00", vts=1)
            titles.append(title)
            signals_map[idx] = sig
        # Title index 3 has no entry in signals_map at all
        extra_title = _make_title(index=3, duration="00:04:00", title_type="extra")  # type: ignore[call-arg]
        titles.append(extra_title)
        # Intentionally not adding to signals_map

        episodes, extras = cluster_episodes(titles, signals_map)
        assert len(episodes) == 3
        assert len(extras) == 1
        assert extras[0].index == 3
