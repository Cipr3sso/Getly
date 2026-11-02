/**
 * Getly - Chrome / Edge Extension Background Service Worker (Manifest V3)
 * Provides native download interception, real-time video/audio sniffing,
 * context menu actions, and communication with the Getly local daemon (port 9666).
 */

const GETLY_API_BASE = "http://127.0.0.1:9666";
const bypassedUrls = new Set();
let interceptionEnabled = true;

// Media sniffer in-memory registry grouped by tabId
// Map<tabId, Array<MediaItem>>
const mediaByTab = new Map();

/**
 * Extracts root/base domain for capturing wildcard & Cloudflare cookies (e.g. .testfile.org).
 */
function getBaseDomain(hostname) {
  if (!hostname) return "";
  const cleanHost = hostname.toLowerCase();
  const parts = cleanHost.split(".");
  if (parts.length <= 2) return cleanHost;
  const secondLevelTLDs = ["co", "com", "net", "org", "edu", "gov", "mil"];
  if (parts.length > 2 && secondLevelTLDs.includes(parts[parts.length - 2])) {
    return parts.slice(-3).join(".");
  }
  return parts.slice(-2).join(".");
}

/**
 * Extracts full cookie header querying URL origin, target URL, and base domain,
 * including Partitioned cookies (CHIPS / partitionKey: {}) to capture essential
 * Cloudflare tokens (cf_clearance, __cf_bm, session cookies) in modern Chromium.
 */
async function extractFullCookieHeader(targetUrl, referrerUrl) {
  const cookieMap = new Map();

  const addCookies = (cookies) => {
    if (!cookies || !Array.isArray(cookies)) return;
    for (const c of cookies) {
      if (c && c.name && c.value !== undefined && !cookieMap.has(c.name)) {
        cookieMap.set(c.name, c.value);
      }
    }
  };

  const queryWithPartitions = async (filter) => {
    try {
      // 1. Try with partitionKey: {} (captures CHIPS partitioned cookies like cf_clearance in Chrome 120+)
      const partitioned = await chrome.cookies.getAll({ ...filter, partitionKey: {} });
      addCookies(partitioned);
    } catch (_) {}
    try {
      // 2. Also try standard unpartitioned
      const standard = await chrome.cookies.getAll(filter);
      addCookies(standard);
    } catch (_) {}
  };

  try {
    // 1. Target URL direct & origin
    if (targetUrl && (targetUrl.startsWith("http://") || targetUrl.startsWith("https://"))) {
      await queryWithPartitions({ url: targetUrl });

      try {
        const parsedTarget = new URL(targetUrl);
        await queryWithPartitions({ url: parsedTarget.origin });

        const baseDomain = getBaseDomain(parsedTarget.hostname);
        if (baseDomain) {
          await queryWithPartitions({ domain: baseDomain });
        }
      } catch (_) {}
    }

    // 2. Referrer URL direct & origin
    if (referrerUrl && (referrerUrl.startsWith("http://") || referrerUrl.startsWith("https://"))) {
      await queryWithPartitions({ url: referrerUrl });

      try {
        const parsedRef = new URL(referrerUrl);
        await queryWithPartitions({ url: parsedRef.origin });

        const refBaseDomain = getBaseDomain(parsedRef.hostname);
        if (refBaseDomain) {
          await queryWithPartitions({ domain: refBaseDomain });
        }
      } catch (_) {}
    }

    // 3. Global partitioned scan for domain & subdomains (handles .testfile.org, files.testfile.org, etc.)
    try {
      const parsed = targetUrl ? new URL(targetUrl) : (referrerUrl ? new URL(referrerUrl) : null);
      if (parsed) {
        const baseDomain = getBaseDomain(parsed.hostname);
        if (baseDomain) {
          const allPartitioned = await chrome.cookies.getAll({ partitionKey: {} });
          const matched = allPartitioned.filter(c =>
            c.domain && (
              c.domain === baseDomain ||
              c.domain === `.${baseDomain}` ||
              c.domain.endsWith(`.${baseDomain}`) ||
              c.domain.includes(baseDomain)
            )
          );
          addCookies(matched);
        }
      }
    } catch (_) {}
  } catch (err) {
    console.warn("[Getly] extractFullCookieHeader error:", err);
  }

  if (cookieMap.size === 0) return "";
  return Array.from(cookieMap.entries())
    .map(([k, v]) => `${k}=${v}`)
    .join("; ");
}

