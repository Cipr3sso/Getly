# Project: Getly Multi-Tier Overhaul

## Architecture
Getly is a high-performance download manager composed of:
1. **Core Python Daemon (`core/`)**: Headless download engine using `aiohttp` and `curl_cffi` with multi-connection chunking, multi-interface socket binding (`IP_UNICASTIF`), local HTTP server (`http://127.0.0.1:9666`), named-pipe IPC server (`\\.\pipe\getly_ipc`), and SQLite state store.
2. **WinUI 3 Frontend (`src/Getly.UI`)**: Modern Windows desktop UI built with Windows App SDK 1.5, .NET 8, and CommunityToolkit.Mvvm. Communicates with Python daemon via named pipe IPC. Includes `MainWindow` and `FloatingDownloadWindow` (Getly Mini).
3. **Browser Extension (`extensions/chrome/`)**: Manifest V3 extension for Chrome/Edge with download interception, media sniffing, and page link grabbing. Communicates with local daemon via HTTP REST API (`http://127.0.0.1:9666`).

## Code Layout
- `core/engine/probe.py`: URL probing, MIME/content-length detection, browser header generation.
- `core/engine/downloader.py`: Multi-chunk download coordination, fallback to single-stream sequential download.
- `core/engine/worker.py`: Per-chunk downloading via aiohttp and curl_cffi, 429 exponential backoff, partial resume.
- `core/network/session_pool.py`: aiohttp and curl_cffi session factories, socket binding.
- `core/api/local_server.py`: Local REST API (`/health`, `/jdcheck.js`, `/flashgot`, `/add`, `/add_batch`).
- `core/scheduler/queue_manager.py`: Task queue management, category subfolders, confirmation thresholds.
- `core/ipc/pipe_server.py`: Named pipe IPC server handling RPC commands from WinUI 3.
- `src/Getly.UI/Views/MainWindow.xaml`: Main window, CommandBar, SettingsView, download lists.
- `src/Getly.UI/Views/MainWindow.xaml.cs`: Dialog management (`PrepareDialog`), command bar event handlers, batch dialog.
- `src/Getly.UI/Views/FloatingDownloadWindow.xaml`: Getly Mini overlay, top metrics row, drawer ListView, confirmation footer.
- `src/Getly.UI/Views/FloatingDownloadWindow.xaml.cs`: Switcher isolation (`IsShownInSwitchers = false`), `ActiveTasks` management, 175px/350px resizing.
- `src/Getly.UI/ViewModels/MainViewModel.cs`: Core ViewModel, settings persistence (`settings.json`), IPC synchronization.
- `src/Getly.UI/ViewModels/TaskItemViewModel.cs`: Item ViewModel, `WaitingConfirmation` status binding.
- `extensions/chrome/manifest.json`: Manifest V3 config with permissions (`scripting`, `cookies`, `activeTab`, etc.).
- `extensions/chrome/background.js`: Service worker, download interception, clean browser headers, batch dispatch.
- `extensions/chrome/popup.html`: Extension popup UI with link grabber section and category filter bar.
- `extensions/chrome/popup.css`: Styling for popup, category pills, grabber item list.
- `extensions/chrome/popup.js`: Script injection (`chrome.scripting.executeScript`), link extraction and categorization, batch message dispatch.
- `tests/`: Pytest test suite for Python core.
- `tests/Getly.UI.Tests/`: xUnit test suite for WinUI 3 ViewModels and components.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Centralized `PrepareDialog` | Sets `XamlRoot` and `RequestedTheme` on all `ContentDialog` instances in `MainWindow.xaml.cs` for dark theme alignment | M3 | R1 |
| 2 | Switcher isolation in Getly Mini | Sets `AppWindow.IsShownInSwitchers = false;` in `FloatingDownloadWindow` to eliminate duplicate taskbar icons | M3 | R1 |
| 3 | Ellipsis & margin on Getly Mini error | Uses 2-column Grid with `TextTrimming="CharacterEllipsis"`, `MaxLines="1"`, `Margin="0,0,12,0"` on left TextBlock | M3 | R1 |
| 4 | Multi-task navigation & expandable drawer | Manages `ObservableCollection<TaskItemViewModel> ActiveTasks`, `◀ 1 / N ▶` paging, `▼` button toggling 175px to 350px height with compact checkbox list | M3 | R1 |
| 5 | `WaitingConfirmation` state handling | Pre-download confirmation footer with `Avvia Ora`, `In Coda`, `Annulla`, and destination path with `Modifica` | M3 | R1 |
| 6 | Confirmation threshold & auto-categorization settings | `RequireConfirmation` (toggle), `ConfirmationThresholdMB` (numberbox), `AutoCategorize` (toggle) in `SettingsView`, persisted & synced over IPC | M3 | R1 |
| 7 | Batch Download Dialog (`+ Download Batch`) | CommandBar button opening dialog with multiline URL input, regex parser for http/https/magnet, preview list with checkboxes and category badges, bulk actions | M3 | R1 |
| 8 | Header sanitization & anti-bot consistency | Remove fake `Sec-Ch-Ua` (v="124"/v="125") when real client headers provided; strip conflicting `User-Agent`/`Sec-Ch-Ua` in `curl_cffi` fallback while preserving `Cookie` & `Referer` | M1 | R2 |
| 9 | Robust `curl_cffi` error checking in worker | Use `stream=True, allow_redirects=True`, validate `if resp.status_code >= 400: raise DownloadError(...)` before `resp.aiter_content()` | M1 | R2 |
| 10 | Cloudflare 403 Range fallback to single connection | Downgrade multi-connection Range failures to 1 single sequential connection without Range header to complete download | M1 | R2 |
| 11 | Real error message preservation on 0-byte failure | Store actual worker exception (e.g. `"HTTP 403 Forbidden (Cloudflare WAF)"`) in `task.error_message` instead of generic `"Download ended incompletely"` | M1 | R2 |
| 12 | `POST /add_batch` API Endpoint | Endpoint in `local_server.py` accepting `{ "items": [...], "auto_start": true }`, routing to `QueueManager` with subfolder categorization & confirmation threshold | M1 | R2 |
| 13 | `"scripting"` permission in Chrome extension | Declare `"scripting"` in `extensions/chrome/manifest.json` | M2 | R3 |
| 14 | Real headers in Chrome extension | Remove hardcoded `Sec-Ch-Ua` from `background.js`, transmit real `User-Agent`, real `Referer`, and full `Cookie` via `extractFullCookieHeader` | M2 | R3 |
| 15 | Page Link Grabber | Extension popup button executing `chrome.scripting.executeScript` to extract links from `<a>`, `<video>`, `<audio>`, `<source>` | M2 | R3 |
| 16 | File extension & category filtering | Classify extracted links into Tutti, Archivi, Documenti, Video, Programmi with category pills and checkboxes | M2 | R3 |
| 17 | Batch dispatch from Extension to Core | Button `"⬇️ Scarica selezionati con Getly"` sending batch payload to `POST http://127.0.0.1:9666/add_batch` | M2 | R3 |
| 18 | Automated test suite execution & new test cases | Run `pytest tests/ -v` (including new tests for `/add_batch` and 403 downgrade) and `dotnet test tests/Getly.UI.Tests -r win-x64` | M4 | R4 |
| 19 | WinUI 3 0-warning clean build | Run `dotnet build src/Getly.UI -c Debug -r win-x64` ensuring 0 warnings and 0 errors | M4 | R4 |
| 20 | Git commit and push | Commit all validated changes and push to `origin/main` | M4 | R4 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Python Core & Batch API | Features 8, 9, 10, 11, 12: `core/probe.py`, `core/downloader.py`, `core/worker.py`, `core/session_pool.py`, `core/api/local_server.py`, `core/scheduler/queue_manager.py`, `core/ipc/pipe_server.py`, new pytest cases | none | DONE |
| M2 | Chrome Extension | Features 13, 14, 15, 16, 17: `extensions/chrome/manifest.json`, `background.js`, `popup.html`, `popup.css`, `popup.js` | M1 | DONE |
| M3 | WinUI 3 Frontend | Features 1, 2, 3, 4, 5, 6, 7: `src/Getly.UI/Views/MainWindow.xaml`, `MainWindow.xaml.cs`, `FloatingDownloadWindow.xaml`, `FloatingDownloadWindow.xaml.cs`, `ViewModels/MainViewModel.cs`, `TaskItemViewModel.cs` | M1 | DONE |
| M4 | Integration Verification & Git Release | Features 18, 19, 20: full pytest execution, WinUI 3 build and test execution, git commit & push to origin/main | M1, M2, M3 | DONE |

