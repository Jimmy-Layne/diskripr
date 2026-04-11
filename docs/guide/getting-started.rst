Getting Started
===============

This guide covers the two primary workflows: ripping a movie disc and ripping
a TV season disc.

.. contents:: Contents
   :local:
   :depth: 2

---

Prerequisites
-------------

* MakeMKV installed and licensed (or in trial mode).
* HandBrake CLI (``HandBrakeCLI``) installed if you want to re-encode.
* A DVD drive accessible at ``/dev/sr0`` (default; override with ``--device``).
* diskripr installed — see :doc:`installation`.

---

Ripping a movie
---------------

**1. Scan the disc first (optional but recommended)**

``diskripr movie scan`` runs the discover stage and prints all titles found
on the disc with their durations, chapter counts, and heuristic
classification guesses::

    diskripr movie scan --name "Rosencrantz and Guildenstern Are Dead" --year 1990

Output example::

    [0] Title_01 — 117 min, 18 chapters  → main feature
    [1] Theatrical Trailer — 2 min, 1 chapter → trailer (high)
    [2] Cast Interview — 14 min, 3 chapters → interview (high)
    [3] t03 — 8 min, 2 chapters → extra (low)

**2. Rip the disc**

::

    diskripr movie rip \
        --name "Rosencrantz and Guildenstern Are Dead" \
        --year 1990 \
        --rip-mode all \
        --encode-format h265

``--rip-mode all`` rips the main feature and all extras.  Use
``--rip-mode main`` to rip only the longest title.  Use
``--rip-mode ask`` to confirm each title interactively.

**3. Output layout**

After ripping and organizing the output directory looks like::

    dvd_output/
    └── movies/
        └── Rosencrantz and Guildenstern Are Dead (1990)/
            ├── Rosencrantz and Guildenstern Are Dead (1990).mkv
            ├── trailers/
            │   └── Theatrical Trailer.mkv
            ├── interviews/
            │   └── Cast Interview.mkv
            └── extras/
                └── Extra 1.mkv

See :doc:`/concepts/jellyfin_naming` for the full directory convention.

---

Ripping a TV season
-------------------

TV discs require you to tell diskripr which season and which episode number
to start from, because the disc itself does not carry absolute episode numbers.

**1. Scan the disc**

``diskripr show scan`` prints the episode cluster guess — which titles are
treated as episodes and which are classified as extras::

    diskripr show scan --show "The Wire" --season 1

Output example::

    Episode candidates (4): titles [0, 1, 2, 3] — ~42 min each
    Extra candidates (1):   title [4] — 8 min (extra, low confidence)

**2. Rip the disc**

::

    diskripr show rip \
        --show "The Wire" \
        --season 1 \
        --start-episode 1 \
        --rip-mode all

``--start-episode`` sets the episode number for the first clustered episode
title.  Subsequent episodes are numbered consecutively.

**3. Output layout**

::

    dvd_output/
    └── Shows/
        └── The Wire/
            └── Season 01/
                ├── The Wire S01E01 - The Target.mkv
                ├── The Wire S01E02 - The Detail.mkv
                ├── The Wire S01E03 - The Buys.mkv
                ├── The Wire S01E04 - Old Cases.mkv
                └── extras/
                    └── Extra 1.mkv

For a second disc of the same season, pass ``--start-episode 5`` (or
whichever episode continues from where disc 1 ended).

---

Next steps
----------

* :doc:`/guide/batch-ripping` — automate a full stack of discs with a job file.
* :doc:`/concepts/jellyfin_naming` — understand the directory convention.
* :doc:`/concepts/heuristics` — how extras are classified automatically.
