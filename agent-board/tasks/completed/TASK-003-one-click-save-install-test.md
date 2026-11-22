# TASK-003: One-click Save, Install & Test + stale-install badge

- **Status:** COMPLETED
- **Owner:** Gemini
- **Priority:** P1
- **Dependencies:** None
- **Primary Files:** `app/installation.py`, `app/studio_server.py`, `web/studio/studio.js`, `web/studio/index.html`, `tests/test_installation.py`, `tests/test_studio_server.py`

## Goal

Eliminate the multi-page ceremony (`Save` → `AI install / remove` → `Install` → `Test match` → `Start match`) on every edit by surfacing install synchronization status and providing one-click Save & Install / Save, Install & Test flows.

## Scope & Implementation Notes

- Add an install-status inspection helper in `app/installation.py` (comparing the project's saved files, `project.json` metadata, and profiles against the target directory's `.bar-ai-editor.json` manifest `MANIFEST` and lobby entries) and expose it via `studio_server.py` (e.g., included in `/api/state` or a dedicated `/api/install-status` endpoint when `settings` are configured).
- Display a clear status badge in `web/studio/index.html` / `web/studio/studio.js` indicating whether the active project is **Up to date**, **Out of date (saved changes not installed)**, **Modified externally**, or **Not installed** for the selected engine.
- Add **Save & Install** (and **Save, Install & Test** from the workshop/Test Match flow) in `web/studio/studio.js` that chains `/api/save` → `/api/install` (preserving all existing confirmation prompts when modifying unmanaged/lobby files) and updates cached/scanned profile entries for the installed AI without requiring a slow full unitsync rescan when only an existing installed AI's profiles or files changed.

## Acceptance Criteria

- [x] Backend reports accurate install sync status (`installed`, `up_to_date`, `out_of_date`, `modified_externally`, or `not_configured`) using `app/installation.py` without mutating files.
- [x] UI shows a live install-status indicator reflecting unsaved edits, saved-but-uninstalled changes, and current install status.
- [x] One-click **Save & Install** saves dirty files (with validation), installs to the configured engine (honoring all existing safety confirmations), and updates status immediately.
- [x] Updating an already-scanned Studio AI updates available profiles in the Test Match state without forcing a multi-minute unitsync rescan unless a new AI short name/version was added or the user requests a refresh.
- [x] Unit tests in `tests/test_installation.py` and `tests/test_studio_server.py` cover install-status detection and confirmation handling.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify stale-install badge transitions across Edit → Save → Install → External modification states.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.
- **2026-11-21 (Gemini):** Implemented read-only `inspect_status` / `_expected_hashes` in `app/installation.py`, `MatchService.sync_installed_ai` in `app/studio_matches.py`, `GET /api/install-status` and `cache_updated` handling in `POST /api/install` in `app/studio_server.py`, and the topbar `#install-status` badge, `#save-install` button, and `Save, install & test` actions in `web/studio/index.html`, `web/studio/style.css`, and `web/studio/studio.js`. Added unit tests in `tests/test_installation.py` and `tests/test_studio_server.py`. Ready for review (interactive browser check pending).
- **2026-11-22 (Review):** Verified implementation, integration, and test suite; archived to `tasks/completed/`.

### Findings

- `_expected_hashes` normalizes `\n` to `os.linesep` for generated `AIInfo.lua` and `AIOptions.lua` to match `Path.write_text` line endings on Windows without mutating files.
- In `web/studio/studio.js`, `saveAndInstall()` respects `Codex-Validation`'s `save()` validation modal by checking `dirty().length` after `await save()` and aborting if validation blocked saving or requires warning confirmation.

### Commands & Run Receipts

- `node --check web/studio/studio.js` → exit `0`
- `$env:NO_PROXY = "127.0.0.1"; python -m unittest tests.test_installation tests.test_headless_match tests.test_project_history tests.test_studio_matches.BenchmarkSeriesAndHistoryTests tests.test_studio_server.StudioInstallSeriesHistoryTests` → `Ran 40 tests in 13.719s` (`OK`)
- `$env:NO_PROXY = "127.0.0.1"; python -m unittest discover -s tests` → `Ran 142 tests in 35.341s` (`OK (skipped=1)`)
- `node --check web/studio/*.js` → exit `0`
- `node --test tests/studio_editors.test.js tests/studio_validation.test.js` → `11 passed, 0 failed`
- `$env:NO_PROXY = "127.0.0.1"; $env:HTTP_PROXY = ""; $env:HTTPS_PROXY = ""; python -m unittest discover -s tests` → `Ran 164 tests in 36.495s` (`OK (skipped=1)`)
- Live loopback `StudioServer` smoke probe (`/`, `/editors.js`, `/experiments.js`, `/api/state`, `/api/match/status`, `/api/match/history`, `/api/experiment/history`, `/api/unit-icons`) → all `200 OK`