## Interface Contracts

### Local HTTP Server (`core/api/local_server.py`) ↔ Chrome Extension (`extensions/chrome/background.js`)
- **Endpoint**: `POST /add_batch`
- **Request Body**:
  ```json
  {
    "items": [
      {
        "url": "https://example.com/file.zip",
        "filename": "file.zip",
        "file_size": 1048576,
        "headers": {
          "Referer": "https://example.com/",
          "User-Agent": "Mozilla/5.0 ...",
          "Cookie": "cf_clearance=...; session=..."
        },
        "category": "Archivi"
      }
    ],
    "auto_start": true
  }
  ```
- **Response**: `200 OK`
  ```json
  {
    "success": true,
    "count": 1,
    "tasks": [
      {
        "task_id": "...",
        "url": "https://example.com/file.zip",
        "filename": "file.zip",
        "status": "DOWNLOADING"
      }
    ]
  }
  ```

### Named Pipe IPC Server (`core/ipc/pipe_server.py`) ↔ WinUI 3 (`MainViewModel.cs`)
- **Method**: `system.set_settings`
- **Params**:
  ```json
  {
    "require_confirmation": false,
    "confirmation_threshold_mb": 100,
    "auto_categorize": true
  }
  ```
- **Response**: `{"status": "ok"}`
- **Task DTO Status**:
  - `status`: `"WAITING"` or `"WAITING_CONFIRMATION"` mapped in `TaskItemViewModel.IsPendingConfirmation = true`.
