"""Heuristic classification for diskripr extras and episode detection.

This module is the single authoritative home for all disc-content classification
logic. It is deliberately kept free of I/O, subprocess calls, and pipeline state
so that every function here can be tested in pure-Python unit tests without any
external dependencies.

**Extras classification**

:func:`classify_extra` evaluates a 13-rule signal chain against a
:class:`TitleSignals` bundle and returns a :class:`ClassificationResult`
containing the assigned Jellyfin extra type, confidence level, an optional
display name (``None`` when the title name is a MakeMKV fallback pattern), and
a list of signal names used to reach the decision.

Rules 1–5 are keyword matches on the title name (case-insensitive) and produce
``"high"`` confidence on a match, short-circuiting all later rules. Rule 6 is
a structural test (short duration + single chapter) producing ``"medium"``
confidence, also short-circuiting. Rules 7–12 are cumulative: each matching
rule contributes one point to a confidence score and may suggest a Jellyfin
type. Rule 13 is the fallback for titles that match none of the preceding rules.

**Episode clustering**

:func:`cluster_episodes` partitions a list of :class:`~diskripr.models.Title`
objects into episode candidates and extra candidates using duration clustering
and a VTS secondary check. It is only called by the show pipeline.

**Display name logic**

:func:`is_generic_title_name` identifies MakeMKV fallback names such as
``Title_01`` and ``t03``. When a name is generic the pipeline falls back to a
counter-based filename; when it is descriptive the sanitized name is used
directly as the Jellyfin display name.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from typing import Literal, Optional

from diskripr.models import JellyfinExtraType, Title

Confidence = Literal["high", "medium", "low"]

# MakeMKV fallback name patterns: "Title_NN" or bare "tNN"
_GENERIC_TITLE_RE = re.compile(r"^(Title_\d+|t\d+)$", re.IGNORECASE)

# Standalone "short" must not match "behind the scenes" etc.
_SHORT_KEYWORD_RE = re.compile(r"\bshort\b", re.IGNORECASE)

_MINIMUM_TRAILER_DURATION_SECONDS = 180  # 3 minutes


@dataclass
class TitleSignals:  # pylint: disable=too-many-instance-attributes
    """All signals available for heuristic classification of a single title."""

    title: Title
    vts_number: Optional[int] = None          # from lsdvd; None if unavailable
    ttn: Optional[int] = None                  # Title Track Number within VTS
    audio_stream_count: Optional[int] = None  # from lsdvd title line
    cell_count: Optional[int] = None          # from lsdvd title line
    segment_count: Optional[int] = None       # from MakeMKV TINFO attr 25
    segments_map: Optional[str] = None        # from MakeMKV TINFO attr 26
    reference_vts: Optional[int] = None       # VTS of the primary content
    pgc_count_in_vts: Optional[int] = None    # PGC count in this title's VTS
    cell_durations: Optional[list[int]] = None  # per-cell durations in seconds


@dataclass
class ClassificationResult:
    """Result of classifying a title as a Jellyfin extra type."""

    extra_type: JellyfinExtraType
    confidence: Confidence
    display_name: Optional[str]
    signals_used: list[str] = field(default_factory=list)


def is_generic_title_name(name: str) -> bool:
    """Return ``True`` when *name* matches a MakeMKV fallback pattern.

    MakeMKV uses ``Title_NN`` and ``tNN`` as placeholder names when the disc
    does not carry a descriptive title string. These names convey no useful
    information to the user and must not be passed through to Jellyfin display
    names.

    >>> is_generic_title_name("Title_01")
    True
    >>> is_generic_title_name("t03")
    True
    >>> is_generic_title_name("The Making of Rosencrantz")
    False
    """
    return bool(_GENERIC_TITLE_RE.match(name))


def _unique_segment_count(segments_map: Optional[str]) -> int:
    """Return the number of unique segment indices in *segments_map*.

    ``segments_map`` is a raw MakeMKV TINFO attr-26 string such as ``"0,1,2"``
    or ``"0,5,11,14"``. Returns 0 when the string is absent or unparseable.
    """
    if not segments_map:
        return 0
    try:
        return len({int(seg.strip()) for seg in segments_map.split(",")})
    except ValueError:
        return 0


def _score_to_confidence(score: int) -> Confidence:
    """Convert an integer cumulative score to a :data:`Confidence` literal."""
    if score >= 3:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def classify_extra(  # pylint: disable=too-many-return-statements,too-many-branches
    signals: TitleSignals,
) -> ClassificationResult:
    """Classify a disc title as a Jellyfin extra type using a 13-rule chain.

    Rules 1–6 produce an immediate result when matched; later rules are not
    evaluated. Rules 7–12 are cumulative: each match increments a confidence
    score and may revise the suggested type. Rule 13 is the catch-all fallback.

    :param signals: All available metadata signals for the title.
    :returns: A :class:`ClassificationResult` with the assigned type,
              confidence, optional display name, and list of signals used.
    """
    title = signals.title
    name_lower = title.name.lower()
    display_name: Optional[str] = (
        None if is_generic_title_name(title.name) else title.name
    )

    # ------------------------------------------------------------------
    # Rules 1–5: title-name keyword matches → high confidence, short-circuit
    # ------------------------------------------------------------------

    # Rule 1: trailer / teaser
    if "trailer" in name_lower or "teaser" in name_lower:
        return ClassificationResult(
            extra_type="trailer",
            confidence="high",
            display_name=display_name,
            signals_used=["title_name_keyword:trailer_teaser"],
        )

    # Rule 2: interview
    if "interview" in name_lower:
        return ClassificationResult(
            extra_type="interview",
            confidence="high",
            display_name=display_name,
            signals_used=["title_name_keyword:interview"],
        )

    # Rule 3: deleted / cut scene
    if "deleted" in name_lower or "cut scene" in name_lower:
        return ClassificationResult(
            extra_type="deletedscene",
            confidence="high",
            display_name=display_name,
            signals_used=["title_name_keyword:deleted_cut_scene"],
        )

    # Rule 4: making / behind / production
    if (
        "making" in name_lower
        or "behind" in name_lower
        or "production" in name_lower
    ):
        return ClassificationResult(
            extra_type="behindthescenes",
            confidence="high",
            display_name=display_name,
            signals_used=["title_name_keyword:making_behind_production"],
        )

    # Rule 5: featurette / short (standalone word)
    if "featurette" in name_lower or _SHORT_KEYWORD_RE.search(name_lower):
        return ClassificationResult(
            extra_type="featurette",
            confidence="high",
            display_name=display_name,
            signals_used=["title_name_keyword:featurette_short"],
        )

    # ------------------------------------------------------------------
    # Rule 6: short duration + single chapter → medium confidence, short-circuit
    # ------------------------------------------------------------------
    if (
        title.duration_seconds < _MINIMUM_TRAILER_DURATION_SECONDS
        and title.chapter_count == 1
    ):
        short_type: JellyfinExtraType = (
            "trailer" if "trailer" in name_lower else "deletedscene"
        )
        return ClassificationResult(
            extra_type=short_type,
            confidence="medium",
            display_name=display_name,
            signals_used=["short_duration_single_chapter"],
        )

    # ------------------------------------------------------------------
    # Rules 7–12: cumulative confidence builder (no short-circuit)
    # ------------------------------------------------------------------
    cumulative_type: Optional[JellyfinExtraType] = None
    confidence_score = 0
    signals_used: list[str] = []

    vts_known = (
        signals.vts_number is not None and signals.reference_vts is not None
    )

    # Rule 7: VTS mismatch from reference → structural indicator of extras block
    if vts_known and signals.vts_number != signals.reference_vts:
        cumulative_type = "extra"
        confidence_score += 1
        signals_used.append("vts_mismatch")

    # Rule 8: same VTS as reference + high unique-segment count → bonus content
    # assembled from scattered VOB cells (characteristic of featurettes)
    if (
        vts_known
        and signals.vts_number == signals.reference_vts
        and _unique_segment_count(signals.segments_map) > 4
    ):
        cumulative_type = "featurette"
        confidence_score += 1
        signals_used.append("high_segment_count")

    # Rule 9: low chapter count → likely a short standalone piece
    if title.chapter_count <= 3:
        if cumulative_type is None:
            cumulative_type = "featurette"
        confidence_score += 1
        signals_used.append("low_chapter_count")

    # Rule 10: single audio track vs. typically multi-track reference content
    if signals.audio_stream_count == 1:
        confidence_score += 1
        signals_used.append("single_audio_track")

    # Rule 11: high PGC count in VTS suggests menu or extras block, not feature
    if signals.pgc_count_in_vts is not None and signals.pgc_count_in_vts >= 8:
        if cumulative_type is None or cumulative_type == "featurette":
            cumulative_type = "extra"
        confidence_score += 1
        signals_used.append("high_pgc_count")

    # Rule 12: all cell durations below 90 s → composed of very short clips
    if signals.cell_durations and all(
        dur < 90 for dur in signals.cell_durations
    ):
        short_cell_type: JellyfinExtraType = (
            "trailer" if "trailer" in name_lower else "deletedscene"
        )
        if cumulative_type is None:
            cumulative_type = short_cell_type
        confidence_score += 1
        signals_used.append("short_cell_durations")

    # ------------------------------------------------------------------
    # Rule 13: fallback — no signal produced a classification
    # ------------------------------------------------------------------
    if not signals_used:
        return ClassificationResult(
            extra_type="extra",
            confidence="low",
            display_name=display_name,
            signals_used=["fallback"],
        )

    return ClassificationResult(
        extra_type=cumulative_type or "extra",
        confidence=_score_to_confidence(confidence_score),
        display_name=display_name,
        signals_used=signals_used,
    )


def cluster_episodes(  # pylint: disable=too-many-locals
    titles: list[Title],
    signals_map: dict[int, TitleSignals],
) -> tuple[list[Title], list[Title]]:
    """Partition *titles* into episode candidates and extra candidates.

    **Duration partitioning**

    The median duration of all titles is computed first. Titles whose duration
    falls within ±25 % of the median (i.e. between 75 % and 125 % of the
    median) are considered core episode candidates. Titles whose duration
    lies between 60 % and 75 % of the median, or between 125 % and 140 % of
    the median, are borderline candidates and remain as episode candidates.
    Titles below 60 % or above 140 % of the median are outer candidates
    subject to the VTS secondary check.

    **VTS secondary check**

    The majority VTS is determined from the initial episode candidates (core +
    borderline). An outer title is promoted to episode when its VTS matches the
    majority VTS (example: an extended pilot episode on the same VTS as regular
    episodes). An outer title that differs from the majority VTS is classified
    as an extra.

    When VTS data is unavailable for all titles (all ``vts_number`` fields are
    ``None``), outer titles are classified as extras unconditionally.

    :param titles: All disc titles to classify. May be empty.
    :param signals_map: Maps ``title.index`` → :class:`TitleSignals`. Entries
        may be absent for titles without lsdvd data.
    :returns: ``(episodes, extras)`` — each list sorted by
        ``Title.index`` ascending.
    """
    if not titles:
        return [], []

    durations = [title.duration_seconds for title in titles]
    median_duration = statistics.median(durations)

    core_lower = 0.75 * median_duration
    core_upper = 1.25 * median_duration
    outer_lower = 0.60 * median_duration
    outer_upper = 1.40 * median_duration

    core_episodes: list[Title] = []
    borderline_episodes: list[Title] = []
    outer_candidates: list[Title] = []

    for title in titles:
        dur = title.duration_seconds
        if core_lower <= dur <= core_upper:
            core_episodes.append(title)
        elif outer_lower <= dur <= outer_upper:
            # Between 60–75 % or 125–140 % — borderline episode
            borderline_episodes.append(title)
        else:
            # Below 60 % or above 140 % — subject to VTS rescue
            outer_candidates.append(title)

    initial_episodes = core_episodes + borderline_episodes

    # Determine majority VTS among initial episode candidates
    vts_counts: dict[int, int] = {}
    for episode in initial_episodes:
        sig = signals_map.get(episode.index)
        if sig is not None and sig.vts_number is not None:
            vts_counts[sig.vts_number] = vts_counts.get(sig.vts_number, 0) + 1
    majority_vts: Optional[int] = (
        max(vts_counts, key=lambda vts: vts_counts[vts])
        if vts_counts
        else None
    )

    final_episodes: list[Title] = list(initial_episodes)
    final_extras: list[Title] = []

    for title in outer_candidates:
        sig = signals_map.get(title.index)
        title_vts = sig.vts_number if sig is not None else None

        if majority_vts is not None and title_vts == majority_vts:
            # Same VTS as episodes → e.g. extended pilot; keep as episode
            final_episodes.append(title)
        else:
            final_extras.append(title)

    final_episodes.sort(key=lambda title: title.index)
    final_extras.sort(key=lambda title: title.index)

    return final_episodes, final_extras
