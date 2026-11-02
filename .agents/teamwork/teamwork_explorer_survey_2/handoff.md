# Survey Report: Python Backend (Getly Core & Tests)

## 1. Observation

Direct observations from source inspection and execution in the workspace:

### 1.1 Baseline Test Suite Execution
- **Command**: `C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/`
- **Result**: `161 passed, 14 warnings in 65.48s (0:01:05)`.
- All existing 161 unit/integration tests are currently passing on Python 3.10.11 (win32).

### 1.2 Anti-Bot & Header Handling (`probe.py`, `downloader.py`, `worker.py`, `session_pool.py`)
- **`core/engine/probe.py`**:
  - Lines 48-68:
    ```python
    if not effective_ua:
        effective_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ...
    is_chromium_desktop = "Chrome" in effective_ua and "Windows" in effective_ua and not is_googlevideo
    if is_chromium_desktop:
        headers.update({
            "Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        })
    ```
    `get_browser_headers()` injects hardcoded `"Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"'` whenever `is_chromium_desktop` is true, even when authentic browser headers are supplied via `custom_headers` or `user_agent` (unless `custom_headers` explicitly overrides `Sec-Ch-Ua`).
  - Line 192 in `_probe_curl_cffi()`:
    ```python
    async with CurlAsyncSession(impersonate="chrome124") as curl_session:
        curl_resp = await curl_session.get(target_url, headers=cf_headers, stream=True)
    ```
    Uses hardcoded `impersonate="chrome124"` instead of `impersonate="chrome"`, and passes `cf_headers` without filtering conflicting `User-Agent` and `Sec-Ch-Ua` headers.
- **`core/network/session_pool.py`**:
  - Line 149:
    ```python
    def create_impersonated_session(self, impersonate: str = "chrome124") -> Optional[Any]:
        if HAVE_CURL_CFFI:
            return CurlAsyncSession(impersonate=impersonate)
    ```
    Defaults to `"chrome124"` instead of `"chrome"`.
- **`core/engine/worker.py`**:
  - Lines 203-207:
    ```python
    if HAVE_CURL_CFFI:
        try:
            async with CurlAsyncSession(impersonate="chrome124") as curl_session:
                curl_resp = await curl_session.get(self.url, headers=headers, stream=True)
                if curl_resp.status_code in (200, 206):
    ```
    - Uses `impersonate="chrome124"` instead of `"chrome"`.
    - Omits `allow_redirects=True`.
    - Does NOT validate `if curl_resp.status_code >= 400: raise DownloadError(...)` before checking status or iterating content.
    - Does not strip conflicting `User-Agent` or `Sec-Ch-Ua` from `headers`, risking WAF fingerprint mismatch.
    - Class `DownloadError` is not defined anywhere in the repository.

### 1.3 Cloudflare Range Rejection & Fallback Downgrade (`downloader.py`, `queue_manager.py`)
- **`core/engine/downloader.py`**:
  - Lines 246-255:
    ```python
    else:
        if self._is_cancelled:
            return False
        logger.warning(
            "Multi-chunk download ended with incomplete chunks for %s. Automatically falling back to single-stream sequential download.",
            filepath.name,
        )
        if self.storage and self.storage.is_open:
            await self.storage.flush()
        return await self._download_single_stream(primary_session, filepath)
    ```
  - Lines 305-327 (`_download_single_stream`):
    ```python
    if filepath.exists() and not is_preallocated:
        try:
            curr_size = filepath.stat().st_size
            if curr_size > 0 and self.info and self.info.supports_ranges and self.info.total_size > 0:
                if curr_size < self.info.total_size:
                    existing_offset = curr_size
                    single_headers["Range"] = f"bytes={existing_offset}-"
                    logger.info("Resuming single-stream from byte %d for %s", existing_offset, filepath.name)
    ...
    async with session.get(self.url, headers=single_headers) as resp:
        if resp.status not in (200, 206):
            logger.error("Single-stream download failed with HTTP %d", resp.status)
            return False
    ```
    - When downgraded due to Range failure, `single_headers` can still contain `Range: bytes={existing_offset}-`, causing CDNs/Cloudflare to reject the single-stream request again.
    - `_download_single_stream` only uses standard `aiohttp.ClientSession`; if Cloudflare WAF blocks aiohttp with 403, it does not fall back to `curl_cffi` single-stream GET.
    - `DownloadEngine` does not record `self.error_message` or capture worker exceptions when downloads fail.
