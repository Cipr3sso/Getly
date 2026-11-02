# Task Assignment: Survey Chrome Extension

## Objective
Survey the Chrome Extension in `extensions/chrome/` to map all requirements under R3 of `ORIGINAL_REQUEST.md`.

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`

## Scope of Investigation
1. `manifest.json`:
   - Inspect existing permissions, matches, host permissions, background service worker, popup.
   - Check where `"scripting"` permission needs to be added.
2. `background.js`:
   - Inspect header interception / transmission to local backend (`http://127.0.0.1:9666`).
   - Identify any hardcoded `Sec-Ch-Ua` headers (e.g. `v="124"`) and remove them in favor of real `navigator.userAgent`, real `Referer`, and full `Cookie` header (`extractFullCookieHeader`).
3. `popup.html` and `popup.js`:
   - Inspect existing UI and logic in the popup.
   - Check requirements for "Analizza tutti i link della pagina" button:
     - Execute `chrome.scripting.executeScript` on active tab.
     - Extract links from `<a href>`, `<video src>`, `<audio src>`, `<source src>`.
     - Filter downloadable files by extensions (.zip, .rar, .7z, .pdf, .mp4, .mkv, .iso, .exe, .msi, .apk, .tar.gz, etc.).
     - Category filter bar (Tutti, Archivi, Documenti, Video, Programmi).
     - Checkbox for each item.
     - Button "Scarica selezionati con Getly" sending batch payload to `POST http://127.0.0.1:9666/add_batch`.

## Deliverable
Write your complete survey report to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_3\handoff.md`
Then send a brief completion message back to the orchestrator.


## 2026-11-02T11:02:35Z
You are teamwork_explorer assigned to survey the Chrome Extension for Getly.
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_3
MANDATORY: Read the original user request at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_3\DISPATCH.md.
Explore the repository: inspect extensions/chrome/ (manifest.json, background.js, popup.html, popup.js, icons/assets).
Write your detailed findings report to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_3\handoff.md.
Update your progress.md periodically.
When finished, send a brief message to your parent orchestrator confirming completion.
