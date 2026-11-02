# Task Assignment: Forensic Auditor — Integrity Verification

## Mandatory Reading
Read ORIGINAL_REQUEST.md at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
Read PROJECT.md at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md`
Read user rules in:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md`

## Mission
You are the Forensic Integrity Auditor (`teamwork_auditor`). Perform systematic integrity forensics on all changes introduced across Python Core (`core/`), Chrome Extension (`extensions/chrome/`), and WinUI 3 Frontend (`src/Getly.UI/`).

## Forensic Checks to Perform
1. **Cheating & Facade Detection**:
   - Verify that no implementations are dummy, facade, or hardcoding expected test results.
   - Verify `POST /add_batch` actually parses and forwards items to `QueueManager`.
   - Verify `PrepareDialog` genuinely configures `XamlRoot` and `RequestedTheme` dynamically from the actual framework element root.
   - Verify `FloatingDownloadWindow` genuinely transitions size and uses `ActiveTasks`.
   - Verify `_download_single_stream_curl` and sequential downgrade genuinely execute HTTP requests.
   - Verify `sanitize_headers_for_impersonation` genuinely sanitizes headers and preserves cookies/referer.
2. **Anti-bot & User Rules Compliance**:
   - Check that no artificial `Sec-Ch-Ua` headers or `"124"` versions remain in `background.js` or `probe.py`.
   - Verify compliance with Winsock `IP_UNICASTIF`, IPv4 `family=socket.AF_INET`, and HTTP 429 exponential backoff.
3. **Execution Verification**:
   - Run tests: `pytest tests/` and `dotnet test tests/Getly.UI.Tests -r win-x64` to verify tests genuinely run and pass against the actual code.
4. **Binary Verdict**:
   - You MUST declare an explicit verdict: `CLEAN` or `INTEGRITY VIOLATION`.

## Deliverable
Write your complete forensic audit report and explicit binary verdict to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\auditor_1\handoff.md`
Then send a brief message with your verdict to the orchestrator.


## 2026-11-02T12:08:22Z
You are teamwork_auditor assigned to perform Forensic Integrity Verification across all Getly changes (WinUI 3, Python Core, Chrome Extension).
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\auditor_1
MANDATORY: Read ORIGINAL_REQUEST.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read AGENTS.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md.
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\auditor_1\DISPATCH.md.
Perform systematic forensic checks:
1. Verify no dummy, facade, or hardcoded cheating implementations exist.
2. Verify dynamic and authentic logic across Python Core, Chrome Extension, and WinUI 3.
3. Verify compliance with user rules in AGENTS.md (Winsock IP_UNICASTIF, IPv4 stack forcing, HTTP 429 exponential backoff, partial resume).
4. Run tests: pytest tests/ and dotnet test tests/Getly.UI.Tests -r win-x64.
5. Provide an explicit binary verdict: CLEAN or INTEGRITY VIOLATION.
Write your full forensic audit report to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\auditor_1\handoff.md.
Update your progress.md periodically.
When done, send a message with your verdict to your parent orchestrator.
