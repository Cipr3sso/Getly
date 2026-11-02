# Task Assignment: Survey WinUI 3 Frontend

## Objective
Survey the WinUI 3 frontend in `src/Getly.UI` and tests in `tests/Getly.UI.Tests` to map all requirements under R1 of `ORIGINAL_REQUEST.md`.

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`

## Scope of Investigation
1. `MainWindow.xaml` and `MainWindow.xaml.cs`:
   - Inspect all `ContentDialog` instances and how they are created/shown.
   - Inspect `CommandBar` and where `📦 Download Batch` button should be added.
   - Check how theme changes and `ActualTheme` / `XamlRoot` are handled.
2. `FloatingDownloadWindow.xaml` and `FloatingDownloadWindow.xaml.cs` (Getly Mini):
   - Check window creation, `AppWindow`, `IsShownInSwitchers`.
   - Check top row layout: Grid, TextBlock columns, TextTrimming, MaxLines, Margin, error/speed vs status text.
   - Check multi-task navigation (`ActiveTasks`, `◀ 1 / N ▶`, expand/collapse button `▼`, height transition 175px to 350px, compact list with CheckBox, file name, size MB, action bar).
   - Check `WaitingConfirmation` state handling, buttons (`Avvia Ora`, `In Coda`, `Annulla`, destination path with `Modifica`).
3. Settings and ViewModels:
   - `Views/SettingsView.xaml` (or where settings UI is located) and `ViewModels/MainViewModel.cs` (or SettingsViewModel):
   - Check `RequireConfirmation`, `ConfirmationThresholdMB`, `AutoCategorize`.
   - Check how settings are persisted and synchronized with Python backend.
4. Batch Download Dialog (`+ Download Batch`):
   - Check existing dialog patterns and components needed for multiline URL input, regex analysis (http/https/magnet), list with checkboxes, deduced filename, category badge, actions ("Avvia Selezionati", "Aggiungi in Coda", "Annulla").
5. Tests in `tests/Getly.UI.Tests`:
   - Inspect test project structure, dependencies, running tests with `dotnet test tests/Getly.UI.Tests -r win-x64`.

## Deliverable
Write your complete survey report to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_1\handoff.md`
Then send a brief completion message back to the orchestrator.


## 2026-11-02T11:02:22Z
You are teamwork_explorer assigned to survey the WinUI 3 Frontend for Getly.
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_1
MANDATORY: Read the original user request at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_1\DISPATCH.md.
Explore the repository: inspect src/Getly.UI (MainWindow.xaml/.cs, FloatingDownloadWindow.xaml/.cs, SettingsView, ViewModels, Dialogs) and tests/Getly.UI.Tests.
Write your detailed findings report to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_1\handoff.md.
Update your progress.md periodically.
When finished, send a brief message to your parent orchestrator confirming completion.
