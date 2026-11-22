# TASK-012: `init.as`-aware layer preview presets

- **Status:** COMPLETED
- **Owner:** Codex-Validation
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `web/studio/scripts.js`, `web/studio/config.js`, `web/studio/studio.js`

## Goal

Inspect `script/init.as` (following forwarding `#include`s) to auto-suggest realistic layer load orders and scenario presets in **Preview combined layers**, reducing manual guesswork when testing how base and variant configs override one another.

## Scope & Implementation Notes

- `layerPreview()` in `web/studio/studio.js` (lines 202–278) currently offers only a generic family example (`Quick example`) and warns that it does not inspect `init.as`.
- Add a conservative static analyzer in `web/studio/scripts.js` (building on `StudioScripts.mask` and `StudioScripts.resolve`) that inspects `script/init.as` (and any included script helpers) to recognize stock BARbarIAn config-loading calls and conditional branches (e.g., base `config/*.json`, `*_leg.json` for Legion, `*_swarm.json`, `*_scav_units.json`, `*_extra_units.json`, and map/game-mode conditional overrides).
- Expose recognized scenario presets inside `layerPreview()` (e.g., **Standard (Armada / Cortex)**, **Legion enabled**, **Swarm / Extra units**, **Detected custom branches**), ordering the matching files automatically while clearly labeling any dynamic or unrecognized branches that still require engine verification.

## Acceptance Criteria

- [x] Static analyzer safely follows `#include`s in `script/init.as` without executing arbitrary script code.
- [x] Stock BARbarIAn layer orders and common conditional variants (e.g., Legion, Swarm, Extra/Scav) are detected and offered as one-click presets in `Preview combined layers`.
- [x] Unrecognized or dynamic conditions in `init.as` are surfaced transparently as warnings rather than silently guessed.
- [x] Existing `StudioConfig.previewLayers` merge semantics and read-only guarantees remain unchanged.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify layer presets against stock BARbarIAn `init.as` profiles (base + conditional layers) and a profile with custom `init.as` logic.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Static analysis produced eight startup presets for each of stock Hard, Hard Aggressive, Medium, and Easy. Chromium displayed stock presets and Node verified the helper expansion limit.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
