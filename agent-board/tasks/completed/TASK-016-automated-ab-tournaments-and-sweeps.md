# TASK-016: Automated A/B tournaments & parameter sweeps

- **Status:** COMPLETED
- **Owner:** Codex-Matches
- **Priority:** Horizon
- **Dependencies:** `TASK-001`, `TASK-002`, `TASK-005`
- **Primary Files:** `app/studio_matches.py`, `app/studio_server.py`, `app/projects.py`, `web/studio/studio.js`

## Goal

Enable unattended A/B tournaments and parameter sweeps where BAR AI Studio runs multiple profile variants or swept parameter values across a map pool and ranks variants by win rate, average match duration, and script/engine stability.

## Scope & Implementation Notes

- **Blocked on:**
  - `TASK-001` (Multi-game benchmark series & team rosters — execution engine & multi-game queue)
  - `TASK-002` (Persistent match history tied to checkpoints — persistent experiment indexing)
  - `TASK-005` (Post-match summary & telemetry — match duration & stability metrics)
- Once dependencies land:
  - Allow defining an experiment matrix: either comparing *K* existing profiles/projects head-to-head (round-robin or against a fixed baseline opponent) or sweeping a numeric JSON/script parameter across candidate values in temporary/cloned evaluation profiles without mutating the user's working profile.
  - Execute the matrix unattended via `MatchService` with explicit real-time/game-count budgets, per-variant error isolation, and clean cancellation/pause.
  - Display a ranked leaderboard summarizing wins, losses, draws, win rate, average simulated match duration, and diagnostic errors, with one-click promotion of the winning variant into a project profile or checkpoint.

## Acceptance Criteria

- [x] Supports head-to-head A/B tournaments across multiple profiles/projects and a configurable map pool.
- [x] Supports parameter sweep definitions that materialize isolated temporary/variant configs without corrupting the source profile.
- [x] Ranks tested variants by win rate, average match length, and error-free completion, persisted in match/experiment history.
- [x] Respects real-time/game-count caps and supports clean cancellation without orphaned engine processes.

## Verification

- Run `python -m unittest discover -s tests`.
- Add integration tests using mocked/stubbed headless runs verifying round-robin scheduling, parameter sweep isolation, ranking math, and cancellation.

## Progress, Findings & Handoff

- **2026-11-21:** Task created from feature roadmap review. Marked **BLOCKED** pending `TASK-001`, `TASK-002`, and `TASK-005`.

### Findings

- Chromium exercised tournament, uninstalled-profile sweep, and paired-result promotion through Studio with a stubbed engine. A native isolated sweep passed smoke, ran for 60 simulated seconds, and preserved the source bytes. Integration tests covered pause, cancellation, budgets, rankings, and persisted history.

### Commands & Run Receipts

- Integrated verification passed with 164 Python tests run, six skipped, 11 JavaScript tests passed, and a clean diff check.
