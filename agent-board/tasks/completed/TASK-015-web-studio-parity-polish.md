# TASK-015: Map dimensions & persistent workspace/match state

- **Status:** COMPLETED
- **Owner:** Codex-Integration
- **Priority:** P3
- **Dependencies:** None
- **Primary Files:** `app/settings.py`, `app/studio_server.py`, `web/studio/studio.js`, `tests/test_studio_server.py`, `tests/test_foundation.py`

## Goal

Restore two helpful quality-of-life features from the legacy Tkinter GUI into the web Studio: showing map dimensions in the map picker and remembering the user's last selected project, profile, and Test Match configuration across restarts.

## Scope & Implementation Notes

- **Map dimensions:** `app/content_scan.py` (lines 66–79) already extracts each map's `width` and `height` (in 512-elmo map squares), and the legacy GUI displayed them alongside map names, whereas `web/studio/studio.js` (line 1226) currently displays only `map.name`. Format map dropdown labels as `<name> (<W>x<H>)` (or note unknown dimensions when `0`).
- **Workspace & match state persistence:** The legacy editor persisted `data/editor-state.json` (last AI, profile, file) and `data/match-state.json` (last map). Extend `app/settings.py` and `app/studio_server.py` (or `/api/state` + lightweight preference updates) to persist and validate the last-selected project, profile, and Test Match settings (`game`, `map`, matchup, `minutes`, `skip_smoke`) safely, restoring them in `web/studio/studio.js` on startup when still available (and falling back cleanly if deleted/missing).

## Acceptance Criteria

- [x] Test Match map dropdown displays map dimensions (`width × height`) when non-zero and indicates unknown dimensions cleanly when missing.
- [x] Last selected project, profile, and Test Match setup (game, map, AI choices, factions, time limit, skip-smoke toggle) persist to `data/` via validated settings helpers.
- [x] Reopening BAR AI Studio restores the saved selections automatically, or falls back gracefully with no error if a previously selected project/map/AI is no longer installed.
- [x] Unit tests in `tests/test_studio_server.py` and `tests/test_foundation.py` cover state serialization, validation, and missing-target fallback.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify map dimensions in Test Match and verify selections survive full server restart + browser reload.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Chromium verified map dimensions, unknown sizes, and workspace and roster restoration across a Studio server restart. Automated tests covered preference validation and stale selections.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
