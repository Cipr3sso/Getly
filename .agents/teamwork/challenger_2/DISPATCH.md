# Task Assignment: Challenger 2 — WinUI 3 & Chrome Extension Empirical Verification

## 2026-11-02T12:08:13Z
You are teamwork_challenger assigned to Challenger 2 (WinUI 3 & Chrome Extension Empirical Challenger).
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_2
MANDATORY: Read ORIGINAL_REQUEST.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_2\DISPATCH.md.
Execute empirical verification:
- Run dotnet build src/Getly.UI -c Debug -r win-x64 (must have 0 errors, 0 warnings).
- Run dotnet test tests/Getly.UI.Tests -r win-x64.
- Test extension files (manifest JSON validity, node -c syntax check, absence of Sec-Ch-Ua / 124).
- Test edge cases in URL extraction / batch deduction.
Write your report and explicit verdict (APPROVE or REJECT) to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_2\handoff.md.
Update your progress.md periodically.
When done, send a message with your verdict to your parent orchestrator.

## Verification Tasks
1. Execute WinUI 3 build:
   `dotnet build src/Getly.UI -c Debug -r win-x64`
   Must produce 0 errors and 0 warnings.
2. Execute WinUI 3 unit tests:
   `dotnet test tests/Getly.UI.Tests -r win-x64`
   All unit tests must pass.
3. Validate Chrome extension:
   - Validate JSON syntax: `python -m json.tool extensions/chrome/manifest.json`
   - Validate JS syntax: `node -c extensions/chrome/background.js extensions/chrome/popup.js`
   - Check absence of fake `Sec-Ch-Ua` or `v="124"`.
4. Adversarial stress test on Batch Download regex & deduction logic:
   - Test URLs embedded in dirty text, quotes, query strings, magnet URIs, and verify filename and category deduction.

## Deliverable
Write your empirical test report and verdict (APPROVE or REJECT) to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\challenger_2\handoff.md`
Then send a brief message with your verdict to the orchestrator.
