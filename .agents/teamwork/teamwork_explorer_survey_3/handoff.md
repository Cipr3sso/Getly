# Survey Report: Chrome Extension (Manifest V3) for Getly

## Executive Summary
This survey provides a comprehensive architectural and code-level investigation of the Chrome Extension in `extensions/chrome/` to satisfy all requirements under **R3** of `ORIGINAL_REQUEST.md`. It covers `manifest.json`, `background.js`, `popup.html`, `popup.css`, and `popup.js`, detailing the removal of hardcoded client hints (`Sec-Ch-Ua`), the addition of `"scripting"` permissions, and the complete design of the "Page Link Grabber" with category filtering and batch dispatch to `POST http://127.0.0.1:9666/add_batch`.

---

## 1. Observation

### 1.1 `extensions/chrome/manifest.json`
- **Location**: `extensions/chrome/manifest.json`, Lines 6–16:
  ```json
  "permissions": [
    "downloads",
    "webRequest",
    "storage",
    "activeTab",
    "contextMenus",
    "cookies"
  ],
  "host_permissions": [
    "<all_urls>"
  ],
  ```
- **Finding**:
  - The `"scripting"` permission is **missing** from `"permissions"`.
  - `"activeTab"` and `"host_permissions": ["<all_urls>"]` are already declared.
  - In Manifest V3, invoking `chrome.scripting.executeScript(...)` requires `"scripting"` in `"permissions"`. Without this permission, any call to `chrome.scripting.executeScript` immediately throws: `TypeError: Cannot read properties of undefined (reading 'executeScript')` or an extension permission exception.

### 1.2 `extensions/chrome/background.js`
- **Location**: `extensions/chrome/background.js`, Lines 216–237 (in `chrome.downloads.onCreated` listener):
  ```javascript
  const payload = {
    url: url,
    filename: filename || "",
    file_size: fileSize,
    headers: {
      Referer: effectiveReferer,
      "User-Agent": navigator.userAgent,
      Cookie: cookieString,
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
      "Accept-Language": navigator.language || "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
      "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
      "Sec-Ch-Ua-Mobile": "?0",
      "Sec-Ch-Ua-Platform": '"Windows"',
      "Sec-Fetch-Dest": "document",
      "Sec-Fetch-Mode": "navigate",
      "Sec-Fetch-Site": "same-site",
      "Sec-Fetch-User": "?1",
      "Upgrade-Insecure-Requests": "1",
    },
    category: detectCategory(filename, mime),
    auto_start: true,
  };
  ```
- **Finding**:
  - Line 226 explicitly hardcodes `"Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'`, and lines 227–228 hardcode `"Sec-Ch-Ua-Mobile": "?0"` and `"Sec-Ch-Ua-Platform": '"Windows"'`.
  - In contrast, lines 450–454 (context menu handler) and lines 490–495 (`sendMediaToGetly`) only send `Referer`, `User-Agent`, and `Cookie`.
  - When the browser's real version is not 124 (e.g. Chrome 125+, 130+, Edge, Brave, or running on non-Windows platforms), this hardcoded version mismatch causes Cloudflare WAF, Akamai, and anti-bot systems to detect an anomaly (mismatched Client Hints vs TLS fingerprint / User-Agent), resulting in HTTP 403 Forbidden.
  - The background service worker already possesses `extractFullCookieHeader(targetUrl, referrerUrl)` (Lines 34–94) which queries `chrome.cookies.getAll` across origin, target URL, and base domains.
  - Message handling in lines 509–545 currently handles `CHECK_STATUS`, `GET_TAB_MEDIA`, `DOWNLOAD_MEDIA_ITEM`, `DOWNLOAD_ALL_TAB_MEDIA`, and `CLEAR_TAB_MEDIA`. There is currently **no** handler for batch downloads (`DOWNLOAD_BATCH`).

### 1.3 `extensions/chrome/popup.html` & `popup.js`
- **Location**: `extensions/chrome/popup.html`, Lines 27–64:
  - Contains a header, a toggle card for download interception (`#interceptionToggle`), and the media sniffer section (`.media-section`).
  - There is currently **no** button or container for analyzing page links or batch downloads.
