Batch Ripping (``diskripr queue``)
===================================

``diskripr queue`` lets you rip a stack of discs unattended by writing a
single JSON job file that describes every disc in order.  The runner
validates the entire file before touching any hardware, processes jobs
sequentially, and handles disc swap sequencing automatically.

.. contents:: Contents
   :local:
   :depth: 2

---

End-to-end walkthrough
-----------------------

This example rips two movie discs and two discs of a TV season.

Step 1 — Scan each disc to confirm titles
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Scan each disc before writing the job file to confirm diskripr sees the
right titles::

    # Disc 1
    diskripr movie scan --name "Lawrence of Arabia" --year 1962

    # Disc 2
    diskripr movie scan --name "Lawrence of Arabia" --year 1962

    # TV disc 1
    diskripr show scan --show "The Wire" --season 1

    # TV disc 2
    diskripr show scan --show "The Wire" --season 1

Take note of any titles you want to exclude and adjust ``min_length`` or
``rip_mode`` per job accordingly.

Step 2 — Write the job file
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create ``jobs.json``::

    {
      "version": "1.0",
      "jobs": [
        {
          "type": "movie",
          "id": "movie-lawrence-disc1",
          "movie": { "name": "Lawrence of Arabia", "year": 1962 },
          "options": {
            "disc_number": 1,
            "encode_format": "h265",
            "eject_on_complete": true
          }
        },
        {
          "type": "movie",
          "id": "movie-lawrence-disc2",
          "movie": { "name": "Lawrence of Arabia", "year": 1962 },
          "options": {
            "disc_number": 2,
            "encode_format": "h265",
            "eject_on_complete": true
          }
        },
        {
          "type": "show",
          "id": "wire-s01-disc1",
          "show": {
            "name": "The Wire",
            "season": 1,
            "start_episode": 1
          },
          "options": {
            "rip_mode": "all",
            "min_length": 600,
            "eject_on_complete": true
          }
        },
        {
          "type": "show",
          "id": "wire-s01-disc2",
          "show": {
            "name": "The Wire",
            "season": 1,
            "start_episode": 5
          },
          "options": {
            "rip_mode": "all",
            "min_length": 600,
            "eject_on_complete": true
          }
        }
      ]
    }

Step 3 — Validate the file
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``diskripr queue check`` validates the file and prints a one-line summary
per job without touching any disc::

    diskripr queue check --file jobs.json

Expected output::

    jobs[0]: OK — movie 'Lawrence of Arabia' (1962)
    jobs[1]: OK — movie 'Lawrence of Arabia' (1962)
    jobs[2]: OK — show 'The Wire' S01 ep1
    jobs[3]: OK — show 'The Wire' S01 ep5

If there are errors they are printed in the format
``Error in jobs[N]: "field" message`` and the exit code is non-zero.

Step 4 — Run the queue
~~~~~~~~~~~~~~~~~~~~~~~

Load the first disc, then start the queue::

    diskripr queue run --file jobs.json --output-dir /mnt/storage/dvd_output

The runner processes each job in order.  After each job completes it:

1. Waits for the drive to go empty (the pipeline ejects automatically when
   ``eject_on_complete: true``).
2. Logs a message and waits for the next disc to be inserted.
3. Starts the next job automatically.

You only need to insert discs as prompted.

---

Overriding options globally
----------------------------

Options passed to ``queue run`` act as defaults for all jobs.  Per-job
``options`` take priority::

    # Set h265 encoding globally; disc 2 overrides to h264
    diskripr queue run --file jobs.json --encode-format h265

This is equivalent to adding ``"encode_format": "h265"`` to every job that
does not already specify one.

---

Handling manual disc removal
-----------------------------

If your drive does not auto-eject or you prefer to remove discs manually,
set ``eject_on_complete: false`` in each job's options::

    "options": { "eject_on_complete": false }

The runner will print::

    Job 1 complete. Please remove the disc and insert the next one. Press Enter to begin polling...

Press Enter after inserting the next disc to begin polling.

---

Saving scan results to a file
------------------------------

``diskripr movie scan`` and ``diskripr show scan`` support ``--output-json``
to write a job-file skeleton you can edit before running::

    diskripr movie scan \
        --name "Lawrence of Arabia" \
        --year 1962 \
        --output-json jobs.json

    # append a second job to the same file
    diskripr show scan \
        --show "The Wire" \
        --season 1 \
        --output-json jobs.json \
        --append

Edit the resulting file to set per-job options, then validate and run.

---

Further reading
---------------

* :doc:`/concepts/queue` — full job file format reference and disc swap details.
* :doc:`/api/queue` — ``QueueRunner``, ``validate_job_file``, and polling API.
