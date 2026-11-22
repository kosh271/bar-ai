# TASK-004: Optional replay recording & replay access

- **Status:** COMPLETED
- **Owner:** Codex-Matches
- **Priority:** P1
- **Dependencies:** None
- **Primary Files:** `app/headless.py`, `app/studio_matches.py`, `app/studio_server.py`, `web/studio/studio.js`, `tests/test_headless_match.py`, `tests/test_studio_matches.py`

## Goal

Allow users to opt in to recording `.sdfz` BAR replays during headless matches and easily access or copy the resulting replay file for visual inspection in BAR.

## Scope & Implementation Notes

- `app/headless.py` (`build_script`, line 157) currently hardcodes `RecordDemo=0;`.
- Add `record_demo: bool = False` to `build_script`, `run_match`, `_run_match` (`app/headless.py`), `MatchService.start` (`app/studio_matches.py`), and `/api/match/start` (`app/studio_server.py`). Keep `RecordDemo=0;` unconditionally for `_run_smoke` so smoke tests stay fast and clean.
- When `record_demo=True`, locate the generated `.sdfz` file(s) under the match run directory (`demos/`), record the relative/safe replay metadata in `result.json` and `logs`, and validate paths without allowing symlinks or escapes outside the match directory.
- In `web/studio/studio.js` (`matchView`), add a **Record replay** toggle (off by default) and post-match actions to view/copy the replay path or copy/export the `.sdfz` cleanly into BAR's `data/demos/` folder (with explicit user action and overwrite safety).

## Acceptance Criteria

- [x] `build_script(..., record_demo=...)` emits `RecordDemo=1;` only when requested and defaults to `RecordDemo=0;` (smoke tests never record demos).
- [x] `/api/match/start` and `MatchService.start` validate `record_demo` as a strict boolean and propagate it into `_run_match`.
- [x] Completed match `result.json` and `MatchService.status()` logs include discovered `.sdfz` replay file metadata when recorded.
- [x] Test Match UI provides a **Record replay** checkbox and displays replay location / copy-to-demos action when a replay was generated.
- [x] Unit tests in `tests/test_headless_match.py` and `tests/test_studio_matches.py` verify start script output, smoke exclusion, and replay discovery.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify `build_script` output with `record_demo=False` vs `record_demo=True` and mock `.sdfz` discovery in tests.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Native Recoil smoke and a 1,800-frame match generated a 13,751-byte .sdfz replay. Automated tests passed for opt-in, smoke exclusion, safe discovery, and overwrite-safe export.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
