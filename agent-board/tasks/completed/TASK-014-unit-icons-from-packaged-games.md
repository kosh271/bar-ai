# TASK-014: Unit icons from packaged BAR `.sdp` games

- **Status:** COMPLETED
- **Owner:** Codex-Icons
- **Priority:** P3
- **Dependencies:** None
- **Primary Files:** `app/unit_icons.py`, `app/game_sources.py`, `app/studio_server.py`, `web/studio/studio.js`, `tests/test_unit_icons.py`

## Goal

Load unit icons directly from packaged BAR `.sdp` archives and `data/pool/` so standard BAR launcher installations display unit icons without requiring an unpacked `data/games/BAR.sdd/unitpics` folder.

## Scope & Implementation Notes

- `app/unit_icons.py` (`icon_directory`, lines 26–37) currently checks only `<bar_root>/data/games/BAR.sdd/unitpics`, so standard BAR installations using rapid `.sdp` packages get `available: False`.
- Reuse the `.sdp` manifest and `data/pool/` lookup pattern already established in `app/game_sources.py` (`list_game_sources`, `_package_entries`) to locate `unitpics/<unit_id>.dds` inside a selected/active packaged game (preferring the catalog's scanned game if installed, or the latest rapid package, while keeping unpacked `BAR.sdd/unitpics` supported when present).
- Cache parsed `.sdp` `unitpics` index maps in memory per `(bar_root, digest)` to avoid re-reading `.sdp` manifests on every `/api/unit-icon/<id>` request, while preserving existing path/link, `MAX_ICON_BYTES`, `MAX_ICON_PIXELS`, MD5 pool integrity, and Pillow DDS→PNG conversion guards.
- Update `/api/unit-icons` status messaging in `app/unit_icons.py` / `web/studio/studio.js` accordingly.

## Acceptance Criteria

- [x] `unit_icons.py` resolves `unitpics/<id>.dds` from unpacked `BAR.sdd` when present and falls back to packaged `.sdp` + `data/pool/` archives on standard BAR installs.
- [x] Package manifest lookups are cached in memory per `(bar_root, digest)` and invalidated when installation settings change.
- [x] Existing security and resource guards (strict unit ID regex, no symlinks/junctions, MD5/size validation, `MAX_ICON_BYTES`, `MAX_ICON_PIXELS`) are enforced for pooled icons.
- [x] Unit tests in `tests/test_unit_icons.py` cover unpacked, `.sdp` packaged fallback, missing icon, and corrupted/oversized pool entries.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify unit icons display in Studio against a standard packaged BAR installation without `BAR.sdd`.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Chromium rendered a packaged armck PNG at 256 × 256 on the installed BAR setup without BAR.sdd. Final checks decoded four packaged unit icons and verified cache, corruption, size, and latest-package handling.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
