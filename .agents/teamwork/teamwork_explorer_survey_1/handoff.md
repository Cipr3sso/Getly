# Survey Report: WinUI 3 Frontend (`src/Getly.UI` & `tests/Getly.UI.Tests`)

## 1. Observation

Direct code examination and tool executions were performed on the repository `c:\Users\carlo\Documents\antigravity\resilient-heisenberg`.

### 1.1 Project Structure and Build Health
- **UI Project**: `src/Getly.UI/Getly.UI.csproj` targets `net8.0-windows10.0.19041.0`, runtime `win-x64`, with `Microsoft.WindowsAppSDK` (v1.5.240607001) and `CommunityToolkit.Mvvm` (v8.2.2).
- **Test Project**: `tests/Getly.UI.Tests/Getly.UI.Tests.csproj` targets `net8.0-windows10.0.19041.0`, `win-x64`, with `xunit` (v2.9.2).
- **Build Status**:
  - Command: `dotnet build src/Getly.UI -c Debug -r win-x64`
  - Output: `Compilazione completata. Avvisi: 0, Errori: 0.` (Exit code: 0)
- **Test Status**:
  - Command: `dotnet test tests/Getly.UI.Tests -r win-x64`
  - Output: `Superato! Non superati: 0. Superati: 9. Ignorati: 0. Totale: 9. Durata: 2 s` (Exit code: 0)

---

### 1.2 `MainWindow.xaml` & `MainWindow.xaml.cs` (Theme & Dialog Management)
- **File**: `src/Getly.UI/Views/MainWindow.xaml.cs`
- **ContentDialog Invocations**:
  1. Lines 357–379 (`OnDeleteButtonClick`):
     ```csharp
     var dialog = new ContentDialog
     {
         Title = title,
         PrimaryButtonText = "Elimina",
         CloseButtonText = "Annulla",
         DefaultButton = ContentDialogButton.Close,
         XamlRoot = Content.XamlRoot,
         Content = new StackPanel { ... }
     };
     var result = await dialog.ShowAsync();
     ```
  2. Lines 566–588 (`OnAddDownloadClick`):
     ```csharp
     var dialog = new ContentDialog
     {
         Title = "Nuovo Download",
         PrimaryButtonText = "Avvia Download",
         CloseButtonText = "Annulla",
         DefaultButton = ContentDialogButton.Primary,
         XamlRoot = Content.XamlRoot,
         Content = new StackPanel { ... }
     };
     var result = await dialog.ShowAsync();
     ```
  3. Lines 667–679 (`ShowTaskDetailsDialogAsync`):
     ```csharp
     var dialog = new ContentDialog
     {
         Title = "Dettagli del Download",
         CloseButtonText = "Chiudi",
         DefaultButton = ContentDialogButton.Close,
         XamlRoot = Content.XamlRoot,
         Content = new ScrollViewer { ... }
     };
     await dialog.ShowAsync();
     ```
- **Finding**: None of these dialogs set `RequestedTheme` or check `this.Content is FrameworkElement root`. There is no centralized `PrepareDialog` method in `MainWindow.xaml.cs`. When the app switches to Dark mode (or starts in Dark mode), WinUI 3 dialogs can default to the OS system theme rather than matching the application's active theme.
- **CommandBar in `MainWindow.xaml` (Lines 147–157)**:
  ```xml
  <CommandBar Grid.Column="0" DefaultLabelPosition="Right" IsDynamicOverflowEnabled="True" Background="Transparent">
      <AppBarButton Icon="Add" Label="Nuovo Download" Click="OnAddDownloadClick" />
      <AppBarSeparator />
      <AppBarButton Icon="Play" Label="Riprendi" ... />
      <AppBarButton Icon="Pause" Label="Pausa" ... />
      <AppBarButton Icon="Delete" Label="Elimina" Click="OnDeleteButtonClick" />
  </CommandBar>
  ```
  There is currently no button for `📦 Download Batch`.

---

### 1.3 `FloatingDownloadWindow.xaml` & `FloatingDownloadWindow.xaml.cs` (Getly Mini)
- **Taskbar / Switcher Isolation**:
  - `FloatingDownloadWindow.xaml.cs:33`: `AppWindow.IsShownInSwitchers = false;` is called in the constructor. However, in `MainWindow.xaml.cs:196–212` (`ShowFloatingWindow`), `_floatingWindow.Activate()` is invoked without reasserting switcher invisibility, and no guard ensures `IsShownInSwitchers = false` remains in effect across window visibility cycles.
