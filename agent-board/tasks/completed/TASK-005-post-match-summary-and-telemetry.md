# TASK-005: Post-match summary & telemetry

- **Status:** COMPLETED
- **Owner:** Codex-Matches
- **Priority:** P1
- **Dependencies:** None
- **Primary Files:** `app/headless.py`, `app/studio_matches.py`, `web/studio/studio.js`, `tests/test_headless_match.py`, `tests/test_studio_matches.py`

## Goal

Surface meaningful in-game telemetry after each headless match (in-game duration, loaded AI profile/factory/opener events, resource/unit stats, and AI errors) so authors can understand *why* an AI won or lost without manually digging through raw `infolog.txt` files.

## Scope & Implementation Notes

- Currently `app/headless.py` (`_run_match`) only returns winner/timeout status, real-time elapsed seconds, and raw error/warning lines.
- Parse match artifacts after `_run_match` finishes:
  - In-game end frame and simulated match duration (`MM:SS` / `HH:MM:SS` at 30 fps) from `infolog.txt` / engine frames.
  - Per-AI BARbarIAn log events from `infolog.txt` (loaded script/profile path, selected factory, opener queue chosen, factory switches, AngelScript runtime errors/warnings).
  - End-of-game or periodic economy/army summary if emitted in `infolog.txt` or `LuaUI/Config/teamstats.json` (handling missing or partial data gracefully across BAR versions).
- Persist a structured `telemetry` summary in `result.json` and return it via `MatchService.status()` in `app/studio_matches.py`.
- Render a concise post-match telemetry card in `web/studio/studio.js` (`matchView`).

## Acceptance Criteria

- [x] `result.json` includes structured telemetry (in-game frames/duration, per-team AI load/opener/factory summary, AI script error count, and available end-of-game stats).
- [x] Parser handles missing, truncated, or variant log formats gracefully without failing the match result.
- [x] `web/studio/studio.js` renders a clear post-match telemetry breakdown alongside the match outcome summary.
- [x] Unit tests in `tests/test_headless_match.py` and `tests/test_studio_matches.py` verify telemetry extraction from realistic sample `infolog.txt` and `teamstats.json` fixtures.

## Verification

- Run `python -m unittest discover -s tests`.
- Validate telemetry parser against sample logs (both normal completion, early defeat, and timeout cases).

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Native Recoil reported 01:00 and both loaded Hard profiles. Fixture tests cover factory and opener events, missing or partial stats, and failed-smoke script errors.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
