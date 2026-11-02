# Task Assignment: Reviewer 2 — Python Backend Review

## Mandatory Reading
Read the original user request at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md`
Read PROJECT.md at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md`
Read user rules in:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md`
Read worker handoff report at:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m1\handoff.md`

## Scope of Review
1. **Python Backend Implementation**:
   - Run tests: `C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe -m pytest tests/ -v`
   - Inspect `core/engine/probe.py`: verify `sanitize_headers_for_impersonation`, verify authentic client headers do not have hardcoded `Sec-Ch-Ua` injected.
   - Inspect `core/engine/downloader.py`: verify `DownloadError`, single-connection sequential downgrade stripping `Range` header, curl_cffi single-stream fallback, and error message storage.
   - Inspect `core/engine/worker.py`: verify `curl_cffi` request handling (`stream=True, allow_redirects=True`), check `resp.status_code >= 400` raising `DownloadError` before content iteration.
   - Inspect `core/scheduler/queue_manager.py`: verify real worker exception on `task.error_message` when 0 bytes transferred, verify confirmation threshold and auto-categorization.
   - Inspect `core/api/local_server.py`: verify `POST /add_batch` endpoint.
   - Check compliance with user rules in `AGENTS.md`: Winsock `IP_UNICASTIF` on Windows, IPv4 stack forcing (`family=socket.AF_INET` in aiohttp), HTTP 429 exponential backoff with Retry-After, partial resume.

## Deliverable
Write your comprehensive review report and explicit verdict (APPROVE or REQUEST_CHANGES) to:
`c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_2\handoff.md`
Then send a brief message with your verdict to the orchestrator.

## 2026-11-02T12:08:03Z
[Message] sender=6b06a18d-9c2f-48a8-997b-397dc5eec34b priority=MESSAGE_PRIORITY_HIGH content=You are teamwork_reviewer assigned to Reviewer 2 (Python Backend Review).
Your working directory is: c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_2
MANDATORY: Read ORIGINAL_REQUEST.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\ORIGINAL_REQUEST.md.
Read PROJECT.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\PROJECT.md.
Read AGENTS.md at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\AGENTS.md.
Read worker handoff:
- c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\worker_m1\handoff.md
Read your task instructions at c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_2\DISPATCH.md.
Perform rigorous review, run pytest suite, verify all changes and rules, and write your report and explicit verdict (APPROVE or REQUEST_CHANGES) to c:\Users\carlo\Documents\antigravity\resilient-heisenberg\.agents\teamwork\reviewer_2\handoff.md.
Update your progress.md periodically.
When done, send a message with your verdict to your parent orchestrator.