// ---------------------------------------------------------------------------
// 1. Settings Initialization & State Sync
// ---------------------------------------------------------------------------
chrome.storage.local.get({ interceptionEnabled: true }, (items) => {
  interceptionEnabled = items.interceptionEnabled !== false;
  console.log("[Getly Background] Initialized. Interception enabled:", interceptionEnabled);
});

chrome.storage.onChanged.addListener((changes, area) => {
  if (area === "local" && changes.interceptionEnabled !== undefined) {
    interceptionEnabled = changes.interceptionEnabled.newValue;
    console.log("[Getly Background] Interception toggled:", interceptionEnabled);
  }
});

// ---------------------------------------------------------------------------
// 2. Native Browser Download Interception (chrome.downloads.onCreated)
// ---------------------------------------------------------------------------
chrome.downloads.onCreated.addListener(async (downloadItem) => {
  if (!interceptionEnabled) {
    return;
  }

  // Ignore finished, failed or historical downloads from browser history
  if (downloadItem.state && downloadItem.state !== "in_progress") {
    return;
  }
  if (downloadItem.startTime) {
    const startTime = new Date(downloadItem.startTime).getTime();
    if (!isNaN(startTime) && Date.now() - startTime > 5000) {
      return;
    }
  }

  const rawUrl = downloadItem.url || "";
  const finalUrl = downloadItem.finalUrl || "";
  const url = finalUrl || rawUrl;
  if (!url) return;

  // Never cancel or intercept blob, data, filesystem or internal extension URLs
  if (
    rawUrl.startsWith("blob:") ||
    rawUrl.startsWith("data:") ||
    rawUrl.startsWith("filesystem:") ||
    url.startsWith("chrome:") ||
    url.startsWith("chrome-extension:") ||
    url.startsWith("edge:") ||
    url.includes("127.0.0.1:9666") ||
    url.includes("localhost:9666")
  ) {
    return;
  }

  // Only intercept HTTP/HTTPS downloads
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return;
  }

  // Filter out webpage resources like HTML, CSS, Favicons
  const mime = (downloadItem.mime || "").toLowerCase();
  if (mime === "text/html" || mime === "text/css" || mime === "image/x-icon") {
    return;
  }

  // Check if re-triggered as fallback after Getly was unreachable
  if (bypassedUrls.has(url)) {
    bypassedUrls.delete(url);
    console.log("[Getly] Bypassing re-triggered fallback download for:", url);
    return;
  }

  // Cancel native download immediately to prevent browser save dialog
  chrome.downloads.cancel(downloadItem.id, () => {
    if (chrome.runtime.lastError) {
      // Ignored
    }
    chrome.downloads.erase({ id: downloadItem.id });
  });

  // Extract clean filename
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
    filename = filename.split(/[\\/]/).pop().split("?")[0];
  }

  // Extract preliminary file size reported by the browser (if known and > 0)
  const fileSize = downloadItem.fileSize && downloadItem.fileSize > 0 ? downloadItem.fileSize : null;

  // Effective Referer resolution: prioritize downloadItem.referrer, then active tab URL, then URL origin
  let effectiveReferer = downloadItem.referrer || "";
  if (!effectiveReferer) {
    try {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      if (activeTab && activeTab.url && activeTab.url.startsWith("http")) {
        effectiveReferer = activeTab.url;
      }
    } catch (_) {}
  }
  if (!effectiveReferer && url) {
    try {
      const parsed = new URL(url);
      effectiveReferer = `${parsed.origin}/`;
    } catch (_) {}
  }

  // Collect session & Cloudflare cookies (cf_clearance on base domain and origin)
  const cookieString = await extractFullCookieHeader(url, effectiveReferer);

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
      "Sec-Fetch-Dest": "document",
      "Sec-Fetch-Mode": "navigate",
      "Sec-Fetch-Site": "same-site",
      "Sec-Fetch-User": "?1",
      "Upgrade-Insecure-Requests": "1",
    },
    category: detectCategory(filename, mime),
    auto_start: true,
  };

  console.log("[Getly] Forwarding intercepted download to daemon:", payload);

  // Send to Getly Local Server
  try {
    const response = await fetch(`${GETLY_API_BASE}/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!response.ok) {
      throw new Error(`Local daemon returned status ${response.status}`);
    }

    const data = await response.json();
    console.log("[Getly] Download queued successfully:", data);
  } catch (err) {
    console.warn("[Getly] Local daemon offline. Falling back to browser download...", err);

    // Re-trigger standard browser download
    bypassedUrls.add(url);
    chrome.downloads.download({
      url: url,
      filename: filename || undefined,
      conflictAction: "uniquify",
    });
  }
});

// ---------------------------------------------------------------------------
// 3. Media & Video Sniffer (chrome.webRequest.onResponseStarted)
// ---------------------------------------------------------------------------
const MEDIA_EXTENSIONS = [
  ".mp4", ".m4v", ".mkv", ".webm", ".flv", ".avi", ".mov", ".wmv",
  ".m3u8", ".mpd", ".ts",
  ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".wav", ".opus"
];

function isMediaResponse(url, contentType) {
  if (!url || url.startsWith("blob:") || url.startsWith("data:")) return false;
  const lowerUrl = url.toLowerCase();
  const lowerType = (contentType || "").toLowerCase();

  // Media MIME types
  if (
    lowerType.startsWith("video/") ||
    lowerType.startsWith("audio/") ||
    lowerType.includes("application/x-mpegurl") ||
    lowerType.includes("application/vnd.apple.mpegurl") ||
    lowerType.includes("application/dash+xml")
  ) {
    return true;
  }

  // Extensions
  for (const ext of MEDIA_EXTENSIONS) {
    if (lowerUrl.includes(ext)) {
      return true;
    }
  }

  return false;
}

function getFormatBadge(url, contentType) {
  const lowerUrl = (url || "").toLowerCase();
  const lowerType = (contentType || "").toLowerCase();

  if (lowerUrl.includes(".m3u8") || lowerType.includes("mpegurl")) return "HLS";
  if (lowerUrl.includes(".mpd") || lowerType.includes("dash")) return "DASH";
  if (lowerUrl.includes(".mp4") || lowerType.includes("mp4")) return "MP4";
  if (lowerUrl.includes(".webm") || lowerType.includes("webm")) return "WEBM";
  if (lowerUrl.includes(".mkv")) return "MKV";
  if (lowerUrl.includes(".mp3") || lowerType.includes("audio/mpeg")) return "MP3";
  if (lowerType.startsWith("audio/")) return "AUDIO";
  return "MEDIA";
}

function formatBytes(bytes) {
  if (!bytes || bytes <= 0) return "Stream live";
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  let val = bytes;
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024;
    i++;
  }
  return `${val.toFixed(1)} ${units[i]}`;
}

chrome.webRequest.onResponseStarted.addListener(
  (details) => {
    // Only capture from active browser tabs and standard HTTP/HTTPS
    if (details.tabId < 0 || !details.url.startsWith("http")) return;
    if (details.url.includes("127.0.0.1:9666") || details.url.includes("localhost:9666")) return;

    let contentType = "";
    let contentLength = 0;

    if (details.responseHeaders) {
      for (const h of details.responseHeaders) {
        const name = h.name.toLowerCase();
        if (name === "content-type") {
          contentType = h.value;
        } else if (name === "content-length") {
          contentLength = parseInt(h.value, 10) || 0;
        }
      }
    }

    if (!isMediaResponse(details.url, contentType)) {
      return;
    }

    // Skip short tracking segments or manifest refreshes if already present
    const cleanUrl = details.url.split("?")[0];
    const tabList = mediaByTab.get(details.tabId) || [];

    // Deduplicate same URL
    if (tabList.some((item) => item.url.split("?")[0] === cleanUrl)) {
      return;
    }

    let filename = "";
    try {
      const parsed = new URL(details.url);
      const name = parsed.pathname.split("/").pop();
      if (name && name.includes(".")) {
        filename = decodeURIComponent(name).split("?")[0];
      }
    } catch (_) {}
    if (!filename) {
      const badge = getFormatBadge(details.url, contentType).toLowerCase();
      filename = `stream_${details.tabId}_${tabList.length + 1}.${badge === "hls" ? "m3u8" : badge}`;
    }

    const mediaItem = {
      id: "media_" + Math.random().toString(36).substr(2, 9),
      url: details.url,
      filename: filename,
      mimeType: contentType,
      sizeBytes: contentLength,
      formattedSize: formatBytes(contentLength),
      formatBadge: getFormatBadge(details.url, contentType),
      tabId: details.tabId,
      timestamp: Date.now(),
    };

    tabList.push(mediaItem);
    mediaByTab.set(details.tabId, tabList);

    // Update extension badge counter
    chrome.action.setBadgeText({
      tabId: details.tabId,
      text: tabList.length.toString(),
    });
    chrome.action.setBadgeBackgroundColor({
      tabId: details.tabId,
      color: "#2563EB",
    });
  },
  { urls: ["<all_urls>"] },
  ["responseHeaders"]
);

// Tab lifecycle: clear media list on navigation or tab close
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.status === "loading" && changeInfo.url) {
    mediaByTab.delete(tabId);
    chrome.action.setBadgeText({ tabId: tabId, text: "" });
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  mediaByTab.delete(tabId);
});

// ---------------------------------------------------------------------------
// 4. Context Menu Actions
// ---------------------------------------------------------------------------
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({
    id: "getly-download-link",
    title: "Scarica con Getly",
    contexts: ["link", "video", "audio", "image"],
  });

  chrome.contextMenus.create({
    id: "getly-download-all-media",
    title: "Scarica tutti i media della pagina",
    contexts: ["page", "action"],
  });
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId === "getly-download-link") {
    const targetUrl = info.linkUrl || info.srcUrl;
    if (!targetUrl) return;

    let effectiveReferer = tab?.url || "";
    if (!effectiveReferer && targetUrl.startsWith("http")) {
      try {
        const parsed = new URL(targetUrl);
        effectiveReferer = `${parsed.origin}/`;
      } catch (_) {}
    }

    const cookieString = await extractFullCookieHeader(targetUrl, effectiveReferer);

    const payload = {
      url: targetUrl,
      headers: {
        Referer: effectiveReferer,
        "User-Agent": navigator.userAgent,
        Cookie: cookieString,
      },
      category: detectCategory(targetUrl, ""),
      auto_start: true,
    };

    fetch(`${GETLY_API_BASE}/add`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    }).catch((err) => {
      console.warn("[Getly ContextMenu] Failed, falling back to browser download:", err);
      chrome.downloads.download({ url: targetUrl });
    });
  } else if (info.menuItemId === "getly-download-all-media" && tab?.id) {
    const tabMedia = mediaByTab.get(tab.id) || [];
    for (const item of tabMedia) {
      sendMediaToGetly(item, tab.url);
    }
  }
});

async function sendMediaToGetly(mediaItem, refererUrl) {
  let effectiveReferer = refererUrl || "";
  if (!effectiveReferer && mediaItem.url.startsWith("http")) {
    try {
      const parsed = new URL(mediaItem.url);
      effectiveReferer = `${parsed.origin}/`;
    } catch (_) {}
  }

  const cookieString = await extractFullCookieHeader(mediaItem.url, effectiveReferer);

  const payload = {
    url: mediaItem.url,
    filename: mediaItem.filename,
    category: "Video",
    headers: {
      Referer: effectiveReferer,
      "User-Agent": navigator.userAgent,
      Cookie: cookieString,
    },
    file_size: mediaItem.sizeBytes > 0 ? mediaItem.sizeBytes : null,
    auto_start: true,
  };

  return fetch(`${GETLY_API_BASE}/add`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  }).then((r) => r.json());
}

// ---------------------------------------------------------------------------
// 5. Popup Communication (Message Dispatcher)
// ---------------------------------------------------------------------------
chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.type === "CHECK_STATUS") {
    fetch(`${GETLY_API_BASE}/health`)
      .then((r) => r.json())
      .then((data) => sendResponse({ online: true, version: data.version, app: data.app }))
      .catch(() => sendResponse({ online: false }));
    return true;
  }

  if (request.type === "GET_TAB_MEDIA") {
    const list = mediaByTab.get(request.tabId) || [];
    sendResponse({ media: list });
    return false;
  }

  if (request.type === "DOWNLOAD_MEDIA_ITEM") {
    sendMediaToGetly(request.item, request.referer)
      .then((res) => sendResponse({ success: true, result: res }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (request.type === "DOWNLOAD_ALL_TAB_MEDIA") {
    const list = mediaByTab.get(request.tabId) || [];
    Promise.all(list.map((item) => sendMediaToGetly(item, request.referer)))
      .then((results) => sendResponse({ success: true, count: results.length }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }

  if (request.type === "CLEAR_TAB_MEDIA") {
    mediaByTab.delete(request.tabId);
    chrome.action.setBadgeText({ tabId: request.tabId, text: "" });
    sendResponse({ success: true });
    return false;
  }

  if (request.type === "DOWNLOAD_BATCH") {
    sendBatchToGetly(request.items, request.referer)
      .then((res) => sendResponse({ success: true, result: res, count: (request.items || []).length }))
      .catch((err) => sendResponse({ success: false, error: err.message }));
    return true;
  }
});

/**
 * Sends a batch of download items to Getly daemon POST /add_batch
 * Resolves full cookies and headers for each item.
 */
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
        file_size: item.file_size || null,
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

// Helper category classifier
function detectCategory(filename, mime) {
  const lower = (filename || "").toLowerCase();
  const lowerMime = (mime || "").toLowerCase();

  if (
    lowerMime.startsWith("video/") ||
    lowerMime.startsWith("audio/") ||
    lower.endsWith(".mp4") || lower.endsWith(".mkv") || lower.endsWith(".webm") ||
    lower.endsWith(".avi") || lower.endsWith(".mov") || lower.endsWith(".wmv") ||
    lower.endsWith(".flv") || lower.endsWith(".m4v") || lower.endsWith(".m3u8") ||
    lower.endsWith(".mpd") || lower.endsWith(".ts") || lower.endsWith(".mp3") ||
    lower.endsWith(".m4a") || lower.endsWith(".aac") || lower.endsWith(".flac") ||
    lower.endsWith(".wav") || lower.endsWith(".ogg") || lower.endsWith(".opus")
  ) {
    return "Video";
  }
  if (
    lower.endsWith(".zip") || lower.endsWith(".rar") || lower.endsWith(".7z") ||
    lower.endsWith(".tar") || lower.endsWith(".gz") || lower.endsWith(".tgz") ||
    lower.endsWith(".bz2") || lower.endsWith(".xz") || lower.endsWith(".7-zip") ||
    lower.includes(".tar.gz")
  ) {
    return "Archivi";
  }
  if (
    lower.endsWith(".exe") || lower.endsWith(".msi") || lower.endsWith(".apk") ||
    lower.endsWith(".dmg") || lower.endsWith(".pkg") || lower.endsWith(".deb") ||
    lower.endsWith(".rpm") || lower.endsWith(".iso") || lower.endsWith(".bin") ||
    lower.endsWith(".appimage")
  ) {
    return "Programmi";
  }
  if (
    lower.endsWith(".pdf") || lower.endsWith(".epub") || lower.endsWith(".mobi") ||
    lower.endsWith(".doc") || lower.endsWith(".docx") || lower.endsWith(".xls") ||
    lower.endsWith(".xlsx") || lower.endsWith(".ppt") || lower.endsWith(".pptx") ||
    lower.endsWith(".txt") || lower.endsWith(".rtf") || lower.endsWith(".odt") ||
    lower.endsWith(".csv")
  ) {
    return "Documenti";
  }
  return "Altro";
}

