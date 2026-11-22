# Agent board

Track open jobs here. Read the linked task page before claiming or continuing work.

## Tasks

| ID  | Status | Owner | Task |
| --- | ------ | ----- | ---- |

## Board rules

- Status describes workflow only: **READY** = next work can start; **IN PROGRESS** = claimed and unfinished; **REVIEW** = delivered work awaits review/integration; **BLOCKED** = a named dependency prevents the next step. Finished work moves to the archive.

- Task names on the board should be brief more details and implementation plans and other collateral for a task go in the tasks detail page in the tasks/ folder.

- Keep one row per open job: ID, status, owner and a short task-page link.

- Claim a READY task before starting; update its row when ownership or status changes. Preserve other agents' edits.

- Claude, Gemini and Codex share the READY queue. Use a distinct provider/worker name, re-read before changing the index, and verify the claim before source edits. Choose independent file scopes or review another delivery while shared integration is occupied.

- Put scope, acceptance criteria, progress, findings, commands, hashes, run receipts and handoff history in task pages; link reports from the task.

- Keep tasks awaiting review or implementation open. Once delivered with no pending review, move the row and all task/report/support files to [tasks/completed/](tasks/completed), and repair incoming and outgoing links. Experimental results do not imply promotion.

- Use the [archive board](tasks/completed/completed-tasks.md) for finished work; do not add result summaries, agent diaries or historical status banners here.