- **Location**: `extensions/chrome/popup.js`, Lines 1–179:
  - Handles toggle state, polling health (`CHECK_STATUS`), and rendering media sniffer items (`GET_TAB_MEDIA`, `DOWNLOAD_MEDIA_ITEM`, `DOWNLOAD_ALL_TAB_MEDIA`, `CLEAR_TAB_MEDIA`).
  - All communication to the local backend daemon is delegated from `popup.js` to `background.js` via `chrome.runtime.sendMessage(...)`.
  - There is currently **no** logic for `chrome.scripting.executeScript`, link scanning, category filtering, or batch download dispatch.

### 1.4 Backend Local Server Contract (`core/api/local_server.py`)
- **Location**: `core/api/local_server.py`, Lines 54–163:
  - Currently exposes `GET /health`, `GET /jdcheck.js`, `POST /flashgot`, and `POST /add`.
  - Per R2 of `ORIGINAL_REQUEST.md`, `POST /add_batch` is being added on the local server at `http://127.0.0.1:9666/add_batch`.
  - Expected JSON structure:
    ```json
    {
      "items": [
        {
          "url": "https://example.com/file1.zip",
          "filename": "file1.zip",
          "file_size": 1048576,
          "headers": {
            "Referer": "https://example.com/",
            "User-Agent": "Mozilla/5.0 ...",
            "Cookie": "session=..."
          },
          "category": "Archivi"
        }
      ],
      "auto_start": true
    }
    ```

---

## 2. Logic Chain

### 2.1 Permission Alignment (`manifest.json`)
- Observation 1.1 reveals `"scripting"` is absent from `manifest.json`.
- Requirement R3 explicitly states: `Aggiungere il permesso "scripting" in extensions/chrome/manifest.json`.
- Adding `"scripting"` allows `chrome.scripting.executeScript({ target: { tabId }, func: ... })` to run in the context of the active webpage without requiring persistent intrusive content scripts.

### 2.2 Removal of Hardcoded `Sec-Ch-Ua` (`background.js`)
- Observation 1.2 shows `"Sec-Ch-Ua"` with `v="124"` is hardcoded in `chrome.downloads.onCreated`.
- Requirement R3 explicitly dictates:
  `Rimuovere l'invio di header Sec-Ch-Ua fittizi (v="124") congiuntamente al navigator.userAgent reale; trasmettere User-Agent effettivo, Referer reale (della scheda attiva o del download) e header Cookie completo ottenuto tramite extractFullCookieHeader`.
- By removing `"Sec-Ch-Ua"`, `"Sec-Ch-Ua-Mobile"`, and `"Sec-Ch-Ua-Platform"` from `payload.headers`, the backend Python engine (`probe.py` / `curl_cffi` / `worker.py`) receives clean, authentic browser headers without artificial contradictions.

### 2.3 Page Link Grabber Script Injection (`chrome.scripting.executeScript`)
- Requirement R3 requires extracting links from `<a href>`, `<video src>`, `<audio src>`, and `<source src>`.
- In Manifest V3, `chrome.scripting.executeScript` runs a pure function in the tab's DOM:
  1. Inspect `document.querySelectorAll("a[href], video[src], audio[src], source[src]")`.
  2. Also inspect `<video><source src="...">` and `<audio><source src="...">`.
  3. Resolve relative URLs to absolute URLs using `a.href` and `new URL(src, document.baseURI).href`.
  4. Discard non-HTTP protocols (`javascript:`, `mailto:`, `blob:`, `data:`, `about:`).
  5. Deduplicate URLs using a `Set` or `Map<url, item>`.