- **`core/scheduler/queue_manager.py`**:
  - Lines 547-551:
    ```python
    else:
        task.status = DownloadStatus.FAILED
        task.error_message = "Download ended incompletely"
        self.state_store.update_task_status(task.task_id, DownloadStatus.FAILED, task.error_message)
        logger.warning("Task %s failed", task.task_id)
    ```
    Regardless of the underlying HTTP error (e.g. 403 Forbidden Cloudflare WAF, 429 Too Many Requests), the task error is hardcoded to `"Download ended incompletely"`.

### 1.4 Batch API (`core/api/local_server.py`) & QueueManager Settings
- **`core/api/local_server.py`**:
  - Lines 27-30:
    ```python
    self._app.router.add_get("/health", self._handle_health)
    self._app.router.add_post("/add", self._handle_add)
    self._app.router.add_get("/jdcheck.js", self._handle_jdcheck)
    self._app.router.add_post("/flashgot", self._handle_flashgot)
    ```
    There is no `/add_batch` endpoint.
- **`core/scheduler/queue_manager.py`**:
  - Lines 194-239:
    `QueueManager.add_url()` routes to category subfolders (`get_category_subfolder`) if `destination_dir` is default.
    However, `QueueManager` currently lacks explicit properties for:
    - `require_confirmation: bool` (default `False`)
    - `confirmation_threshold_mb: int` (default `100`)
    - `auto_categorize: bool` (default `True`)
  - Lines 205-211:
    `auto_start=False` sets `DownloadStatus.WAITING`. However, size-based confirmation threshold checking (`parsed_total_size > confirmation_threshold_mb * 1024 * 1024`) is not yet applied when adding URLs.

### 1.5 Network Resilience Rules (AGENTS.md Compliance)
- **Winsock `IP_UNICASTIF`**: Verified in `core/network/socket_binder.py:34-41` (`sock.setsockopt(socket.IPPROTO_IP, IP_UNICASTIF, if_index_bytes)` using big-endian packing `!I`).
- **IPv4 stack forcing**: Verified in `core/network/session_pool.py:126,136` (`family=socket.AF_INET` in `aiohttp.TCPConnector`).
- **HTTP 429 exponential backoff with jitter and Retry-After**: Verified in `core/engine/worker.py:76-91, 170-194` (parses integer seconds or HTTP-date, falls back to `min(2 ** attempt + random.uniform(0.5, 1.5), 30.0)`).
- **Partial resume**: Verified in `core/engine/worker.py:135, 303-313` (`start_offset = chunk.start_byte + chunk.bytes_downloaded`).

---

## 2. Logic Chain

