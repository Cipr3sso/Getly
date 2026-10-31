/**
 * Getly - Background Service Worker (Manifest V3)
 * Intercepts browser download events, extracts metadata, cookies & headers,
 * and passes them to Getly via Native Messaging or local HTTP daemon.
 * Gracefully falls back to normal browser downloading if the host or downloader is offline.
 */

const bypassedUrls = new Set();
let interceptionEnabled = true;

// Load stored settings
chrome.storage.local.get({ enabled: true }, (items) => {
  interceptionEnabled = items.enabled !== false;
  console.log("Getly extension initialized. Interception enabled:", interceptionEnabled);
});

// Update state when toggle changes in popup
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.enabled !== undefined) {
    interceptionEnabled = changes.enabled.newValue;
    console.log("Interception state changed:", interceptionEnabled);
  }
});

// Primary interceptor on download creation
chrome.downloads.onCreated.addListener(async (downloadItem) => {
  if (!interceptionEnabled) {
    return;
  }

  // 1. State Check: Ignore past or completed downloads from history
  if (downloadItem.state && downloadItem.state !== "in_progress") {
    return;
  }

  // 2. Timestamp Check: Ignore historical downloads from browser history (> 5 seconds old)
  if (downloadItem.startTime) {
    const startTime = new Date(downloadItem.startTime).getTime();
    if (!isNaN(startTime) && Date.now() - startTime > 5000) {
      console.log("[Getly] Ignoring historical download item (>5s old):", downloadItem.url);
      return;
    }
  }

  const rawUrl = downloadItem.url || "";
  const finalUrl = downloadItem.finalUrl || "";
  const url = finalUrl || rawUrl;
  if (!url) {
    return;
  }

  // 3. Ignore blob, data, internal browser pages and local loops
  // Do NOT cancel native browser download for blob:, data: or filesystem: URLs
  if (
    rawUrl.startsWith("blob:") ||
    rawUrl.startsWith("data:") ||
    rawUrl.startsWith("filesystem:") ||
    finalUrl.startsWith("blob:") ||
    finalUrl.startsWith("data:") ||
    finalUrl.startsWith("filesystem:") ||
    url.startsWith("blob:") ||
    url.startsWith("data:") ||
    url.startsWith("filesystem:") ||
    url.startsWith("chrome:") ||
    url.startsWith("chrome-extension:") ||
    url.includes("127.0.0.1") ||
    url.includes("localhost")
  ) {
    console.log("[Getly] Skipping native blob/data/filesystem download without cancelling:", url);
    return;
  }

  // 4. Only intercept standard HTTP and HTTPS requests
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return;
  }

  // 5. Filter MIME / Web page assets that are not actual downloads
  const mime = (downloadItem.mime || "").toLowerCase();
  if (mime === "text/html" || mime === "text/css" || mime === "image/x-icon") {
    return;
  }

  // 6. Prevent recursive loop if this is a fallback download re-triggered by us
  if (bypassedUrls.has(url)) {
    bypassedUrls.delete(url);
    console.log("[Getly] Bypassing re-triggered fallback download for:", url);
    return;
  }

  // 7. Immediately and synchronously cancel the native browser download
  // This prevents Brave / Chrome from popping up the "Save As" file dialog
  chrome.downloads.cancel(downloadItem.id, () => {
    if (chrome.runtime.lastError) {
      // Ignored if already completed or handled
    }
    chrome.downloads.erase({ id: downloadItem.id });
  });

  // 8. Extract clean basename filename (strip full paths and query strings)
  let filename = "";
  if (downloadItem.filename) {
    filename = downloadItem.filename.split(/[\\/]/).pop();
  }
  if (!filename && url) {
    try {
      const parsedUrl = new URL(url);
      const name = parsedUrl.pathname.split("/").pop();
      if (name && name.includes(".")) {
        filename = decodeURIComponent(name);
      }
    } catch (_) {}
  }
  if (filename) {
    filename = filename.split(/[\\/]/).pop();
    if (filename.includes("?")) {
      filename = filename.split("?")[0];
    }
  }

  // 9. Collect session cookies for domain
  let cookieString = "";
  try {
    const cookies = await chrome.cookies.getAll({ url: url });
    if (cookies && cookies.length > 0) {
      cookieString = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
    }
  } catch (err) {
    console.warn("[Getly] Could not read cookies for URL:", err);
  }

  // 10. Extract Referer: downloadItem.referrer or current active tab URL as fallback
  let referrer = downloadItem.referrer || "";
  if (!referrer) {
    try {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (activeTab && activeTab.url && (activeTab.url.startsWith("http://") || activeTab.url.startsWith("https://"))) {
        referrer = activeTab.url;
      }
    } catch (_) {}
  }
  // Twitter / X video CDN anti-hotlinking fallback
  if (url.includes("twimg.com") && (!referrer || referrer.includes("twimg.com"))) {
    referrer = "https://x.com/";
  }

  const payload = {
    url: url,
    filename: filename || "",
    referer: referrer,
    user_agent: navigator.userAgent,
    cookies: cookieString,
    file_size: downloadItem.fileSize || 0,
  };

  console.log("[Getly] Forwarding intercepted download directly to http://127.0.0.1:9666/api/add_download:", payload);

  // 7. Direct HTTP Communication with local CNL / Queue Server (Port 9666)
  try {
    const response = await fetch("http://127.0.0.1:9666/api/add_download", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`Local server responded with HTTP status ${response.status}`);
    }

    const data = await response.json();
    console.log("[Getly] Successfully queued download via direct HTTP API:", data);
  } catch (err) {
    console.warn("[Getly] Direct HTTP API offline or unreachable. Restoring standard browser download...", err);

    // Graceful fallback: re-trigger standard browser download without catching it again
    bypassedUrls.add(url);
    chrome.downloads.download(
      {
        url: url,
        filename: filename || undefined,
        conflictAction: "uniquify",
      },
      (newDownloadId) => {
        if (chrome.runtime.lastError) {
          console.error("[Getly] Fallback browser download failed:", chrome.runtime.lastError);
        } else {
          console.log("[Getly] Fallback download successfully resumed in browser, id:", newDownloadId);
        }
      }
    );
  }
});