### 2.4 Extension Matching & Categorization Logic
- Downloadable file filtering must classify links into the required 5 categories:
  - **Archivi**: `.zip`, `.rar`, `.7z`, `.tar`, `.gz`, `.tgz`, `.bz2`, `.xz`, `.7-zip`
  - **Documenti**: `.pdf`, `.epub`, `.mobi`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.txt`, `.rtf`, `.odt`, `.csv`
  - **Video** (and Media): `.mp4`, `.mkv`, `.webm`, `.avi`, `.mov`, `.wmv`, `.flv`, `.m4v`, `.m3u8`, `.mpd`, `.ts`, `.mp3`, `.m4a`, `.aac`, `.flac`, `.wav`, `.ogg`, `.opus`
  - **Programmi**: `.exe`, `.msi`, `.apk`, `.dmg`, `.pkg`, `.deb`, `.rpm`, `.iso`, `.bin`, `.appimage`
  - **Tutti**: Union of all matched downloadable files.
- Non-downloadable web links (HTML pages like `index.html`, `/about`, `/contact`, query anchors) are excluded from the grabber list.

### 2.5 Popup UI/UX Design & Resiliency
- Observation 1.3 shows the popup is compact (width 360px, max-height 520px).
- Adding the Page Link Grabber:
  1. Add trigger button `"🔍 Analizza tutti i link della pagina"` (`#scanPageLinksBtn`).
  2. Implement an integrated panel or tabbed view (`Media Rilevati` vs `Link Pagina`):
     - Keeps the popup organized without vertical overflow.
     - Displays the Quick Filter Bar with pill buttons: `Tutti (N)`, `Archivi (N)`, `Documenti (N)`, `Video (N)`, `Programmi (N)`.
     - Displays "Seleziona tutti" checkbox and count of selected items.
     - Scrollable list of scanned links, each with checkbox, filename, extension badge, and full URL tooltip.
     - Bottom primary button `"⬇️ Scarica selezionati con Getly (N)"`.
- Dispatch Resiliency:
  - If `popup.js` calls `fetch()` directly, clicking outside the popup instantly unloads the popup DOM, aborting the pending HTTP request.
  - Sending a `DOWNLOAD_BATCH` message to `background.js` allows the background service worker to:
    1. Resolve full cookies for each URL using `extractFullCookieHeader`.
    2. Attach `User-Agent` and real `Referer`.
    3. Send `POST http://127.0.0.1:9666/add_batch`.
    4. Guarantee delivery even if the popup is closed mid-transit.

---

## 3. Caveats
1. **Restricted Browser Pages**:
   - `chrome.scripting.executeScript` cannot execute scripts on `chrome://*`, `chrome-extension://*`, `edge://*`, or the Chrome Web Store.
   - *Mitigation*: Catch the permission rejection in `popup.js` and display a user-friendly toast (`"Impossibile scansionare pagine di sistema del browser"`).
2. **Dynamic / SPA-rendered DOM**:
   - Single Page Applications may render links dynamically upon scrolling.
   - The script inspects the DOM snapshot present at execution time.
3. **Backend Availability**:
   - If the Getly desktop app is closed, `POST http://127.0.0.1:9666/add_batch` will fail with network error.
   - The popup must catch this and show `"Errore: Getly demone offline"`.

---

## 4. Conclusion & Actionable Blueprint

### 4.1 Modifications for `extensions/chrome/manifest.json`
Add `"scripting"` to `"permissions"`:
```diff
--- a/extensions/chrome/manifest.json
+++ b/extensions/chrome/manifest.json
@@ -6,7 +6,8 @@
     "downloads",
     "webRequest",
     "storage",
     "activeTab",
     "contextMenus",
-    "cookies"
+    "cookies",
+    "scripting"
   ],
```

### 4.2 Modifications for `extensions/chrome/background.js`
1. **Remove hardcoded `Sec-Ch-Ua` headers in `chrome.downloads.onCreated`**:
```diff
--- a/extensions/chrome/background.js
+++ b/extensions/chrome/background.js
@@ -224,9 +224,6 @@ chrome.downloads.onCreated.addListener(async (downloadItem) => {
       Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
       "Accept-Language": navigator.language || "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
-      "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
-      "Sec-Ch-Ua-Mobile": "?0",
-      "Sec-Ch-Ua-Platform": '"Windows"',
       "Sec-Fetch-Dest": "document",
       "Sec-Fetch-Mode": "navigate",
       "Sec-Fetch-Site": "same-site",
```

