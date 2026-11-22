# TASK-002: Persistent match history tied to checkpoints

- **Status:** COMPLETED
- **Owner:** Gemini
- **Priority:** P1
- **Dependencies:** None
- **Primary Files:** `app/studio_matches.py`, `app/studio_server.py`, `app/project_history.py`, `web/studio/studio.js`, `tests/test_studio_matches.py`, `tests/test_studio_server.py`

## Goal

Persist headless match and benchmark outcomes in a queryable history tied to each Studio project's Git checkpoints and install state, so results survive page reloads and authors can see whether edits improved performance across versions.

## Scope & Implementation Notes

- Headless runs already store `result.json` and logs under `data/studio-matches/matches/`, but `web/studio/studio.js` currently loses match visibility when Studio restarts or another operation runs.
- Record structured metadata per completed/failed/cancelled match (and series) in an index or enriched `result.json`: timestamp, engine, game, map, players (AI short name, version, profile, faction, ally), outcome, wall-clock duration, and—for any participating Studio-managed AI project—its Git `HEAD` checkpoint commit ID from `app/project_history.py` plus whether uncheckpointed changes or installed-file drift existed when the run started.
- Expose a read-only API endpoint (e.g., `GET /api/match/history` with optional project/profile filtering and safe bounds) in `app/studio_server.py` backed by `MatchService` in `app/studio_matches.py`.
- Render a persistent match history table with filters (by project, profile, map, or checkpoint) on the Test Match page in `web/studio/studio.js`.

## Acceptance Criteria

- [x] Completed, failed, and cancelled runs persist structured history metadata (including participating project checkpoint hash and drift status where applicable).
- [x] Existing runs or newly indexed runs under `data/studio-matches/matches/` are safely readable via `GET /api/match/history` without exposing paths outside `data/studio-matches/`.
- [x] Reloading Studio or restarting the local server preserves the match history table on the Test Match page.
- [x] History entries can be filtered by project/profile/map and clearly distinguish clean checkpoint runs from dirty/out-of-sync runs.
- [x] Unit tests in `tests/test_studio_matches.py` and `tests/test_studio_server.py` cover history persistence, checkpoint linkage, and API filtering/validation.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify history API responses and UI table persistence across simulated runs and server restarts.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.
- **2026-11-21 (Gemini):** Added read-only `status_summary` in `app/project_history.py`, project checkpoint/drift snapshotting (`_capture_project_snapshots`), atomic `history.json` persistence, `result.json` metadata enrichment, safe legacy `result.json` + `script.txt` discovery (`_legacy_entry_from_dir`), relative `run_dir` validation (`_relative_run_dir`), and `MatchService.history` filtering in `app/studio_matches.py`. Exposed `GET /api/match/history` in `app/studio_server.py`, rendered the filterable Match History table (`loadMatchHistory`, `renderMatchHistory`) in `web/studio/studio.js`, and added automated tests in `tests/test_studio_matches.py` and `tests/test_studio_server.py`. Ready for review (interactive browser check pending).
- **2026-11-22 (Review):** Verified implementation, integration, and test suite; archived to `tasks/completed/`.

### Findings

- `run_dir` is strictly restricted to relative directories inside `data/studio-matches/matches/` checked against symlinks/reparse points; external paths (e.g., from mocks or outside folders) are omitted (`None`) rather than exposed.
- Legacy unindexed `match-*` / `smoke-*` directories under `data/studio-matches/matches/` are discovered alongside `history.json` entries and enriched with `script.txt` metadata when present.

### Commands & Run Receipts

- `node --check web/studio/studio.js` → exit `0`
- `$env:NO_PROXY = "127.0.0.1"; python -m unittest tests.test_installation tests.test_headless_match tests.test_project_history tests.test_studio_matches.BenchmarkSeriesAndHistoryTests tests.test_studio_server.StudioInstallSeriesHistoryTests` → `Ran 40 tests in 13.719s` (`OK`)
- `$env:NO_PROXY = "127.0.0.1"; python -m unittest discover -s tests` → `Ran 142 tests in 35.341s` (`OK (skipped=1)`)
- `node --check web/studio/*.js` → exit `0`
- `node --test tests/studio_editors.test.js tests/studio_validation.test.js` → `11 passed, 0 failed`
- `$env:NO_PROXY = "127.0.0.1"; $env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; python -m unittest discover -s tests` → `Ran 164 tests in 36.495s` (`OK (skipped=1)`)
- Live loopback `StudioServer` smoke probe (`/`, `/editors.js`, `/experiments.js`, `/api/state`, `/api/match/status`, `/api/match/history`, `/api/experiment/history`, `/api/unit-icons`) → all `200 OK`
