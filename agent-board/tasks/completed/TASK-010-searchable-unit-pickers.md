# TASK-010: Searchable & filterable unit pickers

- **Status:** COMPLETED
- **Owner:** Codex-Editors
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `web/studio/studio.js`, `web/studio/style.css`, `app/unit_catalog.py`

## Goal

Replace unwieldy raw `<select>` dropdowns for hundreds of units with a reusable searchable, filterable unit picker featuring friendly names, faction/role filters, and inline unit icons.

## Scope & Implementation Notes

- `behaviour.json`, `factory.json`, and `build_chain.json` contain hundreds of units currently selected via plain `<select>` controls in `web/studio/studio.js` (`unitsView`, `production`, `defenseView`).
- Build a reusable accessible unit picker widget in `web/studio/studio.js` (styled in `web/studio/style.css`) supporting:
  - Instant search by friendly unit name (`friendlyUnitName`) or raw unit ID.
  - Quick filter chips/dropdowns for faction prefix (`arm` / `cor` / `leg` / other) and primary role (when role metadata is available in `behaviour.json` or catalog).
  - Inline unit icons (`unitIcon`) alongside readable labels.
- Apply the picker consistently to **Unit behavior** (`unitsView`), **Production** (`production` factory selector and catalog unit add picker), and **Defense** (`defenseView` completed-building selector), while keeping keyboard navigation and screen-reader accessibility intact.

## Acceptance Criteria

- [x] A reusable searchable/filterable unit picker works smoothly with hundreds of units without UI lag.
- [x] Users can search by either friendly unit name or raw unit ID and filter by faction (`arm`/`cor`/`leg`) and role (where available).
- [x] Inline unit icons render properly when icon loading is available.
- [x] Keyboard navigation (`ArrowUp`/`ArrowDown`/`Enter`/`Escape`) and accessible labels work across **Unit behavior**, **Production**, and **Defense**.
- [x] Existing test suite passes and UI behavior is verified interactively.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify search, faction/role filtering, icon display, and keyboard selection in the browser across all three editor pages.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Chromium exercised search, faction and role filters, unit icons, and keyboard selection in Unit behavior, Production, and Defense.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
