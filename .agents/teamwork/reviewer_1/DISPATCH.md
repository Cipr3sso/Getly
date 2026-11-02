# Task Assignment: Reviewer 1 — WinUI 3 Frontend & Chrome Extension Review

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
Read PROJECT.md at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md`
Read worker handoff reports at:
- `c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m2\handoff.md`
- `c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m3\handoff.md`

## Scope of Review
1. **WinUI 3 Frontend (`src/Getly.UI`)**:
   - Verify `dotnet build src/Getly.UI -c Debug -r win-x64` yields 0 warnings, 0 errors.
   - Verify `dotnet test tests/Getly.UI.Tests -r win-x64` passes all tests.
   - Inspect `MainWindow.xaml.cs`: verify `PrepareDialog` sets `XamlRoot` and `RequestedTheme` on all `ContentDialog` instances.
   - Inspect `FloatingDownloadWindow.xaml` and `.cs`: verify `AppWindow.IsShownInSwitchers = false;`, verify 2-column Grid with `Margin="0,0,12,0"`, `CharacterEllipsis`, `MaxLines="1"` on error text, verify `ActiveTasks`, `◀ 1 / N ▶` paging, `▼` drawer expand/collapse (175px/350px), and `WaitingConfirmation` state buttons.
   - Inspect `SettingsView` and `MainViewModel.cs`: verify `RequireConfirmation`, `ConfirmationThresholdMB`, `AutoCategorize`, `%LOCALAPPDATA%\Getly\settings.json` persistence, and IPC synchronization.
   - Inspect `+ Download Batch` CommandBar button and dialog flow.
2. **Chrome Extension (`extensions/chrome/`)**:
   - Inspect `manifest.json`: verify `"scripting"` in `"permissions"`.
   - Inspect `background.js`: verify 0 hardcoded `Sec-Ch-Ua` or fake `v="124"`, verify real `User-Agent`, `Referer`, and full `Cookie`, and `DOWNLOAD_BATCH` listener.
   - Inspect `popup.html`, `popup.css`, `popup.js`: verify "Analizza tutti i link della pagina", link extraction via `chrome.scripting.executeScript`, 5-category filtering, selection state, and batch dispatch to `POST http://127.0.0.1:9666/add_batch`.

## Deliverable
Write your comprehensive review report and explicit verdict (APPROVE or REQUEST_CHANGES) to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_1\handoff.md`
Then send a brief message with your verdict to the orchestrator.

## 2026-11-02T12:07:55Z
You are teamwork_reviewer assigned to Reviewer 1 (WinUI 3 Frontend & Chrome Extension Review).
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_1
MANDATORY: Read ORIGINAL_REQUEST.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read worker handoffs:
- c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m2\handoff.md
- c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m3\handoff.md
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_1\DISPATCH.md.
Perform rigorous review, run builds and tests (dotnet build and dotnet test), and write your comprehensive report and explicit verdict (APPROVE or REQUEST_CHANGES) to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_1\handoff.md.
Update your progress.md periodically.
When done, send a message with your verdict to your parent orchestrator.
