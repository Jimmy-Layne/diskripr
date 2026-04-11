Jellyfin Naming Conventions
===========================

diskripr uses the Jellyfin **type-subdirectory** convention for all extra
content.  This page is the canonical reference for every directory layout and
filename format decision made by the organize stage.  The implementation lives
in :mod:`diskripr.util.jellyfin_filesystem`.

.. contents:: Contents
   :local:
   :depth: 2

---

Why type-subdirectories?
------------------------

Jellyfin supports two conventions for identifying extras:

* **Filename-suffix convention** — embed the type in the filename stem,
  e.g. ``The Library-deleted.mkv``.
* **Type-subdirectory convention** — place the file in a named subdirectory
  and use any filename stem, e.g. ``deleted scenes/The Library.mkv``.

diskripr uses the **type-subdirectory convention** exclusively.  The filename
stem is shown directly to the user in the Jellyfin UI as the display name.
With the subdirectory convention the stem is clean — ``The Library`` — rather
than ``The Library-deleted``.  The subdirectory already communicates the type
to Jellyfin; repeating it in the name adds noise.

The subdirectory names below are Jellyfin-specified strings and **must not be
changed**.  Only the filename stems are free-form.

---

Extra-type subdirectory reference
----------------------------------

+------------------------+-----------------------+---------------------------------------+
| Jellyfin extra type    | Subdirectory name     | Example filename                      |
+========================+=======================+=======================================+
| Behind the Scenes      | ``behind the scenes`` | ``The Making of Rosencrantz.mkv``     |
+------------------------+-----------------------+---------------------------------------+
| Deleted Scene          | ``deleted scenes``    | ``The Library.mkv``                   |
+------------------------+-----------------------+---------------------------------------+
| Featurette             | ``featurettes``       | ``Original Theatrical Trailer.mkv``   |
+------------------------+-----------------------+---------------------------------------+
| Interview              | ``interviews``        | ``Cast Interview.mkv``                |
+------------------------+-----------------------+---------------------------------------+
| Scene                  | ``scenes``            | ``Opening Sequence.mkv``              |
+------------------------+-----------------------+---------------------------------------+
| Short                  | ``shorts``            | ``Short Film.mkv``                    |
+------------------------+-----------------------+---------------------------------------+
| Trailer                | ``trailers``          | ``Theatrical Trailer.mkv``            |
+------------------------+-----------------------+---------------------------------------+
| Generic extra          | ``extras``            | ``Extra 1.mkv``                       |
+------------------------+-----------------------+---------------------------------------+

All eight subdirectories are created by :func:`~diskripr.util.jellyfin_filesystem.build_jellyfin_tree`
and :func:`~diskripr.util.jellyfin_filesystem.build_tv_tree` even when they
will remain empty.  Jellyfin ignores empty directories.

---

Filename stems
--------------

When the disc provides a descriptive title name (anything that is *not* a
MakeMKV fallback pattern such as ``Title_01`` or ``t03``), the sanitized name
is used directly as the stem — ``The Library.mkv``, ``Cast Interview.mkv``.

When no descriptive name is available the stem falls back to a type-prefixed
counter: ``Deleted Scene 1.mkv``, ``Featurette 3.mkv``.  The ``-<type>``
suffix used by the filename-suffix convention is **not** added; the
subdirectory already communicates the type.

The counter is per-type and is determined at organize time by scanning the
target directory with
:func:`~diskripr.util.jellyfin_filesystem.scan_existing_extras`.

---

Movie directory tree
--------------------

Single-disc movie example::

    dvd_output/
    └── movies/
        └── Rosencrantz and Guildenstern Are Dead (1990)/
            ├── Rosencrantz and Guildenstern Are Dead (1990).mkv
            ├── behind the scenes/
            │   └── The Making of Rosencrantz.mkv
            ├── deleted scenes/
            │   └── The Library.mkv
            ├── featurettes/
            ├── interviews/
            │   └── Cast Interview.mkv
            ├── scenes/
            ├── shorts/
            ├── trailers/
            │   └── Theatrical Trailer.mkv
            └── extras/

Multi-disc movie example (``diskripr movie rip --disc 1`` then ``--disc 2``)::

    dvd_output/
    └── movies/
        └── Lawrence of Arabia (1962)/
            ├── Lawrence of Arabia (1962) - Part1.mkv
            ├── Lawrence of Arabia (1962) - Part2.mkv
            ├── behind the scenes/
            │   └── David Lean Interview.mkv
            ├── deleted scenes/
            ├── featurettes/
            │   └── Restoration Featurette.mkv
            ├── interviews/
            ├── scenes/
            ├── shorts/
            ├── trailers/
            └── extras/

---

TV season directory tree
------------------------

TV discs use a parallel structure under ``Shows/``::

    dvd_output/
    └── Shows/
        └── The Wire/
            └── Season 01/
                ├── The Wire S01E01 - The Target.mkv
                ├── The Wire S01E02 - The Detail.mkv
                ├── The Wire S01E03 - The Buys.mkv
                ├── behind the scenes/
                ├── deleted scenes/
                │   └── Deleted Scene 1.mkv
                ├── featurettes/
                ├── interviews/
                ├── scenes/
                ├── shorts/
                ├── trailers/
                └── extras/

Season zero (specials) uses ``Season 00``::

    dvd_output/
    └── Shows/
        └── The Wire/
            └── Season 00/
                ├── The Wire S00E01.mkv
                └── extras/

Episode filenames follow the ``S<SS>E<EE>`` format with zero-padded two-digit
season and episode numbers.  An optional episode title is appended after a
`` - `` separator when the disc provides one.

See :func:`~diskripr.util.jellyfin_filesystem.build_episode_filename` for the
exact format.

---

API reference
-------------

See :doc:`/api/util` for the full function signatures and docstrings.