- **Top Row Metrics / Status Overlap (`FloatingDownloadWindow.xaml:87–109`)**:
  ```xml
  <Grid>
      <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*" />
          <ColumnDefinition Width="Auto" />
      </Grid.ColumnDefinitions>

      <StackPanel Grid.Column="0" x:Name="NormalSpeedPanel" Orientation="Horizontal" Spacing="4" VerticalAlignment="Center">
          <TextBlock x:Name="SpeedIconTextBlock" Text="⬇" ... />
          <TextBlock x:Name="SpeedTextBlock" Text="0 B/s" ... />
          <TextBlock Text="•" ... />
          <TextBlock Text="ETA:" ... />
          <TextBlock x:Name="EtaTextBlock" Text="--:--" ... />
      </StackPanel>

      <StackPanel Grid.Column="0" x:Name="ErrorSpeedPanel" Orientation="Horizontal" Spacing="4" VerticalAlignment="Center" Visibility="Collapsed">
          <FontIcon Glyph="&#xEB90;" FontSize="11" Foreground="#E81123" VerticalAlignment="Center" />
          <TextBlock x:Name="ErrorMessageTextBlock" Text="Connessione fallita - CDN" FontSize="11" FontWeight="Medium" Foreground="#E81123" TextTrimming="CharacterEllipsis" MaxLines="1" />
      </StackPanel>

      <TextBlock Grid.Column="1" x:Name="StatusBadgeTextBlock" Text="In download" FontSize="10" FontWeight="SemiBold" Foreground="#3B82F6" VerticalAlignment="Center" />
  </Grid>
  ```
  - **Finding**: Both `NormalSpeedPanel` and `ErrorSpeedPanel` are horizontal `StackPanel` elements inside `Column 0`. In WinUI 3 layout mechanics, a horizontal `StackPanel` provides infinite measure width to its children; therefore, a child `TextBlock` with `TextTrimming="CharacterEllipsis"` will **never** truncate. Furthermore, `ErrorMessageTextBlock` has no right margin (`Margin="0,0,12,0"` is missing), allowing long error texts to overflow directly into or collide with `StatusBadgeTextBlock` in Column 1.
- **Collection Naming & Window Height (`FloatingDownloadWindow.xaml.cs`)**:
  - Line 24: `public ObservableCollection<TaskItemViewModel> MonitoredTasks { get; } = new();`
    Requirement specifies: managing `ObservableCollection<TaskItemViewModel> ActiveTasks`.
  - Line 72 (`PositionWindow`):
    `int height = _isExpanded ? 340 : 165;`
    Requirement specifies: height transition from `175px` to `350px`.
  - Compact ListView Item Template (`FloatingDownloadWindow.xaml:207–208`):
    Line 208 displays `DownloadedFormatted` rather than file weight in MB ("peso in MB").
- **State Handling (`TaskItemViewModel.cs` & `FloatingDownloadWindow.xaml.cs`)**:
  - `TaskItemViewModel.cs:125`:
    ```csharp
    IsPendingConfirmation = string.Equals(dto.Status, "WAITING", StringComparison.OrdinalIgnoreCase)
                            || string.Equals(dto.Status, "PENDING_CONFIRMATION", StringComparison.OrdinalIgnoreCase);
    ```
    Status `"WaitingConfirmation"` or `"WAITING_CONFIRMATION"` is not explicitly matched.
  - `FloatingDownloadWindow.xaml:150–178` (`ConfirmationActionsPanel`) contains `Avvia Ora` (`StartNowButton`), `In Coda` (`EnqueueButton`), and `Annulla` (`CancelTaskButton`), and lines 112–122 (`DestinationRowGrid`) contain destination path with `Modifica` (`ChangeFolderButton`).

---

### 1.4 Settings View & MainViewModel
- **File**: `src/Getly.UI/Views/MainWindow.xaml` (Lines 419–520, `SettingsView`)
- **Storage Card**: Currently contains only `DefaultDownloadDir` (TextBox + Browse + Open Folder) and `MaxConcurrentConnections` (NumberBox).
- **Missing Controls**:
  - `ToggleSwitch` `RequireConfirmation` (default: `false`)
  - `NumberBox` `ConfirmationThresholdMB` (default: `100`, Min: `0`, Max: `100000`)
  - `ToggleSwitch` `AutoCategorize` (default: `true`)
