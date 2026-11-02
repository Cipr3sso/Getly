# Handoff Report: Milestone M2 — Chrome Extension for Getly

## 1. Observation
- **`extensions/chrome/manifest.json`**:
  - Previously contained `permissions: ["downloads", "webRequest", "storage", "activeTab", "contextMenus", "cookies"]` without `"scripting"`.
  - Calling `chrome.scripting.executeScript` in Manifest V3 without `"scripting"` in `permissions` causes a browser permission rejection.
- **`extensions/chrome/background.js`**:
  - Lines 226–228 previously hardcoded client hints:
    `"Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'`, `"Sec-Ch-Ua-Mobile": "?0"`, and `"Sec-Ch-Ua-Platform": '"Windows"'`.
  - When the browser version differs from 124 or runs on non-Windows platforms, Cloudflare WAF and anti-bot systems flag the Client Hints vs TLS/User-Agent discrepancy, triggering HTTP 403 Forbidden.
  - Message handling previously handled `CHECK_STATUS`, `GET_TAB_MEDIA`, `DOWNLOAD_MEDIA_ITEM`, `DOWNLOAD_ALL_TAB_MEDIA`, `CLEAR_TAB_MEDIA`, but had no listener for batch downloads.
  - `detectCategory` previously used non-standard category names ("Compressi", "Software").
- **`extensions/chrome/popup.html` & `popup.css`**:
  - Previously only supported the media sniffer without link grabbing or batch actions.
- **`extensions/chrome/popup.js`**:
  - Lacked scripting execution, DOM link extraction, category filtering, selection state handling, and batch message dispatch.

---

## 2. Logic Chain
1. **Manifest Permission**:
   - Added `"scripting"` to `"permissions"` in `extensions/chrome/manifest.json`.
   - Allows invoking `chrome.scripting.executeScript({ target: { tabId }, func: extractPageLinksInContent })` without requiring permanent content scripts on all visited domains.
2. **Client Hints Alignment & Clean Headers**:
   - Removed hardcoded `"Sec-Ch-Ua"`, `"Sec-Ch-Ua-Mobile"`, and `"Sec-Ch-Ua-Platform"` from `chrome.downloads.onCreated`.
   - Download requests now forward only clean, authentic browser headers (`Referer`, `User-Agent`, `Cookie`), ensuring consistency with the Python core daemon (`probe.py`, `curl_cffi`, `worker.py`).
3. **Batch Download Dispatcher**:
   - Added `DOWNLOAD_BATCH` listener in `chrome.runtime.onMessage`.
   - Implemented `sendBatchToGetly(items, refererUrl)`:
     - Iterates through items, dynamically resolving full cookies with `extractFullCookieHeader(item.url, itemReferer)`.
     - Packages payload conforming to the contract: `{ items: [...], auto_start: true }`.
     - Sends `POST http://127.0.0.1:9666/add_batch`.
     - Returns `{ success: true, count, result }`.
