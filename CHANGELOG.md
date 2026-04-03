# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
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
