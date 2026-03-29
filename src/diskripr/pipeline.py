"""Pipeline orchestrator for the diskripr workflow.

Exposes ``run(config, on_progress)`` as the primary entry point, which chains
the following stages in order:

1. Discover  — detect drive, scan titles via MakeMKV and lsdvd.
2. Select    — choose titles based on ``config.rip_mode`` (internal logic).
3. Classify  — assign Jellyfin extra types to non-main titles (internal logic).
4. Rip       — extract selected titles to the temp directory.
5. Encode    — re-encode with HandBrake (skipped when ``encode_format="none"``
               or HandBrakeCLI is absent).
6. Organize  — move files into the Jellyfin directory tree.
7. Inspect   — report stream details via ffprobe (skipped when unavailable).

Each stage is also exposed as a standalone callable so CLI subcommands (``scan``,
``organize``) can invoke individual stages without running the full pipeline.

Title selection and extras classification are implemented here as internal
functions because they are decision logic — they do not invoke external tools
or perform I/O.

Error propagation: if a title fails to rip it is excluded from subsequent
stages with a warning rather than a crash. Stage-level exceptions surface as
typed errors (``RipError``, ``EncodeError``) so the caller can handle them
distinctly.
"""
