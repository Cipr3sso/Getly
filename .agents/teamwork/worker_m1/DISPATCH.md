# Task Assignment: Milestone M1 — Python Core & Batch API

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
Read the architecture and contracts in:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md`
Read the detailed survey report from Explorer 2 at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_2\handoff.md`
And strictly observe user rules in:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md` (Winsock IP_UNICASTIF, family=socket.AF_INET, HTTP 429 retry-after & exponential backoff, partial resume).

## Exclusive Write Ownership
You exclusively own:
- `core/engine/downloader.py`
- `core/engine/worker.py`
- `core/engine/probe.py`
- `core/network/session_pool.py`
- `core/scheduler/queue_manager.py`
- `core/api/local_server.py`
- `core/ipc/pipe_server.py`
- `tests/` (including `tests/test_local_server.py` and new test cases)

## Mandatory Integrity Warning
DO NOT CHEAT. All implementations must be genuine. DO NOT hardcode test results, create dummy/facade implementations, or circumvent the intended task. A teamwork_auditor will independently verify your work. Integrity violations WILL be detected and your work WILL be rejected.

## Requirements to Implement

1. **Anti-bot and Header Handling (`probe.py`, `downloader.py`, `worker.py`, `session_pool.py`)**:
   - Define `DownloadError(Exception)` (can be in `downloader.py` and imported by `worker.py`).
   - In `probe.py`: in `get_browser_headers()`, do NOT inject hardcoded `Sec-Ch-Ua` headers (e.g. `"125"`) if authentic client headers (`custom_headers`) are provided.
   - When falling back to `curl_cffi.requests.AsyncSession(impersonate="chrome")`, sanitize custom headers: strip discordant `User-Agent`, `Sec-Ch-Ua`, `Sec-Ch-Ua-Mobile`, `Sec-Ch-Ua-Platform` that conflict with curl_cffi's impersonated profile, while strictly preserving `Cookie` (including `cf_clearance`) and `Referer`.
   - In `session_pool.py`: use `impersonate="chrome"` as standard.
   - In `worker.py`: with `curl_cffi`, use `resp = await session.get(url, headers=req_headers, stream=True, allow_redirects=True)`. Validate `if resp.status_code >= 400: raise DownloadError(f"HTTP {resp.status_code} {resp.reason if hasattr(resp, 'reason') else ''}")` before iterating on `resp.aiter_content()`.

2. **Cloudflare Range Rejection & Fallback Downgrade (`downloader.py`, `queue_manager.py`)**:
   - In `downloader.py`: when multi-connection Range requests receive HTTP 403/429/503 (or incomplete chunks due to Range rejection), execute the automatic downgrade to 1 single sequential connection. In `_download_single_stream`, strip the `Range` header (`single_headers.pop("Range", None)`) to ensure a sequential download completes instead of failing. If aiohttp receives 403 in single-stream, fall back to curl_cffi single-stream with `impersonate="chrome"`.
   - If download fails with 0 bytes downloaded, store the actual worker exception on `self.error_message` in `DownloadEngine`.
   - In `queue_manager.py:549`: set `task.error_message = self.engine.error_message or "Download ended incompletely"` so the actual HTTP/WAF error (e.g. `"HTTP 403 Forbidden (Cloudflare WAF)"`) is preserved.

3. **Batch API Endpoint & Auto-Smistamento (`local_server.py`, `queue_manager.py`, `pipe_server.py`)**:
   - In `local_server.py`: add endpoint `POST /add_batch` accepting `{ "items": [ { "url": "...", "filename": "...", "file_size": ..., "headers": {...}, "category": "..." } ], "auto_start": true }`.
   - Forward each item to `QueueManager.add_url(...)`.
   - In `queue_manager.py`: add properties `require_confirmation: bool` (default False), `confirmation_threshold_mb: int` (default 100), `auto_categorize: bool` (default True).
   - If `auto_categorize` is True and no explicit destination directory is provided, route to the category subfolder (`get_category_subfolder`).
   - If `require_confirmation` is True or (`file_size` or parsed total size > `confirmation_threshold_mb * 1024 * 1024`), set task status to `DownloadStatus.WAITING` (WaitingConfirmation) rather than auto-starting.
   - Return `{ "success": True, "count": len(tasks), "tasks": [...] }` from `POST /add_batch`.
   - In `pipe_server.py`: add `system.set_settings` RPC handler to update `require_confirmation`, `confirmation_threshold_mb`, `auto_categorize` on `QueueManager`.

4. **Testing & Verification**:
   - Add comprehensive tests in `tests/test_local_server.py` testing `POST /add_batch` (valid items, empty items, category routing, auto_start flag).
   - Add unit test for single-stream downgrade on 403 and error message preservation on 0-byte failure.
   - Run the Python test suite: `C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v`.
   - All tests must pass (161 existing + new tests).

## Deliverable
Write your complete handoff report to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m1\handoff.md`
Report test commands executed and results. Then send a completion message to the orchestrator.


## 2026-11-02T11:15:01Z
You are teamwork_worker assigned to implement Milestone M1: Python Core & Batch API.
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m1
MANDATORY: Read the original user request at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read the survey report at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_2\handoff.md.
Read your task assignment and integrity warning at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m1\DISPATCH.md.
Strictly observe user rules in c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md.
Implement all requirements in core/ and tests/.
Run the pytest test suite using: C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v.
Document your changes, build/test results, and verification in c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m1\handoff.md.
Update your progress.md periodically.
When done, send a completion message to your parent orchestrator.
