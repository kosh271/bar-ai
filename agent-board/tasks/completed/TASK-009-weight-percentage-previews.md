# TASK-009: Normalized percentage preview for relative weights

- **Status:** COMPLETED
- **Owner:** Codex-Editors
- **Priority:** P2
- **Dependencies:** None
- **Primary Files:** `web/studio/studio.js`, `web/studio/openings.js`, `web/studio/style.css`

## Goal

Display live normalized percentage shares (`%`) next to relative weight inputs in the **Production** and **Opening** editors so authors can immediately gauge effective selection probabilities without changing the raw weight values stored on disk.

## Scope & Implementation Notes

- In `web/studio/studio.js` (`production()`, lines 610–638), factory unit weights are edited per `tierN` column, while in `web/studio/openings.js` (`StudioOpenings`, lines 363–382), queue weights are relative float literals (`SQueue(weight, {...})`) that are never normalized in source.
- Compute read-only normalized shares client-side:
  - In **Production**, compute each unit's percentage share within its `tierN` column (`weight / sum_positive_weights * 100`, handling zero-sum columns and negative/non-finite edge cases cleanly) and update live on edit or unit addition/removal.
  - In **Opening**, compute each queue's percentage share across all valid queues for the selected factory (`queueWeight / sum_queue_weights * 100`) and display it in each queue header.
- Keep stored JSON / AngelScript source values exact and untouched; style the percentage hints unobtrusively in `web/studio/style.css`.

## Acceptance Criteria

- [x] **Production** table displays live read-only `%` share next to each unit's tier weight (and handles zero-total or negative weights cleanly without `NaN`).
- [x] **Opening** editor displays live read-only `%` share for each `SQueue` in the selected factory.
- [x] Percentage previews update immediately when weights change, rows are added/removed, or undo/reload occurs.
- [x] Saved files retain exact user-entered raw weights without normalization or rounding.

## Verification

- Run `python -m unittest discover -s tests`.
- Verify live percentage calculations in the browser across Production and Opening editors (including zero-sum and decimal weights).

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review.

### Findings

- Chromium verified live shares in Production and Opening, including zero/negative weights and preservation of decimal spellings in saved source.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