// Messages from popup (status check & manual submission)
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "CHECK_NATIVE_HOST") {
    // Check local HTTP endpoint first
    fetch("http://127.0.0.1:9666/flash/check")
      .then((r) => r.text())
      .then((t) => {
        if (t.includes("success")) {
          sendResponse({ connected: true, via: "http" });
        } else {
          sendResponse({ connected: false });
        }
      })
      .catch(() => {
        // Fallback check to native messaging
        chrome.runtime.sendNativeMessage("resilient_downloader", { action: "ping" }, (response) => {
          if (chrome.runtime.lastError) {
            sendResponse({ connected: false, error: chrome.runtime.lastError.message });
          } else {
            sendResponse({ connected: true, via: "native", response: response });
          }
        });
      });
    return true; // asynchronous sendResponse
  }

  if (request.type === "MANUAL_DOWNLOAD") {
    fetch("http://127.0.0.1:9666/api/add_download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request.payload),
    })
      .then((res) => res.json())
      .then((data) => {
        sendResponse({ success: true, response: data });
      })
      .catch((httpErr) => {
        chrome.runtime.sendNativeMessage("resilient_downloader", request.payload, (response) => {
          if (chrome.runtime.lastError) {
            sendResponse({ success: false, error: httpErr.message || chrome.runtime.lastError.message });
          } else {
            sendResponse({ success: true, response: response });
          }
        });
      });
    return true;
  }
});

// Setup context menus to allow direct download of any link or magnet
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "getly-download-link",
    title: "Scarica con Getly",
    contexts: ["link"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "getly-download-link" && info.linkUrl) {
    const url = info.linkUrl;
    let cookieString = "";
    if (url.startsWith("http://") || url.startsWith("https://")) {
      try {
        const cookies = await chrome.cookies.getAll({ url: url });
        if (cookies && cookies.length > 0) {
          cookieString = cookies.map((c) => `${c.name}=${c.value}`).join("; ");
        }
      } catch (_) {}
    }

    const payload = {
      url: url,
      referer: tab ? tab.url : "",
      user_agent: navigator.userAgent,
      cookies: cookieString,
    };

    fetch("http://127.0.0.1:9666/api/add_download", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch((err) => {
      console.warn("[Getly] Error forwarding context menu link:", err);
    });
  }
});
