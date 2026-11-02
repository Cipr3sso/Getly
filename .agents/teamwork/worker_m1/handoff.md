# Handoff Report: Milestone M1 — Python Core & Batch API

## 1. Observation

All requirements specified in `DISPATCH.md` for Milestone M1 were directly investigated, implemented, and verified in the codebase.

### 1.1 Source Code Changes
- **`core/engine/probe.py`**:
  - Added `sanitize_headers_for_impersonation(headers: Optional[dict]) -> dict`: strips discordant client hints (`Sec-Ch-Ua`, `Sec-Ch-Ua-Mobile`, `Sec-Ch-Ua-Platform`) and `User-Agent` that conflict with `curl_cffi` TLS impersonation fingerprints, while strictly preserving `Cookie` (including `cf_clearance`), `Referer`, and `Range`.
  - In `get_browser_headers()` (lines 60-75): when authentic `custom_headers` are passed, suppresses injection of hardcoded `"Chromium";v="125"` client hints.
  - In `_probe_curl_cffi()` (lines 208-220): updated session initialization to `CurlAsyncSession(impersonate="chrome")`, added `sanitize_headers_for_impersonation`, and added `allow_redirects=True`.
- **`core/engine/downloader.py`**:
  - Defined `DownloadError(Exception)`.
  - Added `self.error_message: Optional[str] = None` attribute to `DownloadEngine`.
  - In `_download_single_stream()` (lines 318-385): strips the `Range` header (`single_headers.pop("Range", None)`) to ensure clean sequential downloads without Range rejection.
  - Added `_download_single_stream_curl(self, filepath: Path) -> bool`: sequential single-stream download using `CurlAsyncSession(impersonate="chrome")` as a fallback when aiohttp single-stream receives HTTP 401 or 403.
  - In `start()` (lines 260-280): collects worker exceptions on incomplete multi-chunk runs, and records `self.error_message` when 0 bytes are transferred.
  - Used `TYPE_CHECKING` and deferred import of `ChunkWorker` in `start()` to eliminate circular imports with `worker.py`.
- **`core/engine/worker.py`**:
  - Imported `DownloadError` and `sanitize_headers_for_impersonation`.
  - Added `self.last_error: Optional[str] = None` to `ChunkWorker`.
  - In `_download_chunk()`: updated `CurlAsyncSession(impersonate="chrome")`, used `req_headers = sanitize_headers_for_impersonation(headers)`, used `allow_redirects=True`, and validated `if curl_resp.status_code >= 400:` raising `DownloadError` with the status code and reason.
  - Recorded specific error strings on HTTP 401, 403, 429, 5xx, and exceptions in `self.last_error`.
- **`core/engine/__init__.py`**:
  - Re-exported `DownloadError` in `__all__`.
- **`core/network/session_pool.py`**:
  - Updated `create_impersonated_session()` default parameter to `impersonate: str = "chrome"`.
- **`core/scheduler/categories.py`**:
  - Added `CATEGORY_ALIASES` mapping (`"archivi"`, `"compressi"`, `"programmi"`, etc.) and updated `get_category_subfolder()` to resolve aliases dynamically.
- **`core/scheduler/queue_manager.py`**:
  - Added settings properties to `QueueManager`:
    - `require_confirmation: bool = False`
    - `confirmation_threshold_mb: int = 100`
    - `auto_categorize: bool = True`
  - In `add_url()`: normalized category aliases, routed to category subfolders only when `auto_categorize` is True, and set `DownloadStatus.WAITING` when `auto_start is False` or when `require_confirmation is True` or file size exceeds `confirmation_threshold_mb * 1024 * 1024`.
  - In `_run_task()` (line 563): set `task.error_message = getattr(engine, "error_message", None) or "Download ended incompletely"` to preserve genuine HTTP/WAF exceptions (e.g. `"HTTP 403 Forbidden (Cloudflare WAF)"`) on 0-byte failures.
- **`core/api/local_server.py`**:
  - Updated `LocalHttpServer.__init__` to accept `queue_manager` and `on_batch_requested`.
  - Added route `POST /add_batch` and handler `_handle_add_batch()`: accepts `{ "items": [...], "auto_start": bool }`, dispatches to `queue_manager.add_url()` (or `on_download_requested`), and returns `{ "success": True, "count": len(tasks), "tasks": [...] }`.
- **`core/ipc/pipe_server.py`**:
  - Registered `system.set_settings` RPC handler in `_router` updating `require_confirmation`, `confirmation_threshold_mb`, and `auto_categorize` on `QueueManager`.
  - Updated `_rpc_system_get_stats()` to expose current settings values.
