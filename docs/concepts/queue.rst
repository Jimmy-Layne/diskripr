Batch Queue (``diskripr queue``)
================================

The ``diskripr queue`` command group lets you rip a stack of discs
unattended by describing every job in a single JSON file.  The runner
validates the file up front, then processes each job in order, handling
disc swap sequencing automatically between jobs.

.. contents:: Contents
   :local:
   :depth: 2

---

Job file format
---------------

A job file is a UTF-8 JSON document with two top-level fields:

``version``
    Must be the string ``"1.0"``.

``jobs``
    An ordered array of job objects.  Jobs are processed sequentially in
    array order.

Each job object has a ``type`` discriminator field (``"movie"`` or
``"show"``) which determines which remaining fields are required.

Minimal movie job::

    {
      "version": "1.0",
      "jobs": [
        {
          "type": "movie",
          "movie": {
            "name": "Rosencrantz and Guildenstern Are Dead",
            "year": 1990
          }
        }
      ]
    }

Minimal show job::

    {
      "version": "1.0",
      "jobs": [
        {
          "type": "show",
          "show": {
            "name": "The Wire",
            "season": 1,
            "start_episode": 1
          }
        }
      ]
    }

Annotated multi-job example
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: json

    {
      "version": "1.0",
      "jobs": [
        {
          "type": "movie",
          "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
          "movie": {
            "name": "Lawrence of Arabia",
            "year": 1962
          },
          "options": {
            "encode_format": "h265",
            "quality": 20,
            "eject_on_complete": true
          }
        },
        {
          "type": "show",
          "id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
          "show": {
            "name": "The Wire",
            "season": 1,
            "start_episode": 1
          },
          "options": {
            "rip_mode": "all",
            "min_length": 600
          }
        },
        {
          "type": "show",
          "show": {
            "name": "The Wire",
            "season": 1,
            "start_episode": 5
          }
        }
      ]
    }

Field reference
~~~~~~~~~~~~~~~

**Top-level**

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Required
     - Description
   * - ``version``
     - Yes
     - Schema version — must be ``"1.0"``.
   * - ``jobs``
     - Yes
     - Ordered array of job objects.

**Common job fields**

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Required
     - Description
   * - ``type``
     - Yes
     - ``"movie"`` or ``"show"``.
   * - ``id``
     - No
     - Client-assigned idempotency key (UUID v4 recommended).  Logged
       alongside job status messages for traceability.
   * - ``options``
     - No
     - Per-job option overrides (see below).  Omitted fields fall through
       to the ``queue run`` CLI flags or built-in defaults.

**Movie job — ``movie`` object**

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Required
     - Description
   * - ``name``
     - Yes
     - Movie title as it should appear in the Jellyfin library.
   * - ``year``
     - Yes
     - Release year (1888–2100); used in directory naming.

**Show job — ``show`` object**

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Required
     - Description
   * - ``name``
     - Yes
     - Series title as it should appear in the Jellyfin library.
   * - ``season``
     - Yes
     - Season number ≥ 0.  Season 0 maps to Jellyfin "Specials" (``Season 00/``).
   * - ``start_episode``
     - Yes
     - Episode number of the first title on this disc (≥ 1).  Subsequent
       titles are numbered consecutively from this value.

**Options object**

All fields are optional.  Omitted fields are resolved from the
``queue run`` CLI flags, then built-in defaults.

.. list-table::
   :header-rows: 1
   :widths: 22 55 23

   * - Field
     - Description
     - Default
   * - ``device``
     - Block device path (e.g. ``"/dev/sr0"``).
     - ``"/dev/sr0"``
   * - ``output_dir``
     - Absolute path to the output root directory.
     - ``"dvd_output"``
   * - ``temp_dir``
     - Temporary working directory; ``null`` uses system temp.
     - ``null``
   * - ``disc_number``
     - Disc index for a multi-disc title (1-based); ``null`` for single-disc.
     - ``null``
   * - ``rip_mode``
     - Title selection: ``"main"``, ``"all"``, or ``"ask"``.
     - ``"main"``
   * - ``encode_format``
     - Encoding: ``"h264"``, ``"h265"``, ``"none"``, or ``"ask"``.
     - ``"none"``
   * - ``quality``
     - HandBrake CRF value; ``null`` uses the format default.
     - ``null``
   * - ``min_length``
     - Minimum title length in seconds.
     - ``10``
   * - ``keep_original``
     - Retain raw MKV files after encoding.
     - ``false``
   * - ``eject_on_complete``
     - Eject the disc when the job finishes.
     - ``true``

.. warning::

   ``rip_mode: "ask"`` and ``encode_format: "ask"`` pause for interactive
   input.  The queue runner logs a warning when a job uses ask mode, as
   unattended operation will stall until an operator responds.

---

Option priority
---------------

Effective options for each job are resolved in this priority order (highest
first):

1. Values set in the job's ``options`` object.
2. Values supplied on the ``diskripr queue run`` command line.
3. Built-in diskripr defaults.

This lets you set a global default (e.g. ``--encode-format h265``) on the
command line and override it selectively per job.

---

JSON Schema
-----------

A machine-readable JSON Schema (Draft 2020-12) is available at
``docs/_static/diskripr-queue-schema.json`` in the source tree.  You can
reference it in your editor for inline validation and autocompletion::

    {
      "$schema": "path/to/diskripr-queue-schema.json",
      "version": "1.0",
      "jobs": [ ... ]
    }

---

Disc swap sequencing
--------------------

Between each pair of consecutive jobs the runner performs a disc swap
sequence so the next job starts automatically when a fresh disc is inserted.

With ``eject_on_complete: true`` (default):

1. The pipeline ejects the disc at the end of the job.
2. The runner waits up to 30 minutes for the drive to report empty
   (:func:`~diskripr.queue.wait_for_disc_removed`).
3. It then waits up to 30 minutes for a new disc to be inserted
   (:func:`~diskripr.queue.wait_for_disc_inserted`).
4. The next job starts automatically.

With ``eject_on_complete: false``:

1. The runner prints a prompt asking the operator to remove the old disc
   and insert the next one, then press Enter.
2. After Enter is pressed, polling begins for disc insertion.
3. The next job starts automatically once a disc is detected.

Both wait functions raise :exc:`TimeoutError` after 30 minutes (1800 seconds)
with no state change.  Disc presence is detected via
``udevadm info --query=property --name=<device>`` (``ID_CDROM_MEDIA=1``).

---

Validation errors
-----------------

``diskripr queue check --file PATH`` validates the file and prints one line
per job.  ``diskripr queue run`` also validates up front and exits non-zero
before touching any disc if errors are found.

Error messages follow this format::

    Error in jobs[N]: "field.path" <message>
    Error in file: "field" <message>      # for top-level envelope errors

Examples::

    Error in jobs[0]: "movie.year" Input should be less than or equal to 2100
    Error in jobs[1]: "show.start_episode" Input should be greater than or equal to 1
    Error in file: "version" Input should be '1.0'

---

API reference
-------------

See :doc:`/api/queue` for full function and class signatures.
