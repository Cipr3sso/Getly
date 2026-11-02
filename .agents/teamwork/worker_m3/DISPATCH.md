# Task Assignment: Milestone M3 — WinUI 3 Frontend

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
Read the architecture and contracts in:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md`
Read the detailed survey report from Explorer 1 at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_1\handoff.md`

## Exclusive Write Ownership
You exclusively own:
- `src/Getly.UI/Views/MainWindow.xaml`
- `src/Getly.UI/Views/MainWindow.xaml.cs`
- `src/Getly.UI/Views/FloatingDownloadWindow.xaml`
- `src/Getly.UI/Views/FloatingDownloadWindow.xaml.cs`
- `src/Getly.UI/ViewModels/MainViewModel.cs`
- `src/Getly.UI/ViewModels/TaskItemViewModel.cs`
- `src/Getly.UI/Services/IpcClient.cs` (if needed for IPC synchronization)

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Requirements to Implement

1. **Dark Theme Alignment on all `ContentDialog` (`MainWindow.xaml.cs`)**:
   - Create centralized helper method:
     ```csharp
     private void PrepareDialog(ContentDialog dialog)
     {
         if (this.Content is FrameworkElement root)
         {
             dialog.XamlRoot = root.XamlRoot;
             dialog.RequestedTheme = root.ActualTheme;
         }
     }
     ```
   - Invoke `PrepareDialog(dialog)` before every `await dialog.ShowAsync()` in `MainWindow.xaml.cs` (inclusi "Nuovo Download", "Eliminare il download...", "Dettagli del Download", and the new batch dialog).

2. **Fix Grafico & Multi-Tasking Unificato in `FloatingDownloadWindow` (Getly Mini)**:
   - Apply `AppWindow.IsShownInSwitchers = false;` to prevent duplicate taskbar icons.
   - Fix overlap in top row metrics (`FloatingDownloadWindow.xaml`):
     - Replace unbounded horizontal StackPanel in Column 0 with a Grid, apply `Margin="0,0,12,0"`, `TextTrimming="CharacterEllipsis"`, `MaxLines="1"` on the left TextBlock so long error/speed text truncates neatly without colliding with the status text in Column 1 (`Width="Auto"`).
   - Multi-task navigation & expandable drawer:
     - Manage `ObservableCollection<TaskItemViewModel> ActiveTasks` (provide `MonitoredTasks => ActiveTasks` alias for compatibility).
     - Header controls: `◀` `1 / N` `▶` to page through tasks and `▼` button to expand/collapse drawer.
     - Height transition: resize from 175px (collapsed) to 350px (expanded) via `PositionWindow()` / `AppWindow.Resize()`.
     - Drawer content: compact ListView with CheckBox, file name, weight in MB ("peso in MB"), and bottom action bar with buttons to select all, start or delete selected.
   - `WaitingConfirmation` state handling:
     - In footer show buttons: `Avvia Ora`, `In Coda`, `Annulla`, and destination path with `Modifica` button.
     - In `TaskItemViewModel.cs`: update `IsPendingConfirmation` check to recognize `"WaitingConfirmation"` and `"WAITING_CONFIRMATION"`.

3. **Settings: Confirmation Threshold & Auto-Categorization (`MainWindow.xaml` & `MainViewModel.cs`)**:
   - In `MainWindow.xaml` under `SettingsView` "Download & Percorsi":
     - `ToggleSwitch` `RequireConfirmation` (default: `false`).
     - `NumberBox` `ConfirmationThresholdMB` (default: `100`, Min: `0`, Max: `100000`).
     - `ToggleSwitch` `AutoCategorize` (default: `true`).
   - In `MainViewModel.cs`:
     - Add observable properties `RequireConfirmation`, `ConfirmationThresholdMB`, `AutoCategorize`.
     - Persist settings to local JSON file in `%LOCALAPPDATA%\Getly\settings.json`.
     - In `SaveSettingsAsync`: serialize to `settings.json` and sync via `_ipcClient` (`system.set_settings`).

4. **Batch Download Dialog (`+ Download Batch`)**:
   - In `MainWindow.xaml` `CommandBar`: next to `+ Nuovo Download`, add button `📦 Download Batch` (`AppBarButton`).
   - In `MainWindow.xaml.cs`: on click, show a `ContentDialog` prepared with `PrepareDialog(dialog)`:
     - Multiline `TextBox` to paste URL list or raw text.
     - Button "Analizza URL" extracting links via RegExp (`https?://[^\s"'<>]+|magnet:\?[^\s"'<>]+`).
     - Populated list with `CheckBox`, deduced filename, category badge.
     - Buttons: "Avvia Selezionati", "Aggiungi in Coda", "Annulla".

5. **Build & Test Verification**:
   - Run: `dotnet build src/Getly.UI -c Debug -r win-x64`
     Ensure 0 errors and 0 warnings.
   - Run: `dotnet test tests/Getly.UI.Tests -r win-x64`
     Ensure all unit tests pass.

## Deliverable
Write your complete handoff report to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m3\handoff.md`
Report compilation and test execution commands and results. Then send a completion message to the orchestrator.

## 2026-11-02T11:35:58Z
From: 6b06a18d-9c2f-48a8-997b-397dc5eec34b
Content: You are teamwork_worker assigned to implement Milestone M3: WinUI 3 Frontend for Getly.
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m3
MANDATORY: Read the original user request at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read the survey report at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_1\handoff.md.
Read your task assignment and integrity warning at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m3\DISPATCH.md.
Implement all requirements in src/Getly.UI (MainWindow.xaml/.cs, FloatingDownloadWindow.xaml/.cs, MainViewModel.cs, TaskItemViewModel.cs).
Verify your changes:
- Run dotnet build src/Getly.UI -c Debug -r win-x64 (ensure 0 warnings, 0 errors).
- Run dotnet test tests/Getly.UI.Tests -r win-x64 (ensure all tests pass).
Write your detailed handoff report to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m3\handoff.md.
Update your progress.md periodically.
When done, send a completion message to your parent orchestrator.