- **`engine_service.py`**:
  - Passed `queue_manager=queue_manager` to `LocalHttpServer`.
- **`tests/test_local_server.py`**:
  - Added `test_local_server_add_batch_on_download_requested`: verifies `/add_batch` with callback, empty list handling, and validation errors.
  - Added `test_local_server_add_batch_with_queue_manager`: verifies `/add_batch` with QueueManager, category routing, and confirmation threshold (>100MB) triggering `WAITING` status.
- **`tests/test_milestone_m1.py`**:
  - Created 6 unit/integration tests:
    - `test_sanitize_headers_for_impersonation`
    - `test_get_browser_headers_suppresses_hardcoded_sec_ch_ua`
    - `test_single_stream_downgrade_strips_range_header`
    - `test_error_message_preservation_on_zero_bytes`
    - `test_named_pipe_system_set_settings`
    - `test_worker_curl_cffi_download_error_handling`

### 1.2 Build & Test Verification Results
- **Command**: `C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v`
- **Output**:
  ```
  169 passed, 16 warnings in 61.14s (0:01:01)
  ```
- **Exit Code**: 0 (All 161 existing tests + 8 new tests passed with 0 failures).

---

## 2. Logic Chain

1. **Header Alignment & Cloudflare WAF Bypass**:
   - Cloudflare challenge mechanisms inspect both the TLS fingerprint and the HTTP headers (specifically `User-Agent` and Client Hints `Sec-Ch-Ua*`).
   - When authentic browser headers are intercepted, hardcoded Chromium hints (`v="125"`) created discrepancies on non-Chrome or differing version User-Agents. Suppressing hardcoded `Sec-Ch-Ua` when custom headers are provided resolves this.
   - When falling back to `curl_cffi`, stripping discordant `User-Agent` and `Sec-Ch-Ua*` allows `curl_cffi` to emit TLS-coherent headers matching its Chrome profile, while preserving `Cookie` (including `cf_clearance`) and `Referer`.
2. **Single-Stream Sequential Fallback**:
   - Multi-connection Range requests to CDN edge servers can be rejected with 403 or 429 when range slicing is disallowed or challenged.
   - Downgrading to a single-stream sequential connection and stripping the `Range` header allows the download to proceed from byte 0 to completion without triggering CDN range blocks.
   - If `aiohttp` is blocked on single-stream GET with 403, delegating to `curl_cffi` with `impersonate="chrome"` succeeds through Cloudflare WAF.
3. **Error Preservation**:
   - Hardcoding `"Download ended incompletely"` masked the underlying reason when 0 bytes were downloaded. Storing the actual exception raised by workers or single-stream GET on `engine.error_message` and assigning it in `queue_manager.py` ensures users and logs receive the actual reason (e.g. `"HTTP 403 Forbidden (Cloudflare WAF)"`).
4. **Batch API Endpoint & Auto-Categorization**:
   - `POST /add_batch` parses batch payloads and invokes `queue_manager.add_url()` for each item.
   - `auto_categorize` routes files into category subdirectories (`Compressi`, `Video`, `Programmi`, etc.), supporting user aliases such as `"Archivi"`.
   - Threshold checking marks tasks exceeding `confirmation_threshold_mb` as `DownloadStatus.WAITING`, allowing the UI or client to prompt the user before starting heavy downloads.

---

## 3. Caveats

- `curl_cffi` remains an optional dependency (`HAVE_CURL_CFFI = True/False`). If `curl_cffi` is unavailable in an environment, the engine will propagate standard aiohttp error messages gracefully.
- When `require_confirmation` is triggered or `auto_start` is False, tasks enter `WAITING` status and will not consume download bandwidth until resumed via IPC or UI.

---

## 4. Conclusion

Milestone M1 (Python Core & Batch API) is completely implemented according to specifications and interface contracts in `PROJECT.md` and user rules in `AGENTS.md`. All unit and integration tests pass cleanly with 100% success rate (169 passed, 0 failed).

---

## 5. Verification Method

To independently verify this milestone:

1. **Run full Python test suite**:
   ```powershell
   C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v
   ```
   *Expected result*: 169 passed, 0 failed.

2. **Run dedicated M1 tests**:
   ```powershell
   C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/test_milestone_m1.py tests/test_local_server.py -v
   ```
   *Expected result*: 10 passed, 0 failed.

3. **Inspect modified files**:
   - `core/engine/probe.py`
   - `core/engine/downloader.py`
   - `core/engine/worker.py`
   - `core/network/session_pool.py`
   - `core/scheduler/queue_manager.py`
   - `core/scheduler/categories.py`
   - `core/api/local_server.py`
   - `core/ipc/pipe_server.py`
