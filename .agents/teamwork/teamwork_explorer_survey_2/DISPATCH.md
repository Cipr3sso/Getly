# Task Assignment: Survey Python Backend

## Objective
Survey the Python backend in `core/` and tests in `tests/` to map all requirements under R2 and user rules in `AGENTS.md`.

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
And note the network resilience rules in `AGENTS.md` (Winsock IP_UNICASTIF on Windows, family=socket.AF_INET in aiohttp, HTTP 429 exponential backoff with retry-after, partial resume).

## Scope of Investigation
1. Anti-bot and Header Handling (`core/probe.py`, `core/downloader.py`, `core/worker.py`, `core/session_pool.py`):
   - Locate where `User-Agent` and `Sec-Ch-Ua` headers are set, manipulated, or hardcoded (e.g., `"124"`).
   - Check how fallback with `curl_cffi.requests.AsyncSession(impersonate="chrome")` handles custom headers, stripping conflicting `User-Agent` / `Sec-Ch-Ua` while preserving `Cookie` (including `cf_clearance`) and `Referer`.
   - Check in `worker.py` how `curl_cffi` requests are issued: `resp = await session.get(url, headers=req_headers, stream=True, allow_redirects=True)` and where `if resp.status_code >= 400: raise DownloadError(...)` is missing or needed before `resp.aiter_content()`.
2. Cloudflare Range Multi-connection Rejection and Downgrade (`core/downloader.py`):
   - Locate where multi-connection Range requests receive HTTP 403/429/503.
   - Investigate how automatic fallback/downgrade to 1 single sequential connection (no Range header) can be implemented to complete the download.
   - Investigate error recording: if download fails with 0 bytes downloaded, ensure `task.error_message` records the actual worker exception (e.g. `"HTTP 403 Forbidden (Cloudflare WAF)"`) rather than `"Download ended incompletely"`.
3. Batch API (`core/api/local_server.py` and `QueueManager`):
   - Inspect existing REST endpoints in `local_server.py`.
   - Determine how `POST /add_batch` should be added: accepting `{ "items": [ { "url": "...", "filename": "...", "file_size": ..., "headers": {...} } ], "auto_start": true }`.
   - Check how `QueueManager` categorizes into subfolders and how confirmation thresholds (`WaitingConfirmation`) are applied.
4. Python Tests (`tests/`):
   - Inspect test suite structure, fixtures, mocks, and how to run `pytest tests/ -v`.
   - Map what new tests are needed for `/add_batch` and 403 single-connection fallback.

## Deliverable
Write your complete survey report to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_2\handoff.md`
Then send a brief completion message back to the orchestrator.

## 2026-11-02T11:02:29Z
[Message] sender=6b06a18d-9c2f-48a8-997b-397dc5eec34b priority=MESSAGE_PRIORITY_HIGH
You are teamwork_explorer assigned to survey the Python Backend for Getly.
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_2
MANDATORY: Read the original user request at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_2\DISPATCH.md.
Explore the repository: inspect core/ (probe.py, downloader.py, worker.py, session_pool.py, api/local_server.py, QueueManager) and tests/.
Check network resilience user rules in AGENTS.md (Winsock IP_UNICASTIF on Windows, family=socket.AF_INET in aiohttp, HTTP 429 exponential backoff with retry-after, partial resume).
Write your detailed findings report to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\teamwork_explorer_survey_2\handoff.md.
Update your progress.md periodically.
When finished, send a brief message to your parent orchestrator confirming completion.