4. **Page Link Grabber UI & Controller**:
   - In `popup.html`:
     - Added button `"🔍 Analizza tutti i link della pagina"` (`#scanPageLinksBtn`).
     - Added tab navigation (`🎬 Media rilevati` vs `🔗 Link pagina`).
     - Added category filter bar with pill buttons for `Tutti`, `Archivi`, `Documenti`, `Video`, `Programmi` (`#categoryFilterBar`).
     - Added select-all controls (`#selectAllLinks`, `#selectedCountText`).
     - Added scrollable link list container (`#grabberList`) with empty state (`#emptyGrabber`).
     - Added batch action button `"⬇️ Scarica selezionati con Getly"` (`#downloadBatchBtn`).
   - In `popup.css`:
     - Integrated modern Fluent Design dark theme (`#18181b` body, `#27272a` cards, `#3f3f46` borders, `#3b82f6` accent blue).
     - Styled scan button (`.btn-scan`), nav tabs (`.nav-tabs`, `.tab-btn`), filter pills (`.filter-pill`), select bar (`.select-bar`), grabber item cards (`.grabber-item`), and category badges (`.badge.archivi`, `.badge.documenti`, `.badge.video`, `.badge.programmi`).
   - In `popup.js`:
     - Implemented `#scanPageLinksBtn` click handler invoking `chrome.scripting.executeScript` targeting `activeTab.id`.
     - Implemented `extractPageLinksInContent`: runs inside tab DOM, queries `a[href]`, `video[src]`, `audio[src]`, `source[src]`, resolves relative paths with `new URL(trimmed, document.baseURI).href`, skips non-HTTP protocols (`javascript:`, `mailto:`, `blob:`, `data:`), and deduplicates URLs.
     - Implemented `parseDownloadableItem`: filters by extensions and maps to categories:
       - **Archivi**: `.tar.gz`, `.7-zip`, `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, `.tgz`, `.bz2`, `.xz`
       - **Documenti**: `.pdf`, `.epub`, `.mobi`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.txt`, `.rtf`, `.odt`, `.csv`
       - **Video**: `.mp4`, `.mkv`, `.webm`, `.avi`, `.mov`, `.wmv`, `.flv`, `.m4v`, `.m3u8`, `.mpd`, `.ts`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.wav`, `.ogg`, `.opus`
       - **Programmi**: `.exe`, `.msi`, `.apk`, `.dmg`, `.pkg`, `.deb`, `.rpm`, `.iso`, `.bin`, `.appimage`
       - **Tutti**: Union of all matched downloadable files.
     - Implemented selection state management: individual item checkbox toggling, select-all checkbox (with indeterminate state support), and dynamic batch download button count.
     - Implemented `#downloadBatchBtn` click handler sending `type: "DOWNLOAD_BATCH"` to `background.js`.

---

## 3. Caveats
- Browser system pages (e.g. `chrome://`, `chrome-extension://`, `edge://`, `about:blank`, Chrome Web Store) forbid script injection by browser security policy. In `popup.js`, this is detected and intercepted with a friendly error toast (`"Impossibile scansionare pagine di sistema del browser"`).
- Single-page applications (SPAs) that load DOM links lazily via infinite scroll will only have links currently in the DOM extracted at the moment of scan. Re-clicking the scan button refreshes the scan with any newly rendered items.
- If the Getly desktop daemon is offline, batch requests report a user-friendly error toast without unhandled exceptions.

---

## 4. Conclusion
Milestone M2 (Chrome Extension) is fully implemented, strictly adhering to the requirements of R3 in `ORIGINAL_REQUEST.md`, `PROJECT.md`, and `DISPATCH.md`. The extension manifest includes `"scripting"`, `background.js` has 0 hardcoded `Sec-Ch-Ua` or fake `v="124"` headers, and the Page Link Grabber provides DOM link extraction, 5-category filtering, selection management, and batch dispatch to `POST http://127.0.0.1:9666/add_batch`.

---

## 5. Verification Method

### 5.1 Static Verification Commands
```powershell
# 1. Validate manifest.json syntax
python -m json.tool extensions/chrome/manifest.json

# 2. Check for absence of Sec-Ch-Ua or hardcoded 124
# Expectation: 0 matches found
git grep -i "Sec-Ch-Ua" extensions/chrome/
git grep -i "124" extensions/chrome/

# 3. Check JavaScript syntax
node -c extensions/chrome/background.js extensions/chrome/popup.js
```

### 5.2 Python Backend Integration & Core Verification
```powershell
# Verify local server add_batch endpoint compatibility
& 'C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe' -m pytest tests/test_local_server.py -v

# Full suite verification (169 passed)
& 'C:\Users\carlo\AppData\Local\Programs\Python\Python310\python.exe' -m pytest tests/ -v
```

### 5.3 Extension Functional Verification in Browser
1. In Google Chrome or Microsoft Edge, navigate to `chrome://extensions/`.
2. Enable "Developer mode" and click "Load unpacked".
3. Select `c:\Users\carlo\Documents\antigravity\resilient-heisenberg\extensions\chrome`.
4. Ensure the extension loads with version 1.0.0 and 0 errors.
5. Open any webpage containing downloadable links (archives, PDFs, audio/video, binaries).
6. Click the Getly extension icon and click `"🔍 Analizza tutti i link della pagina"`.
7. Verify links appear categorized under the category filter pills (`Tutti`, `Archivi`, `Documenti`, `Video`, `Programmi`).
8. Toggle checkboxes and click `"⬇️ Scarica selezionati con Getly"`.
9. Verify payload is sent to `http://127.0.0.1:9666/add_batch`.
