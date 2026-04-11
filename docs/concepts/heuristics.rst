Heuristics: Extras Classification and Episode Clustering
=========================================================

diskripr 0.2.0 introduces a pure-function heuristics module
(:mod:`diskripr.util.heuristics`) that classifies DVD titles as specific
Jellyfin extra types and clusters TV episode candidates from a mixed disc.
This page documents the signal chain, the episode clustering algorithm, and
the DVD structural background that motivates each decision.

.. contents:: Contents
   :local:
   :depth: 2

---

Background: why heuristics?
-----------------------------

A DVD does not carry a reliable machine-readable field that says "this title
is a deleted scene".  The authoritative source — the DVD menu system — would
require full VM command interpretation to trace button targets; this is
impractical to implement here.

Instead, diskripr harvests every structural signal available from four sources
and evaluates them in a weighted chain:

* **MakeMKV** — title name (from disc metadata), duration, chapter count,
  segment count (attr 25), and segments map (attr 26).
* **lsdvd** — VTS number, Title Track Number (TTN), audio stream count, and
  cell count per title.
* **pyparsedvd (IFO files)** — PGC count per VTS, and per-cell playback
  durations.
* **Pipeline context** — the reference VTS (VTS of the main feature for
  movies; majority VTS of the episode cluster for shows).

Not all signals are available for every disc.  Fields are ``None`` when the
corresponding driver is unavailable or the disc does not carry the information.
The signal chain is designed to degrade gracefully: it produces a result even
when only the title name and duration are known.

---

Extras classification
---------------------

:func:`~diskripr.util.heuristics.classify_extra` accepts a
:class:`~diskripr.util.heuristics.TitleSignals` bundle and returns a
:class:`~diskripr.util.heuristics.ClassificationResult` containing:

* ``extra_type`` — a :data:`~diskripr.models.JellyfinExtraType` literal.
* ``confidence`` — ``"high"``, ``"medium"``, or ``"low"``.
* ``display_name`` — sanitized title name when descriptive; ``None`` when the
  name is a MakeMKV fallback pattern (e.g. ``Title_01``, ``t03``).
* ``signals_used`` — list of signal names for logging and debugging.

Signal chain
~~~~~~~~~~~~

Rules are evaluated in priority order.  Rules 1–6 are **short-circuit**: the
first rule producing confidence ≥ ``"medium"`` stops evaluation.  Rules 7–12
are **cumulative**: each matching rule increments a confidence score and may
suggest a type; all are evaluated before a final result is produced.  Rule 13
is the catch-all fallback.

.. list-table::
   :header-rows: 1
   :widths: 8 30 40 22

   * - Rule
     - Signal
     - Condition
     - Type assigned
   * - 1
     - Title name keyword
     - Name contains ``"trailer"`` or ``"teaser"`` (case-insensitive)
     - ``trailer`` (high)
   * - 2
     - Title name keyword
     - Name contains ``"interview"``
     - ``interview`` (high)
   * - 3
     - Title name keyword
     - Name contains ``"deleted"`` or ``"cut scene"``
     - ``deletedscene`` (high)
   * - 4
     - Title name keyword
     - Name contains ``"making"``, ``"behind"``, or ``"production"``
     - ``behindthescenes`` (high)
   * - 5
     - Title name keyword
     - Name contains ``"featurette"`` or standalone ``"short"``
     - ``featurette`` (high)
   * - 6
     - Duration + chapter count
     - Duration < 3 min **and** chapter count = 1
     - ``trailer`` if name contains ``"trailer"``; else ``deletedscene`` (medium)
   * - 7
     - VTS mismatch
     - ``vts_number`` ≠ ``reference_vts``
     - ``extra`` (+1 confidence point)
   * - 8
     - Segment count
     - Same VTS as reference **and** > 4 unique segments in ``segments_map``
     - ``featurette`` (+1 confidence point)
   * - 9
     - Chapter count
     - ``chapter_count`` ≤ 3
     - ``featurette`` if no type set yet (+1 confidence point)
   * - 10
     - Audio stream count
     - ``audio_stream_count`` = 1
     - No type change (+1 confidence point)
   * - 11
     - PGC count in VTS
     - ``pgc_count_in_vts`` ≥ 8
     - ``extra`` if type is unset or ``featurette`` (+1 confidence point)
   * - 12
     - Cell durations
     - All values in ``cell_durations`` < 90 s
     - ``trailer``/``deletedscene`` based on name (+1 confidence point)
   * - 13
     - Fallback
     - No earlier rule matched
     - ``extra`` (low)

