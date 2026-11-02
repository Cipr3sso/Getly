# Task Assignment: Challenger 1 — Python Backend Empirical Verification

## Mandatory Reading
Read ORIGINAL_REQUEST.md at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
Read PROJECT.md at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md`
Read user rules in:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md`

## Mission
You are a code-executing adversarial challenger. Empirically verify the correctness, resilience, and boundary handling of the Python backend changes.

## Verification Tasks
1. Execute full test suite:
   `C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v`
   Ensure 169+ tests pass with 0 failures.
2. Adversarial tests on `sanitize_headers_for_impersonation`:
   Test stripping of various case-insensitive combinations of `Sec-Ch-Ua`, `sec-ch-ua-platform`, `user-agent` while strictly keeping `cookie` and `referer`.
3. Adversarial tests on `POST /add_batch`:
   Verify behavior with empty list, missing fields, invalid URLs, large files exceeding confirmation threshold (triggering `WAITING`), and auto-categorization.
4. Verify Range 403 single-stream downgrade and worker exception preservation on `task.error_message`.

## Deliverable
Write your empirical test report and verdict (APPROVE or REJECT) to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_1\handoff.md`
Then send a brief message with your verdict to the orchestrator.


## 2026-11-02T12:08:08Z
[Message] timestamp=2026-11-02T12:08:08Z sender=6b06a18d-9c2f-48a8-997b-397dc5eec34b priority=MESSAGE_PRIORITY_HIGH content=You are teamwork_challenger assigned to Challenger 1 (Python Backend Empirical Challenger).
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_1
MANDATORY: Read ORIGINAL_REQUEST.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read AGENTS.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md.
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_1\DISPATCH.md.
Execute empirical verification, run pytest suite, stress test header sanitization and batch API edge cases, and write your report and explicit verdict (APPROVE or REJECT) to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_1\handoff.md.
Update your progress.md periodically.
When done, send a message with your verdict to your parent orchestrator.