2. **Add `DOWNLOAD_BATCH` message handler & `sendBatchToGetly` function**:
```javascript
// In chrome.runtime.onMessage:
if (request.type === "DOWNLOAD_BATCH") {
  sendBatchToGetly(request.items, request.referer)
    .then((res) => sendResponse({ success: true, result: res, count: (request.items || []).length }))
    .catch((err) => sendResponse({ success: false, error: err.message }));
  return true;
}

// Function:
async function sendBatchToGetly(items, refererUrl) {
  if (!items || !items.length) {
    throw new Error("Nessun elemento specificato per il download batch");
  }

  const preparedItems = await Promise.all(
    items.map(async (item) => {
      let itemReferer = refererUrl || "";
      if (!itemReferer && item.url && item.url.startsWith("http")) {
        try {
          const parsed = new URL(item.url);
          itemReferer = `${parsed.origin}/`;
        } catch (_) {}
      }

      const cookieString = await extractFullCookieHeader(item.url, itemReferer);

      return {
        url: item.url,
        filename: item.filename || "",
        file_size: item.file_size || 0,
        headers: {
          Referer: itemReferer,
          "User-Agent": navigator.userAgent,
          Cookie: cookieString,
        },
        category: item.category || detectCategory(item.filename || item.url, ""),
      };
    })
  );

  const payload = {
    items: preparedItems,
    auto_start: true,
  };

  const response = await fetch(`${GETLY_API_BASE}/add_batch`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    throw new Error(`Local daemon /add_batch returned status ${response.status}`);
  }

  return await response.json();
}
```

### 4.3 Modifications for `extensions/chrome/popup.html`
Add:
1. Navigation tabs or link grabber action card:
   - Button `#scanPageLinksBtn`: `"🔍 Analizza tutti i link della pagina"`
2. Link Grabber view section (`#grabberSection`):
   - Category filter bar with pills: `Tutti`, `Archivi`, `Documenti`, `Video`, `Programmi`
   - Select all checkbox (`#selectAllLinks`)
   - Scrollable list `#grabberList`
   - Action button `#downloadBatchBtn`: `"⬇️ Scarica selezionati con Getly"`

### 4.4 Modifications for `extensions/chrome/popup.css`
Add styles matching the Fluent / dark design system:
- `.filter-bar`, `.filter-pill`, `.filter-pill.active`
- `.grabber-section`, `.grabber-item`, `.grabber-meta`, `.grabber-actions`
- Scrollbar refinements and responsive sizing within 360px popup width.

### 4.5 Modifications for `extensions/chrome/popup.js`
Implement:
1. `chrome.scripting.executeScript` invocation targeting `activeTab.id`.
2. Link extraction function with regex extension classification.
3. Category tab filtering state management (`activeCategory = "Tutti"`).
4. Selection toggle logic (individual and select all).
5. Message dispatch to `background.js` via `type: "DOWNLOAD_BATCH"`.

---

## 5. Verification Method

### 5.1 Static Verification
1. Inspect `extensions/chrome/manifest.json`:
   - Verify `"scripting"` is listed in `"permissions"`.
   - Validate JSON syntax: `python -m json.tool extensions/chrome/manifest.json`.
2. Inspect `extensions/chrome/background.js`:
   - Run grep to ensure no `"124"` or `"Sec-Ch-Ua"` remains:
     `grep -i "Sec-Ch-Ua" extensions/chrome/background.js` -> 0 matches.
   - Verify `DOWNLOAD_BATCH` and `sendBatchToGetly` are defined.

### 5.2 Functional Extension Verification
1. Load unpacked extension in Chrome/Edge at `chrome://extensions/`:
   - Ensure 0 errors reported in the extension card.
2. Test on a web page containing mixed links (e.g., zip files, pdf documents, videos, exes):
   - Click `"🔍 Analizza tutti i link della pagina"`.
   - Verify links are extracted, grouped under category badges, and filterable via the category pills.
   - Click `"⬇️ Scarica selezionati con Getly"`.
   - Verify batch payload is sent to `POST http://127.0.0.1:9666/add_batch`.

### 5.3 Automated Python Test Suite
Run project tests to ensure daemon and backend compatibility:
```powershell
pytest tests/test_local_server.py -v
pytest tests/ -v
```
