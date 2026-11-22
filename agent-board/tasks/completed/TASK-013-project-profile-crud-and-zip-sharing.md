# TASK-013: Project/profile CRUD & `.zip` import/export

- **Status:** COMPLETED
- **Owner:** Codex-Integration
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `app/projects.py`, `app/studio_server.py`, `web/studio/studio.js`, `tests/test_projects.py`, `tests/test_studio_server.py`

## Goal

Expose full project and profile lifecycle management (create profile from Hard, rename, set default profile, safe delete) plus `.zip` project export/import so users can organize, back up, and share custom BAR AIs easily.

## Scope & Implementation Notes

- `app/projects.py` already implements `create_profile_from_hard` (line 198), `delete_profile` (line 243), and `delete_project` (line 259), none of which are wired into `app/studio_server.py` or `web/studio/studio.js`.
- Add backend helpers and endpoints with strict path/link/confirmation safety:
  - Wire `create_profile_from_hard`, `delete_profile` (refusing deletion of the last profile or updating `default_profile` safely), and `delete_project`.
  - Add rename project / rename profile and set `default_profile` in `project.json` (synchronizing `AIOptions.lua` defaults on next install).
  - Add `.zip` export (streaming project files excluding `.git` metadata and symlinks) and `.zip` import (validating archive entries against zip-slip/path traversal, size/file-count limits, valid `project.json` and `profiles/`, unique project name, and initializing fresh Studio Git history via `project_history.ensure`).
- Add clean management controls in `web/studio/studio.js` (Manage / Overview / sidebar dialogs).

## Acceptance Criteria

- [x] Users can create a new profile from BAR Hard, rename projects/profiles, change the project's default profile, and delete profiles/projects with confirmation and safety guards.
- [x] Deleting the active default profile either updates `default_profile` cleanly or prevents leaving an invalid project; deleting the sole remaining profile is refused.
- [x] Export `.zip` packages the project cleanly without `.git` history or temporary staging files.
- [x] Import `.zip` validates paths, entry sizes, links, and metadata (preventing zip-slip or overwriting existing projects) and initializes fresh local Git history.
- [x] Unit tests in `tests/test_projects.py` and `tests/test_studio_server.py` cover CRUD operations, edge-case guards, and `.zip` round-trip/security validation.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify export → delete → import round-trip and confirm imported AI installs and passes a smoke check.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- ZIP export, deletion, import, and fresh Git baseline passed the API round trip. The imported profile installed into a private Recoil copy and passed native smoke. Rename and default selection also passed a reinstall regression test.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
