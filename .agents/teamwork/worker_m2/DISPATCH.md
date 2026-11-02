# Task Assignment: Milestone M2 — Chrome Extension

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
Read the architecture and contracts in:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md`
Read the detailed survey report from Explorer 3 at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_3\handoff.md`

## Exclusive Write Ownership
You exclusively own:
- `extensions/chrome/manifest.json`
- `extensions/chrome/background.js`
- `extensions/chrome/popup.html`
- `extensions/chrome/popup.css`
- `extensions/chrome/popup.js`

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Requirements to Implement

1. **Manifest Permissions (`manifest.json`)**:
   - Add `"scripting"` to `"permissions"` array.

2. **Header Alignment & Client Hints (`background.js`)**:
   - In `chrome.downloads.onCreated.addListener`, remove the hardcoded `"Sec-Ch-Ua"`, `"Sec-Ch-Ua-Mobile"`, and `"Sec-Ch-Ua-Platform"` headers.
   - Ensure clean real headers (`User-Agent`, `Referer`, and full `Cookie` from `extractFullCookieHeader`) are transmitted.
   - Add `DOWNLOAD_BATCH` message listener in `chrome.runtime.onMessage`.
   - Implement `sendBatchToGetly(items, refererUrl)`: resolves cookies for each item, builds the payload, and sends `POST http://127.0.0.1:9666/add_batch`.

3. **Page Link Grabber & UI (`popup.html`, `popup.css`, `popup.js`)**:
   - In `popup.html`:
     - Add button `"🔍 Analizza tutti i link della pagina"` (`#scanPageLinksBtn`).
     - Add link grabber section with category filter bar (`Tutti`, `Archivi`, `Documenti`, `Video`, `Programmi`).
     - Add select-all checkbox, scrollable link list with individual checkboxes, file name, extension badge, and full URL tooltip.
     - Add action button `"⬇️ Scarica selezionati con Getly"` (`#downloadBatchBtn`).
   - In `popup.css`:
     - Add styling consistent with Fluent / dark theme (filter bar, filter pills, grabber item list, badges, checkboxes).
   - In `popup.js`:
     - Implement click on `#scanPageLinksBtn`: invoke `chrome.scripting.executeScript` targeting `activeTab.id`.
     - Script function extracts links from `<a href>`, `<video src>`, `<audio src>`, and `<source src>`.
     - Filter links by file extensions (.zip, .rar, .7z, .pdf, .mp4, .mkv, .iso, .exe, .msi, .apk, .tar.gz, etc.).
     - Categorize items into:
       - **Archivi**: `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, `.tgz`, `.bz2`, `.xz`, `.7-zip`
       - **Documenti**: `.pdf`, `.epub`, `.mobi`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.txt`, `.rtf`, `.odt`, `.csv`
       - **Video**: `.mp4`, `.mkv`, `.webm`, `.avi`, `.mov`, `.wmv`, `.flv`, `.m4v`, `.m3u8`, `.mpd`, `.ts`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.wav`, `.ogg`, `.opus`
       - **Programmi**: `.exe`, `.msi`, `.apk`, `.dmg`, `.pkg`, `.deb`, `.rpm`, `.iso`, `.bin`, `.appimage`
       - **Tutti**: all detected downloadable files.
     - Support category pill clicks to filter visible items.
     - Handle selection state (all or individual checkboxes).
     - On `#downloadBatchBtn` click, send `type: "DOWNLOAD_BATCH"` to `background.js`.

4. **Verification**:
   - Validate `manifest.json` syntax.
   - Verify no hardcoded `Sec-Ch-Ua` remains in `background.js`.
   - Verify all grabber and batch download logic is complete and functional.

## Deliverable
Write your complete handoff report to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m2\handoff.md`
Report verification commands executed and results. Then send a completion message to the orchestrator.

## 2026-11-02T11:35:52Z
You are teamwork_worker assigned to implement Milestone M2: Chrome Extension for Getly.
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m2
MANDATORY: Read the original user request at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read the survey report at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_3\handoff.md.
Read your task assignment and integrity warning at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m2\DISPATCH.md.
Implement all requirements in extensions/chrome/ (manifest.json, background.js, popup.html, popup.css, popup.js).
Verify your changes (JSON syntax validation, grep check for no hardcoded Sec-Ch-Ua v="124", test link extraction and batch payload).
Write your detailed handoff report to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m2\handoff.md.
Update your progress.md periodically.
When done, send a completion message to your parent orchestrator.

