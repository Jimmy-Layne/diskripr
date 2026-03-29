"""Click-based command-line interface for diskripr.

Defines the top-level ``diskripr`` command group and three subcommands:

- ``rip``      — Full pipeline: discover → rip → encode → organize.
- ``scan``     — Discover stage only; inspects a disc without ripping.
- ``organize`` — Organize stage only; re-sorts already-ripped files from the
                 temp directory (reads location from ``DISKRIPR_TEMP_DIR`` or
                 ``<output_dir>/.tmp``).

This is the only module that writes to stdout or prompts the user. It builds a
``Config`` from Click parameters, wires up a progress callback backed by
Click's progress bar, and delegates all work to ``pipeline``.

Typical multi-disc workflow::

    $ diskripr rip -n "Lawrence of Arabia" -y 1962 --disc 1
    # swap disc
    $ diskripr rip -n "Lawrence of Arabia" -y 1962 --disc 2
"""
import click  # noqa: F401  # pylint: disable=unused-import


def main() -> None:
    """Entry point placeholder — full Click group defined during implementation."""
    raise NotImplementedError("CLI not yet implemented")
