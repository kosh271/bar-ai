# TASK-008: Side-by-side profile comparison

- **Status:** COMPLETED
- **Owner:** Codex-Editors
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `web/studio/config.js`, `web/studio/studio.js`, `web/studio/style.css`, `app/studio_server.py`, `tests/test_studio_server.py`

## Goal

Let users compare any two AI profiles (within the same project or across two projects) side-by-side with a structured diff of JSON configuration values, factory weights, openers, and script files.

## Scope & Implementation Notes

- Currently `app/studio_server.py` serves one profile at a time via `GET /api/profile?project=...&profile=...`, and `web/studio/studio.js` has no cross-profile diff view.
- Reuse `GET /api/profile` (or add a lightweight comparison fetch) in `web/studio/studio.js` so users can pick a comparison target `(project, profile)` without discarding unsaved edits in the currently open profile (comparing either saved state or including current unsaved edits).
- Reuse `StudioConfig.parse` (`web/studio/config.js`) to compute structural JSONC diffs:
  - Added, removed, and changed files across the two profiles.
  - Changed JSON paths and values (ignoring comment-only and formatting-only differences by default, with an option to inspect raw text).
  - Friendly unit names and unit icons next to changed unit/factory keys where applicable.
  - Clear line-by-line diff for `.as` AngelScript files (including `commander.as` openers).
- Style the comparison view cleanly in `web/studio/style.css` (accessible from Overview or top actions/dialog).

## Acceptance Criteria

- [x] Users can select any other profile (in the same project or another Studio project) to compare against the active profile.
- [x] Comparison view lists added, removed, and modified files and highlights structural JSONC key/value differences independently of comments/whitespace.
- [x] Unit IDs and factory keys in the comparison view display friendly unit names and icons when available.
- [x] AngelScript `.as` differences display readable unified/side-by-side hunks.
- [x] Existing profile-read endpoint tests and any new comparison helpers pass without regressions.

## Verification

- Run `python -m unittest discover -s tests`.
- Manually compare two modified profiles (both JSON config edits and `.as` script edits) in the Studio UI.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Chromium compared profiles in the same and different projects, including unsaved edits and AngelScript hunks. Node tests verified semantic JSONC and line diffs.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