- **MainViewModel (`src/Getly.UI/ViewModels/MainViewModel.cs`)**:
  - Lines 37–54: Missing properties for `RequireConfirmation`, `ConfirmationThresholdMB`, and `AutoCategorize`.
  - Lines 509–524 (`SaveSettingsAsync`):
    ```csharp
    public async Task SaveSettingsAsync()
    {
        try {
            if (GlobalSpeedLimitBps >= 0)
                await _ipcClient.SetSpeedLimitAsync(GlobalSpeedLimitBps);
            StatusMessage = "Impostazioni applicate con successo.";
        } ...
    }
    ```
    Settings are neither persisted to disk (e.g. `%LOCALAPPDATA%\Getly\settings.json`) nor synchronized with the Python backend via IPC.

---

### 1.5 Batch Download (`+ Download Batch`)
- **Current State**: Neither `MainWindow.xaml` nor `MainWindow.xaml.cs` contains the `+ Download Batch` button or dialog logic.
- **Required Pattern**:
  - Multiline `TextBox` for pasting URL lists / raw text.
  - "Analizza URL" button using regex: `@"(https?://[^\s"'<>]+|magnet:\?[^\s"'<>]+)"`.
  - Parsed list with `CheckBox`, deduced filename, category badge, and URL.
  - Buttons: "Avvia Selezionati", "Aggiungi in Coda", "Annulla".

---

## 2. Logic Chain

1. **Theme Inconsistency on ContentDialogs**:
   - *Observation*: `MainWindow.xaml.cs` lines 357, 566, and 667 show `ContentDialog` instantiation directly setting `XamlRoot = Content.XamlRoot`, but never setting `RequestedTheme = root.ActualTheme`.
   - *Deduction*: When an application root has `RequestedTheme = ElementTheme.Dark`, WinUI 3 `ContentDialog` roots do not automatically inherit this unless attached via `RequestedTheme = root.ActualTheme`.
   - *Inference*: A centralized `PrepareDialog(ContentDialog dialog)` method must be introduced and called before every `ShowAsync()`.

2. **FloatingDownloadWindow Text Truncation Bug**:
   - *Observation*: `FloatingDownloadWindow.xaml:103–106` embeds `ErrorMessageTextBlock` with `TextTrimming="CharacterEllipsis"` inside a horizontal `StackPanel` in Column 0, without `Margin="0,0,12,0"`.
   - *Deduction*: In XAML, `StackPanel` measure pass allocates unbounded width along its orientation axis. As a result, `CharacterEllipsis` is disabled or inactive, causing text overflow that overlaps `StatusBadgeTextBlock` in Column 1.
   - *Inference*: Column 0 must constrain width (e.g. via direct Grid column binding or grid container) and enforce `Margin="0,0,12,0"` with `TextTrimming="CharacterEllipsis"` and `MaxLines="1"`.

3. **Floating Window Drawer & Navigation Specs**:
   - *Observation*: `FloatingDownloadWindow.xaml.cs:24` uses `MonitoredTasks` and line 72 uses heights `165` and `340`.
   - *Deduction*: Requirement R1 explicitly specifies `ObservableCollection<TaskItemViewModel> ActiveTasks` and window heights transitioning from `175px` (collapsed) to `350px` (expanded).
   - *Inference*: Renaming/aliasing the collection to `ActiveTasks` and adjusting `PositionWindow` to `175` / `350` ensures strict adherence to requirements and clean UI layout.

4. **Settings Persistence & IPC Synchronization Gap**:
   - *Observation*: `MainViewModel.cs:509–524` only sends `system.set_speed_limit` and does not persist `DefaultDownloadDir`, `RequireConfirmation`, `ConfirmationThresholdMB`, or `AutoCategorize`.
   - *Deduction*: Without disk persistence, user configurations are lost when the UI process closes. Without IPC synchronization, the Python engine (`QueueManager`) cannot know whether confirmation is required or whether to auto-categorize.
   - *Inference*: `MainViewModel` must save settings locally (`%LOCALAPPDATA%\Getly\settings.json`) and dispatch a configuration synchronization call over IPC (e.g. `system.set_settings`).

5. **Batch Download Workflow Integration**:
   - *Observation*: `MainWindow.xaml` command bar has room next to `OnAddDownloadClick` (line 152).
   - *Deduction*: Adding `📦 Download Batch` button invoking a `PrepareDialog`-tematized `ContentDialog` allows users to parse multiple links via Regex and enqueue or start them in bulk.
   - *Inference*: A dedicated dialog flow with URL regex extraction, category classification, and batch dispatch to `MainViewModel` meets R1 completely.

