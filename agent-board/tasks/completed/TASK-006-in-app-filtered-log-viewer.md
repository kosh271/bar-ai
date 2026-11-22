# TASK-006: In-app filtered log viewer & diagnostic triage

- **Status:** COMPLETED
- **Owner:** Codex-Matches
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `app/headless.py`, `app/studio_matches.py`, `app/studio_server.py`, `web/studio/studio.js`, `tests/test_studio_server.py`, `tests/test_headless_match.py`

## Goal

Provide an in-app, searchable, categorized log viewer in BAR AI Studio that separates real AI / AngelScript errors and events from noisy BAR engine shader, weapondef, and yardmap warnings.

## Scope & Implementation Notes

- Currently `web/studio/studio.js` only shows a short summary (`m.snapshot.result.summary`) and the raw filesystem path (`Logs: ...`), while `app/headless.py` (`_diagnostics`, `_diagnostic_preview`) mixes engine warnings with AI diagnostics and truncates previews to 3–10 lines.
- Add a categorization helper in `app/headless.py` (or `app/studio_matches.py`) classifying diagnostics into clear buckets such as `ai_script` (AngelScript compilation/runtime errors, `Skirmish AI` loads/crashes), `engine_error`, and `engine_warning` / `noise`.
- Add a safe, size-bounded read-only endpoint in `app/studio_server.py` (e.g., `GET /api/match/logs`) allowing the UI to inspect `result.json`, `infolog.txt`, and `stdout.txt` for a run ID/directory strictly validated inside `data/studio-matches/matches/` (rejecting symlinks, junctions, and path traversal, and bounding response bytes).
- Build a modal or expandable log inspector in `web/studio/studio.js` with category filter pills, text search, line numbers/timestamps, and a copy button.

## Acceptance Criteria

- [x] Diagnostics are classified into `AI / AngelScript`, `Engine Errors`, and `Engine Warnings` without altering raw `infolog.txt` / `stdout.txt` preservation.
- [x] `GET /api/match/logs` safely serves bounded slices/pages of `infolog.txt`, `stdout.txt`, and `result.json` strictly from `data/studio-matches/matches/`, rejecting traversal or symlinked files.
- [x] Studio UI provides an in-app log viewer accessible from both smoke and match results (and match history), with category toggles and keyword filtering.
- [x] Unit tests in `tests/test_studio_server.py` and `tests/test_headless_match.py` verify path-safety guards, byte limits, and diagnostic categorization.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify log viewer filtering, search, and security boundary rejections via automated tests.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Chromium exercised the history log button and AI / AngelScript filter against the real Studio API. Automated tests cover bounded paging, source selection, path guards, and classification.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
