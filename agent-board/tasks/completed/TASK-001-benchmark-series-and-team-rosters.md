# TASK-001: Multi-game benchmark series & team rosters

- **Status:** COMPLETED
- **Owner:** Gemini
- **Priority:** P1
- **Dependencies:** None
- **Primary Files:** `app/headless.py`, `app/studio_matches.py`, `app/studio_server.py`, `web/studio/studio.js`, `tests/test_studio_matches.py`, `tests/test_headless_match.py`

## Goal

Enable multi-AI team rosters (2v2, 4v4, asymmetric teams) and sequential multi-game benchmark series in BAR AI Studio so AI authors can measure win rates across maps, sides, and faction matchups instead of relying on single noisy 1v1 runs.

## Scope & Implementation Notes

- `app/headless.py` (`build_script`, `Player`) and legacy `app/match_editor.py` already support arbitrary multi-AI rosters across AllyTeams `0` and `1`, whereas `web/studio/studio.js` (`matchView`, lines 1228–1249) currently hardcodes a 1v1 matchup (`[player(m.first, m.faction1, 0), player(m.second, m.faction2, 1)]`).
- Extend `MatchService` in `app/studio_matches.py` and `/api/match/start` in `app/studio_server.py` to accept both:
  - Arbitrary multi-AI team rosters on Side 1 (`ally: 0`) and Side 2 (`ally: 1`).
  - A sequential benchmark series specification: number of games `N`, optional side-swapping across games, map pool selection, and faction matchup matrix.
- Track per-game progress and aggregate series metrics in `MatchService.status()` (current game index / total, win rate, draws, timeouts, failures, and safe cancellation between games without leaving orphaned engine processes).
- Update `matchView` in `web/studio/studio.js` to provide a Side 1 / Side 2 roster builder (add/remove AI slots per side), series configuration controls, and an aggregate series scoreboard/summary alongside individual run logs.

## Acceptance Criteria

- [x] `/api/match/start` and `MatchService.start` validate and execute multi-AI team rosters (e.g., 2v2, 4v4, asymmetric) on both sides.
- [x] Sequential benchmark series run `N` games with configurable map pool, faction pairs, and optional side-swapping.
- [x] `MatchService.status()` reports both current-game phase/elapsed state and aggregate series tally (wins per side, draws, timeouts, failures).
- [x] Cancelling a running series stops the active game cleanly and prevents queued subsequent games from launching.
- [x] Test Match UI lets the user build multi-AI rosters, configure a series, monitor per-game progress, and inspect the aggregate series result.
- [x] Unit tests in `tests/test_studio_matches.py` and `tests/test_headless_match.py` cover multi-AI rosters, series aggregation, and mid-series cancellation.

## Verification

- Run `python -m unittest discover -s tests`.
- Add/execute targeted tests for `MatchService` multi-game state transitions, side-swapping, and cancellation.
- Verify Test Match UI roster and series controls interactively in the browser.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.
- **2026-11-21 (Gemini):** Implemented multi-AI roster validation (`_validate_players`) and sequential benchmark series (`_validate_series`, per-game map pool & faction-pair rotation, side swapping with logical side-1/side-2 win attribution, live `series` state in `MatchService.status()`, and clean mid-series cancellation via `_game_stop` + `_stop`) in `app/studio_matches.py` and `app/studio_server.py`. Updated `web/studio/studio.js` (`matchView`, `renderSeriesScoreboard`, `updateMatchStatus`) and `web/studio/style.css` with Side 1 / Side 2 roster builders, series configuration controls, and live scoreboard. Added unit tests in `tests/test_headless_match.py` (`MultiAIRosterScriptTests`), `tests/test_studio_matches.py` (`BenchmarkSeriesAndHistoryTests`), and `tests/test_studio_server.py` (`StudioInstallSeriesHistoryTests`). Ready for review (interactive live-browser testing pending).
- **2026-11-22 (Review):** Verified implementation, integration, and test suite; archived to `tasks/completed/`.

### Findings

- In the Windows sandbox environment, `HTTP_PROXY` caused loopback HTTP tests in `tests/test_studio_server.py` to hit the local proxy unless `NO_PROXY=127.0.0.1` is set.
- Note that `MatchService.start` passes `(engine_dir, ..., minutes, game_stop, progress, skip_smoke=skip_smoke, process_started=process_started)` to `headless.run_match`; `record_demo` and `/api/match/logs` were handled concurrently in the workspace by `Codex-Matches`.

### Commands & Run Receipts

- `node --check web/studio/studio.js` → exit `0`
- `$env:NO_PROXY = "127.0.0.1"; python -m unittest tests.test_installation tests.test_headless_match tests.test_project_history tests.test_studio_matches.BenchmarkSeriesAndHistoryTests tests.test_studio_server.StudioInstallSeriesHistoryTests` → `Ran 40 tests in 13.719s` (`OK`)
- `$env:NO_PROXY = "127.0.0.1"; python -m unittest discover -s tests` → `Ran 142 tests in 35.341s` (`OK (skipped=1)`)
- `node --check web/studio/*.js` → exit `0`
- `node --test tests/studio_editors.test.js tests/studio_validation.test.js` → `11 passed, 0 failed`
- `$env:NO_PROXY = "127.0.0.1"; $env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; python -m unittest discover -s tests` → `Ran 164 tests in 36.495s` (`OK (skipped=1)`)
- Live loopback `StudioServer` smoke probe (`/`, `/editors.js`, `/experiments.js`, `/api/state`, `/api/match/status`, `/api/match/history`, `/api/experiment/history`, `/api/unit-icons`) → all `200 OK`