1. **Anti-bot & Cloudflare Detection (Obs 1.2)**:
   - When the Chrome extension intercepts a download or a user supplies authentic browser headers (`custom_headers`), `probe.py` injects hardcoded `"Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"'`.
   - When falling back to `curl_cffi` (which impersonates Chrome's TLS fingerprint), sending custom headers containing discordant `User-Agent` or `Sec-Ch-Ua` causes Cloudflare WAF to flag the client as an automated bot due to fingerprint/header divergence.
   - Removing conflicting `User-Agent` and `Sec-Ch-Ua*` headers before passing custom headers to `curl_cffi` while preserving `Cookie` (especially `cf_clearance`) and `Referer` allows `curl_cffi` to supply its own authentic Chrome headers matching its TLS fingerprint.
   - In `worker.py`, invoking `curl_session.get(..., stream=True, allow_redirects=True)` without checking `resp.status_code >= 400` allows HTTP error responses (e.g. HTML 403 challenge pages) to be treated as raw binary payload or silently skipped without capturing the failure. Raising `DownloadError` with the status code prevents corrupted files and captures the exact error.

2. **Downgrade to Single Sequential Connection on Range 403 (Obs 1.3)**:
   - Multi-connection Range requests from multiple workers to Cloudflare often trigger 403 Forbidden or 429 rate limits.
   - When multi-chunk download ends with incomplete chunks, `DownloadEngine` enters `_download_single_stream`.
   - Forcing `single_headers.pop("Range", None)` guarantees a clean sequential GET request without Range headers.
   - If `aiohttp` receives a 403 in `_download_single_stream`, adding a sequential `curl_cffi` fallback with `impersonate="chrome"` completes the download through Cloudflare WAF.
   - Storing the actual exception (`HTTP 403 Forbidden (Cloudflare WAF)`) on `DownloadEngine.error_message` ensures `QueueManager` records the specific failure reason on `task.error_message` when 0 bytes are transferred, satisfying R2.

3. **Batch API Endpoint & Auto-Smistamento (Obs 1.4)**:
   - `POST /add_batch` in `LocalHttpServer` must accept `{ "items": [...], "auto_start": bool }`.
   - Forwarding each element to `QueueManager` allows standard URL normalization, category detection, and subfolder routing.
   - Integrating `require_confirmation`, `confirmation_threshold_mb`, and `auto_categorize` into `QueueManager` ensures batch downloads larger than the threshold or with confirmation enabled enter `DownloadStatus.WAITING` (or `PENDING_CONFIRMATION`), ready for WinUI 3 `FloatingDownloadWindow` confirmation actions.

---

## 3. Caveats

- `curl_cffi` dependency is optional (`HAVE_CURL_CFFI = True/False`). Tests and production code must continue to operate gracefully when `curl_cffi` is unavailable (falling back to standard aiohttp error propagation).
- On Windows systems with non-standard routing, `AF_INET` forcing avoids IPv6 DNS failures, but requires loopback or IPv4 networking.
- When `auto_start` is set to `False` or confirmation threshold is exceeded, tasks enter `WAITING` status and do not automatically start streaming until explicitly resumed or confirmed via UI/IPC.

---

## 4. Conclusion

The Python backend architecture is well-structured and all 161 tests pass. To fulfill Requirement R2 and the user rules:
1. **Define `DownloadError`** in `core/engine/downloader.py` (or `worker.py`) to standardize download stream exceptions.
2. **Sanitize headers for impersonation** in `probe.py`, `downloader.py`, and `worker.py`: strip `User-Agent` and `Sec-Ch-Ua*` when using `curl_cffi.AsyncSession(impersonate="chrome")`, preserving `Cookie` (including `cf_clearance`) and `Referer`. Do not inject hardcoded `Sec-Ch-Ua` in `get_browser_headers()` when authentic client headers are provided.
3. **Implement single-connection downgrade in `downloader.py`**: on Range rejection, strip `Range` header and provide a sequential single-stream fallback with `curl_cffi` if `aiohttp` is challenged.
4. **Propagate real worker error to `task.error_message`**: in `queue_manager.py:549`, if downloaded bytes is 0, assign `engine.error_message` or worker exception (e.g. `"HTTP 403 Forbidden (Cloudflare WAF)"`) instead of `"Download ended incompletely"`.
5. **Add `POST /add_batch` to `core/api/local_server.py`** and wire settings (`require_confirmation`, `confirmation_threshold_mb`, `auto_categorize`) in `QueueManager` and `NamedPipeServer`.
6. **Add targeted pytest cases** in `tests/test_local_server.py` and `tests/test_winui3_features.py` (or `tests/test_engine.py`) covering `/add_batch`, 403 single-connection downgrade, and error message preservation.

---

## 5. Verification Method

To independently verify these findings and subsequent implementation:

1. **Run full Python test suite**:
   ```powershell
   C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v
   ```
   *Expected outcome*: 161+ tests pass with 0 failures.

2. **Run dedicated tests for Local Server**:
   ```powershell
   C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/test_local_server.py -v
   ```

3. **Verify specific code files**:
   - `core/engine/probe.py`: verify header preservation and `sanitize_headers_for_impersonation`.
   - `core/engine/worker.py`: verify `resp.status_code >= 400` check raising `DownloadError` before `aiter_content()`.
   - `core/engine/downloader.py`: verify sequential downgrade without `Range` header and curl_cffi single-stream fallback.
   - `core/scheduler/queue_manager.py`: verify `task.error_message` records real worker exception on 0-byte failure.
   - `core/api/local_server.py`: verify `POST /add_batch` endpoint accepts items list and returns 200 OK with queued tasks.