Cumulative confidence scoring (rules 7–12):

* Score ≥ 3 → ``"high"``
* Score ≥ 2 → ``"medium"``
* Score < 2 → ``"low"``

DVD structural background
~~~~~~~~~~~~~~~~~~~~~~~~~

* **VTS (Video Title Set)** — a DVD is divided into VTS blocks.  Feature
  content typically occupies VTS 1; extras are often authored on a separate
  VTS (e.g. VTS 2).  A VTS mismatch between a title and the main feature is a
  strong structural indicator that the title is bonus content.
* **PGC (Program Chain)** — a VTS contains one or more PGCs, each defining a
  sequence of cells.  A VTS with many short PGCs (≥ 8) is structurally
  consistent with a menu or extras block rather than a feature presentation.
* **Segments map (MakeMKV attr 26)** — records the VOB cell indices that make
  up the title.  A title assembled from many scattered cell indices (> 4
  unique segments) is characteristic of a featurette that was interleaved with
  other content on the disc.
* **Cell durations** — per-cell playback times from IFO files.  A title whose
  every cell is under 90 seconds is composed of very short clips, which is
  typical of trailers and deleted-scene montages.

Known limitations
~~~~~~~~~~~~~~~~~

* Titles with a generic MakeMKV name (``Title_NN`` / ``tNN``) cannot benefit
  from rules 1–5.  They receive a counter-based display name regardless of
  classification.
* lsdvd may fail silently on CSS-encrypted discs; all lsdvd-derived fields
  will be ``None`` in that case, and rules 7–10 are skipped.
* IFO parsing requires a readable ``VIDEO_TS`` directory on the mounted disc;
  rules 11–12 are skipped when it is inaccessible.

---

Episode clustering
------------------

:func:`~diskripr.util.heuristics.cluster_episodes` partitions a list of
:class:`~diskripr.models.Title` objects into episode candidates and extra
candidates.  It is called only by the show pipeline.

Algorithm
~~~~~~~~~

1. **Compute median duration** of all titles on the disc.

2. **Duration partitioning** — classify each title relative to the median:

   * **Core episode** — duration within ±25 % of median (75 %–125 %).
   * **Borderline episode** — duration within ±40 % of median (60 %–75 % or
     125 %–140 %); treated as an episode initially.
   * **Outer candidate** — duration below 60 % or above 140 % of median;
     subject to the VTS secondary check.

3. **Determine majority VTS** from the combined set of core and borderline
   episodes.

4. **VTS secondary check** — for each outer candidate:

   * If its VTS matches the majority VTS → promoted to episode (e.g. an
     extended pilot on the same VTS as regular episodes).
   * Otherwise → classified as an extra.

5. Both output lists are sorted by ``Title.index`` ascending.

When VTS data is unavailable for all titles (all ``vts_number`` fields are
``None``), outer candidates are classified as extras unconditionally.

Example
~~~~~~~

Disc with five titles (durations in minutes): 42, 44, 43, 8, 52.

* Median: 43 min.  Core band: 32–54 min.  Outer threshold: < 26 min or > 60 min.
* Titles 42, 44, 43 → core episodes; 52 → borderline episode.
* Title 8 → outer candidate.  Majority VTS from core + borderline = VTS 1.
  Title 8 is on VTS 2 → extra.
* Result: episodes [42, 43, 44, 52 min], extras [8 min].

---

Display name logic
------------------

:func:`~diskripr.util.heuristics.is_generic_title_name` identifies MakeMKV
placeholder names matching the patterns ``Title_NN`` or ``tNN`` (case-insensitive).

When a name is generic:

* ``display_name`` in the result is ``None``.
* The organize stage falls back to ``<Type Label> <counter>.mkv``.

When a name is descriptive:

* ``display_name`` is set to the raw title name.
* :func:`~diskripr.util.jellyfin_filesystem.sanitize_filename` strips illegal
  characters before the name is embedded in the path.
* The file is placed in the appropriate type subdirectory with the sanitized
  name as the stem.

---

API reference
-------------

See :doc:`/api/util` for full function signatures and docstrings.