---

## 3. Caveats

- **No Caveats on Architecture**: The existing architecture (WinUI 3 MVVM with CommunityToolkit + Named Pipe IPC to Python headless daemon) is clean, stable, and compiles cleanly with 0 errors.
- **Settings IPC Protocol Assumption**: The Python backend's `NamedPipeServer` currently exposes `system.set_speed_limit` and `system.toggle_adapter`. For full two-way synchronization of `RequireConfirmation`, `ConfirmationThresholdMB`, and `AutoCategorize`, a matching `system.set_settings` RPC handler in `core/ipc/pipe_server.py` should be implemented during backend workstream R2.
- **Windows Switcher Behavior**: WinUI 3 Windowing presenter changes can sometimes reset `IsShownInSwitchers`; reasserting `AppWindow.IsShownInSwitchers = false;` in `FloatingDownloadWindow` whenever visibility or presenter changes guarantees isolation.

---

## 4. Conclusion & Proposed Implementation Blueprint

All findings for requirement R1 have been localized with exact file locations and line numbers. Below are the precise proposed code changes:

### 4.1 `MainWindow.xaml.cs` — `PrepareDialog` & ContentDialog Theme Alignment
Add helper method:
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
Invoke `PrepareDialog(dialog)` in:
- `OnDeleteButtonClick` (before line 379)
- `OnAddDownloadClick` (before line 588)
- `ShowTaskDetailsDialogAsync` (before line 679)
- `OnBatchDownloadClick` (new batch dialog)

### 4.2 `FloatingDownloadWindow.xaml` & `.xaml.cs` — Layout & ActiveTasks
1. **In `FloatingDownloadWindow.xaml` (Row 1, Metrics/Status Grid)**:
   ```xml
   <Grid>
       <Grid.ColumnDefinitions>
           <ColumnDefinition Width="*" />
           <ColumnDefinition Width="Auto" />
       </Grid.ColumnDefinitions>

       <!-- Left column: speed or error message with CharacterEllipsis and Margin 0,0,12,0 -->
       <Grid Grid.Column="0" Margin="0,0,12,0" VerticalAlignment="Center">
           <!-- Live Speed & ETA (Normal) -->
           <StackPanel x:Name="NormalSpeedPanel" Orientation="Horizontal" Spacing="4" VerticalAlignment="Center">
               <TextBlock x:Name="SpeedIconTextBlock" Text="⬇" FontSize="11" Foreground="#60A5FA" />
               <TextBlock x:Name="SpeedTextBlock" Text="0 B/s" FontSize="11" FontWeight="Medium" Foreground="{ThemeResource TextFillColorPrimaryBrush}" />
               <TextBlock Text="•" FontSize="11" Foreground="{ThemeResource TextFillColorTertiaryBrush}" Margin="2,0,2,0" />
               <TextBlock Text="ETA:" FontSize="11" Foreground="{ThemeResource TextFillColorSecondaryBrush}" />
               <TextBlock x:Name="EtaTextBlock" Text="--:--" FontSize="11" FontWeight="Medium" Foreground="{ThemeResource TextFillColorPrimaryBrush}" />
           </StackPanel>

           <!-- Error Message Panel -->
           <Grid x:Name="ErrorSpeedPanel" Visibility="Collapsed">
               <Grid.ColumnDefinitions>
                   <ColumnDefinition Width="Auto" />
                   <ColumnDefinition Width="*" />
               </Grid.ColumnDefinitions>
               <FontIcon Grid.Column="0" Glyph="&#xEB90;" FontSize="11" Foreground="#E81123" VerticalAlignment="Center" Margin="0,0,4,0" />
               <TextBlock Grid.Column="1" x:Name="ErrorMessageTextBlock" Text="Connessione fallita - CDN" FontSize="11" FontWeight="Medium" Foreground="#E81123" TextTrimming="CharacterEllipsis" MaxLines="1" VerticalAlignment="Center" />
           </Grid>
       </Grid>

       <TextBlock Grid.Column="1" x:Name="StatusBadgeTextBlock" Text="In download" FontSize="10" FontWeight="SemiBold" Foreground="#3B82F6" VerticalAlignment="Center" />
   </Grid>
   ```
