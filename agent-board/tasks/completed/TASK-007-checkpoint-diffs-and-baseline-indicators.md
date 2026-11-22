# TASK-007: Checkpoint diffs & baseline-change indicators

- **Status:** COMPLETED
- **Owner:** Codex-Integration
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `app/project_history.py`, `app/studio_server.py`, `web/studio/studio.js`, `web/studio/style.css`, `tests/test_project_history.py`, `tests/test_studio_server.py`

## Goal

Surface Git checkpoint diffs inside Project History and show subtle "modified from baseline" indicators in the visual editors so AI authors can always see what changed since stock Hard or between any two checkpoints.

## Scope & Implementation Notes

- `app/project_history.py` already initializes and manages a local Git repository per project (`ensure`, `checkpoint`, `list_checkpoints`, `restore`), while `web/studio/studio.js` (`review()`) only diffs *unsaved* in-browser edits against `file.original`.
- Add read-only diff helpers in `app/project_history.py` (validating commit hashes strictly as in `restore`, running read-only `git diff` / `git show` with size bounds, and returning changed files + unified diffs or structured baseline snapshots):
  - Diff a checkpoint against its parent commit.
  - Diff between any two selected checkpoints (or current saved files vs. initial baseline commit).
- Expose the diff API in `app/studio_server.py` (e.g., `POST /api/history/diff` or `GET /api/history/diff`).
- Update `projectHistory()` in `web/studio/studio.js` to view checkpoint diffs inline, and annotate modified files/values relative to the initial project baseline inside the visual editors (e.g., Production weights, Economy/Script settings) using `web/studio/style.css`.

## Acceptance Criteria

- [x] `app/project_history.py` safely computes diffs between commits or between a baseline commit and current saved files, rejecting invalid commit IDs or unmanaged repos.
- [x] `app/studio_server.py` exposes the read-only history diff endpoint with token auth and output size limits.
- [x] Project History dialog allows inspecting changed files and line diffs for any checkpoint and comparing against the initial baseline.
- [x] Visual editors indicate settings/files modified relative to the baseline (without obscuring current edits).
- [x] Unit tests in `tests/test_project_history.py` and `tests/test_studio_server.py` verify diff output, commit validation, and unmanaged-repo refusal.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify checkpoint diff rendering and baseline indicators in the Studio UI.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Chromium displayed the baseline label and saved-file diff. Git tests verified revisions, unmanaged repositories, the output cap, and byte-for-byte index preservation for read-only operations.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
