# Changelog

All notable Ballad releases are recorded here. The early release numbers are
retrospective milestone labels assigned to the existing Git history.

## [1.4.2] — 2026-08-08

**Current release candidate** built on the static-quality milestone
([660a2f1](https://github.com/Izento/ballad-a-song-organizer/commit/660a2f1)).

- Cleared the repository-wide Pyright diagnostics.
- Cleared Ruff lint and formatting diagnostics.
- Added focused typing contracts for GUI mixins and domain model coercion.
- Preserved the full passing test suite while tightening optional-value handling.
- Restored game-first naming for OC ReMix files using `[OC ReMix]` suffixes.

## [1.4.1] — 2026-08-04

**Post-refactor GUI and workflow polish** ([3d61c36](https://github.com/Izento/ballad-a-song-organizer/commit/3d61c36)).

- Added dark-mode theme support.
- Improved review-state reset and proposal coordination.
- Updated the user-facing documentation and GUI behavior after the refactor.

## [1.4.0] — 2026-08-03

**Codebase refactor and packaging cleanup** ([c837f8c](https://github.com/Izento/ballad-a-song-organizer/commit/c837f8c)).

- Split the GUI and core workflows into focused modules.
- Removed verified dead code and clarified module ownership.
- Added structural checks and finalized the one-folder release layout.

## [1.3.0] — 2026-08-03

**Safer review workflow and release packaging** ([66f3fd3](https://github.com/Izento/ballad-a-song-organizer/commit/66f3fd3)).

- Hardened review, apply, verification, and recovery behavior.
- Made artwork proposals explicit and safer to review.
- Added the reproducible public and private PyInstaller package flow.

## [1.2.0] — 2026-08-02

**Metadata enrichment and duplicate-safety expansion** ([0bbee2b](https://github.com/Izento/ballad-a-song-organizer/commit/0bbee2b)).

- Added verified MusicBrainz metadata enrichment and cover-art handling.
- Expanded media adapters and canonical metadata models.
- Strengthened duplicate evidence and transaction-safe tag updates.

## [1.1.0] — 2026-07-19

**Review-first GUI and apply workflow upgrade** ([9f73974](https://github.com/Izento/ballad-a-song-organizer/commit/9f73974)).

- Coordinated review proposals with explicit apply behavior.
- Improved GUI selection and review/apply state handling.
- Added broader extraction and AcoustID coverage.

## [1.0.0] — 2026-07-15

**Initial public release** ([8019616](https://github.com/Izento/ballad-a-song-organizer/commit/8019616)).

- Released the first Ballad CLI and desktop organizer.
- Added filename repair, tag auditing, duplicate analysis, review plans,
  guarded apply, and undo support.