2. **In `FloatingDownloadWindow.xaml.cs`**:
   - Change collection property:
     ```csharp
     public ObservableCollection<TaskItemViewModel> ActiveTasks { get; } = new();
     public ObservableCollection<TaskItemViewModel> MonitoredTasks => ActiveTasks; // backward-compat alias
     ```
   - Update `TasksListView.ItemsSource = ActiveTasks;`
   - In `PositionWindow()`:
     ```csharp
     int width = 380;
     int height = _isExpanded ? 350 : 175;
     AppWindow.Resize(new SizeInt32(width, height));
     ```
   - In `TaskItemViewModel.cs`:
     Update `IsPendingConfirmation` check to include `"WAITING_CONFIRMATION"` and `"WaitingConfirmation"`.

### 4.3 `MainWindow.xaml` & `MainViewModel.cs` — Settings View & Persistence
1. **In `MainWindow.xaml` (`SettingsView` under "Download & Percorsi")**:
   ```xml
   <Border Height="1" Background="{ThemeResource CardStrokeColorDefaultBrush}" />

   <ToggleSwitch
       Header="Richiedi conferma prima di avviare il download (WaitingConfirmation)"
       IsOn="{x:Bind ViewModel.RequireConfirmation, Mode=TwoWay}" />

   <StackPanel Spacing="6">
       <TextBlock Text="Soglia dimensione per richiesta conferma (MB)" FontSize="12" FontWeight="Medium" Foreground="{ThemeResource TextFillColorSecondaryBrush}" />
       <StackPanel Orientation="Horizontal" Spacing="12">
           <NumberBox
               Value="{x:Bind ViewModel.ConfirmationThresholdMB, Mode=TwoWay}"
               Minimum="0"
               Maximum="100000"
               SpinButtonPlacementMode="Inline"
               SmallChange="10"
               LargeChange="100"
               Width="160" />
           <TextBlock Text="I download superiori a questa soglia richiederanno conferma manuale." FontSize="11" Foreground="{ThemeResource TextFillColorTertiaryBrush}" VerticalAlignment="Center" />
       </StackPanel>
   </StackPanel>

   <Border Height="1" Background="{ThemeResource CardStrokeColorDefaultBrush}" />

   <ToggleSwitch
       Header="Smista automaticamente i download in sottocartelle per categoria (Video, Compressi, Documenti, ecc.)"
       IsOn="{x:Bind ViewModel.AutoCategorize, Mode=TwoWay}" />
   ```
2. **In `MainViewModel.cs`**:
   - Add observable properties `RequireConfirmation` (default `false`), `ConfirmationThresholdMB` (default `100`), `AutoCategorize` (default `true`).
   - Add local JSON file persistence in `%LOCALAPPDATA%\Getly\settings.json`.
   - In `SaveSettingsAsync`: serialize to `settings.json` and sync via `IpcClient`.

### 4.4 `MainWindow.xaml` & `MainWindow.xaml.cs` — Batch Download Dialog
1. **In `MainWindow.xaml` (`CommandBar`)**:
   ```xml
   <AppBarButton Icon="Add" Label="Nuovo Download" Click="OnAddDownloadClick" />
   <AppBarButton Icon="AllApps" Label="📦 Download Batch" Click="OnBatchDownloadClick" ToolTipService.ToolTip="Aggiungi una lista di download da testo o link" />
   <AppBarSeparator />
   ```
2. **In `MainWindow.xaml.cs`**:
   Implement `OnBatchDownloadClick` showing a `PrepareDialog`-tematized `ContentDialog` with Regex parser (`https?://[^\s"'<>]+|magnet:\?[^\s"'<>]+`), interactive checkboxes, deduced filenames, category badges, and bulk enqueue/start actions.

---

## 5. Verification Method

### 5.1 Compilation Verification
Run from repository root:
```powershell
dotnet build src/Getly.UI -c Debug -r win-x64
```
*Expected*: Exit code 0, 0 warnings, 0 errors.

### 5.2 Unit Test Verification
Run from repository root:
```powershell
dotnet test tests/Getly.UI.Tests -r win-x64
```
*Expected*: All unit tests pass without errors or timeouts.

### 5.3 Invalidation Conditions
- Any `ContentDialog` in `MainWindow.xaml.cs` shown without `PrepareDialog(dialog)`.
- Long error text in `FloatingDownloadWindow` pushing `StatusBadgeTextBlock` offscreen or wrapping onto it.
- `FloatingDownloadWindow` window height differing from `175px` (collapsed) or `350px` (expanded).
- Toggling `RequireConfirmation`, `ConfirmationThresholdMB`, or `AutoCategorize` not persisting across app restarts.
