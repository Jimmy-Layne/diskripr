# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.0] - Unreleased

### Breaking Changes

- **CLI restructured**: the top-level `diskripr rip`, `diskripr scan`, and
  `diskripr organize` commands have been removed and replaced by two explicit
  subgroups:
  - `diskripr movie rip / scan / organize` — movie pipeline
  - `diskripr show rip / scan / organize` — TV season pipeline

  Migration: replace `diskripr rip` with `diskripr movie rip`, and add
  `--show`, `--season`, and `--start-episode` flags for TV discs.

### Added

- **TV season support** via `diskripr show` command group. Episodes are
  clustered by duration and VTS, numbered from `--start-episode`, and
  organized into the Jellyfin `Shows/<Name>/Season NN/` layout.
- **Extras classification heuristics** (`util/heuristics.py`): a 13-rule
  signal chain classifies each extra as trailer, interview, deleted scene,
  behind-the-scenes, featurette, or generic extra. Signals are drawn from
  title name keywords, duration, chapter count, VTS structure, segment map,
  PGC count, and per-cell durations.
- **Jellyfin type-subdirectory layout** (`util/jellyfin_filesystem.py`):
  extras are routed into the eight Jellyfin-specified subdirectories
  (`trailers/`, `deleted scenes/`, `behind the scenes/`, `interviews/`,
  `featurettes/`, `scenes/`, `shorts/`, `extras/`). Descriptive title names
  are used as display stems; generic MakeMKV names fall back to a counter.
- **Batch queue** (`diskripr queue run / check`): JSON job files describe
  ordered disc rip jobs. The runner validates up front, processes
  sequentially, and polls the drive for disc swaps automatically.
- **IFO driver** (`drivers/ifo.py`) using `pyparsedvd` to extract PGC counts
  and per-cell durations from `VIDEO_TS` IFO files.
- **Config split**: `Config` replaced by `BaseConfig`, `MovieConfig`, and
  `ShowConfig` with type-specific validation.
- **Pipeline split**: `Pipeline` replaced by `BasePipeline`, `MoviePipeline`,
  and `ShowPipeline`.
- **Schema module** (`schema.py`): Pydantic models and JSON Schema export
  (Draft 2020-12) for the queue job file format.
- **New concept docs**: `jellyfin_naming`, `heuristics`, and `queue` pages.
- **New guide**: `batch-ripping` end-to-end walkthrough.

## [0.1.3] - 2026-04-02
## Fixed
- Increased logging for ripping process
- Incorrect interpretation of makemv messages as error
- typo in jellyfin resolution.


## [0.1.0] - 2026-03-29

### Added

- Four-stage ripping pipeline: Discover, Rip, Encode, Organize.
- MakeMKV driver for drive scanning, title enumeration, and MKV extraction.
- HandBrake driver for optional H.264/H.265 re-encoding with configurable RF quality.
- FFprobe driver for post-organize stream inspection (video, audio, subtitle tracks).
- lsdvd driver for quick disc pre-check (non-fatal on encrypted discs).
- Jellyfin-compatible output directory structure with extras naming conventions.
- Multi-disc movie support via `--disc N` (produces `- PartN` filename suffix).
- Interactive CLI (`diskripr rip`) with title selection, extras classification, and encoder prompts.
- `diskripr scan` subcommand for disc inspection without ripping.
- `diskripr organize` subcommand for re-sorting already-ripped temp files.
- Structured progress callbacks decoupling pipeline from presentation.
- Comprehensive test suite (232 tests) with captured tool-output fixtures.
