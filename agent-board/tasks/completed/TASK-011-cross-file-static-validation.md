# TASK-011: Cross-file static validation & linting

- **Status:** COMPLETED
- **Owner:** Codex-Validation
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `web/studio/studio.js`, `web/studio/config.js`, `web/studio/scripts.js`, `web/studio/openings.js`

## Goal

Expand Studio's single-file JSON checks into a profile-wide static validator that catches broken unit IDs, unbuildable factory units, unknown role references, and broken `#include` chains before waiting on a 2-minute headless smoke test.

## Scope & Implementation Notes

- Currently `validateConfig()` in `web/studio/studio.js` (lines 1409–1449) only checks array-length alignment within a single JSON file and generator count bounds.
- Expand validation into a structured profile validator distinguishing **blocking syntax/structural errors** from **non-blocking advisory warnings** (since conditional layers/mod options may legitimately reference extra units):
  - Validate unit IDs in `factory.json`, `build_chain.json`, `block_map.json`, and `behaviour.json` against the loaded unit catalog (`state.catalog`), including whether a unit added to a factory appears in that factory's `builds` list.
  - Cross-check role names across `response.json` (`vs`), `behaviour.json` (`role`), and `commander.as` (`RT::...` roles parsed by `StudioOpenings`) to flag typos or undefined roles.
  - Validate `#include` paths and detect missing files or cycles across all `.as` scripts using `StudioScripts.resolve` (`web/studio/scripts.js`).
- Surface a **Validate profile** button/report (plus pre-save warning summary) linking directly to the relevant page or file.

## Acceptance Criteria

- [x] Profile-wide validator separates blocking structural/syntax issues from advisory cross-file warnings (unknown unit IDs, non-catalog factory build options, mismatched/unknown role names, broken `#include` scripts).
- [x] Validation works gracefully whether or not a unit catalog has been scanned yet (falling back to cross-file consistency checks when no catalog is loaded).
- [x] Users can run **Validate profile** on demand from the workshop UI and inspect categorized findings with file/location context.
- [x] Existing save/validation behavior does not block editing legitimate custom or mod-specific units behind false-positive hard errors.

## Verification

- Run `python -m unittest discover -s tests`.
- Test validator against clean profiles, intentional typos (bad unit ID, bad role name, broken `#include`, misaligned tier array), and custom-unit warnings.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Node and Chromium verified the profile report and severity classification against stock and malformed fixtures. Save, Install & Test awaits advisory review.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
