/**
 * Getly Chrome / Edge Extension Popup Controller
 * Handles daemon status, media sniffer, page link grabber, category filtering,
 * and batch download dispatch to Getly local server.
 */

document.addEventListener("DOMContentLoaded", async () => {
  // Elements - Header & Status
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
  const interceptionToggle = document.getElementById("interceptionToggle");
  const toast = document.getElementById("toast");

  // Elements - Navigation Tabs
  const tabMediaBtn = document.getElementById("tabMediaBtn");
  const tabGrabberBtn = document.getElementById("tabGrabberBtn");
  const mediaSection = document.getElementById("mediaSection");
  const grabberSection = document.getElementById("grabberSection");

  // Elements - Media Sniffer
  const mediaCountBadge = document.getElementById("mediaCountBadge");
  const mediaList = document.getElementById("mediaList");
  const emptyMedia = document.getElementById("emptyMedia");
  const bulkActions = document.getElementById("bulkActions");
  const downloadAllBtn = document.getElementById("downloadAllBtn");
  const clearMediaBtn = document.getElementById("clearMediaBtn");

  // Elements - Link Grabber
  const scanPageLinksBtn = document.getElementById("scanPageLinksBtn");
  const grabberCountBadge = document.getElementById("grabberCountBadge");
  const categoryFilterBar = document.getElementById("categoryFilterBar");
  const selectAllLinks = document.getElementById("selectAllLinks");
  const selectedCountText = document.getElementById("selectedCountText");
  const grabberList = document.getElementById("grabberList");
  const emptyGrabber = document.getElementById("emptyGrabber");
  const grabberActions = document.getElementById("grabberActions");
  const downloadBatchBtn = document.getElementById("downloadBatchBtn");

  // Category counts
  const countTutti = document.getElementById("countTutti");
  const countArchivi = document.getElementById("countArchivi");
  const countDocumenti = document.getElementById("countDocumenti");
  const countVideo = document.getElementById("countVideo");
  const countProgrammi = document.getElementById("countProgrammi");

  // Grabber State
  let allGrabbedItems = [];
  let activeCategory = "Tutti";

  // Category file extensions mapping (longer extensions checked first)
  const CATEGORY_DEFINITIONS = {
    Archivi: [".tar.gz", ".7-zip", ".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"],
    Documenti: [".pdf", ".epub", ".mobi", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".txt", ".rtf", ".odt", ".csv"],
    Video: [".mp4", ".mkv", ".webm", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".m3u8", ".mpd", ".ts", ".mp3", ".m4a", ".aac", ".flac", ".wav", ".ogg", ".opus"],
    Programmi: [".exe", ".msi", ".apk", ".dmg", ".pkg", ".deb", ".rpm", ".iso", ".bin", ".appimage"],
  };

  let activeTab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    activeTab = tabs[0];
  } catch (err) {
    console.error("Failed to query active tab:", err);
  }

  // -------------------------------------------------------------------------
  // 1. Interception Toggle
  // -------------------------------------------------------------------------
  chrome.storage.local.get({ interceptionEnabled: true }, (items) => {
    interceptionToggle.checked = items.interceptionEnabled !== false;
  });

  interceptionToggle.addEventListener("change", () => {
    const isChecked = interceptionToggle.checked;
    chrome.storage.local.set({ interceptionEnabled: isChecked }, () => {
      showToast(isChecked ? "Cattura download abilitata" : "Cattura download disabilitata");
    });
  });

  // -------------------------------------------------------------------------
  // 2. Check Daemon Health
  // -------------------------------------------------------------------------
  checkDaemonHealth();

  function checkDaemonHealth() {
    chrome.runtime.sendMessage({ type: "CHECK_STATUS" }, (res) => {
      if (res && res.online) {
        statusBadge.className = "status-badge online";
        statusText.textContent = `Getly ${res.version || "v1.0"}`;
      } else {
        statusBadge.className = "status-badge offline";
        statusText.textContent = "Getly Offline";
      }
    });
  }

  // -------------------------------------------------------------------------
  // 3. Tab Switching Navigation
  // -------------------------------------------------------------------------
  tabMediaBtn.addEventListener("click", () => switchTab("media"));
  tabGrabberBtn.addEventListener("click", () => switchTab("grabber"));

  function switchTab(target) {
    if (target === "media") {
      tabMediaBtn.classList.add("active");
      tabGrabberBtn.classList.remove("active");
      mediaSection.classList.add("active");
      grabberSection.classList.remove("active");
    } else {
      tabGrabberBtn.classList.add("active");
      tabMediaBtn.classList.remove("active");
      grabberSection.classList.add("active");
      mediaSection.classList.remove("active");
    }
  }

  // -------------------------------------------------------------------------
  // 4. Media Sniffer Section
  // -------------------------------------------------------------------------
  if (activeTab && activeTab.id) {
    loadTabMedia(activeTab.id);
  }

  function loadTabMedia(tabId) {
    chrome.runtime.sendMessage({ type: "GET_TAB_MEDIA", tabId: tabId }, (res) => {
      const list = (res && res.media) || [];
      renderMediaList(list);
    });
  }

  function renderMediaList(items) {
    mediaCountBadge.textContent = items.length;

    if (items.length === 0) {
      emptyMedia.classList.remove("hidden");
      bulkActions.classList.add("hidden");
      document.querySelectorAll(".media-item").forEach((el) => el.remove());
      return;
    }

    emptyMedia.classList.add("hidden");
    bulkActions.classList.remove("hidden");
    document.querySelectorAll(".media-item").forEach((el) => el.remove());

    items.forEach((item) => {
      const card = document.createElement("div");
      card.className = "media-item";

      const badgeClass = (item.formatBadge || "media").toLowerCase();

      card.innerHTML = `
        <div class="media-meta">
          <span class="media-name" title="${escapeHtml(item.filename || item.url)}">
            ${escapeHtml(item.filename || "Flusso multimediale")}
          </span>
          <div class="media-details">
            <span class="badge ${badgeClass}">${escapeHtml(item.formatBadge || "MEDIA")}</span>
            <span class="media-size">${escapeHtml(item.formattedSize || "")}</span>
          </div>
        </div>
        <button class="btn btn-sm" data-id="${item.id}">⬇ Scarica</button>
      `;

      card.querySelector("button").addEventListener("click", () => {
        downloadSingleItem(item);
      });

      mediaList.appendChild(card);
    });
  }

  function downloadSingleItem(item) {
    chrome.runtime.sendMessage(
      {
        type: "DOWNLOAD_MEDIA_ITEM",
        item: item,
        referer: activeTab ? activeTab.url : "",
      },
      (res) => {
        if (res && res.success) {
          showToast(`Inviato a Getly: ${item.filename}`);
        } else {
          showToast(`Errore: Getly demone offline`, true);
        }
      }
    );
  }

  downloadAllBtn.addEventListener("click", () => {
    if (!activeTab || !activeTab.id) return;

    chrome.runtime.sendMessage(
      {
        type: "DOWNLOAD_ALL_TAB_MEDIA",
        tabId: activeTab.id,
        referer: activeTab.url,
      },
      (res) => {
        if (res && res.success) {
          showToast(`Inviati ${res.count} stream multimediali a Getly!`);
        } else {
          showToast(`Errore durante l'invio a Getly`, true);
        }
      }
    );
  });

  clearMediaBtn.addEventListener("click", () => {
    if (!activeTab || !activeTab.id) return;

    chrome.runtime.sendMessage(
      {
        type: "CLEAR_TAB_MEDIA",
        tabId: activeTab.id,
      },
      () => {
        renderMediaList([]);
        showToast("Lista media azzerata");
      }
    );
  });

  // -------------------------------------------------------------------------
  // 5. Page Link Grabber Section
  // -------------------------------------------------------------------------
  scanPageLinksBtn.addEventListener("click", async () => {
    if (!activeTab || !activeTab.id) {
      showToast("Nessuna scheda attiva trovata", true);
      return;
    }

    if (
      !activeTab.url ||
      activeTab.url.startsWith("chrome://") ||
      activeTab.url.startsWith("chrome-extension://") ||
      activeTab.url.startsWith("edge://") ||
      activeTab.url.startsWith("about:") ||
      activeTab.url.startsWith("view-source:")
    ) {
      showToast("Impossibile scansionare pagine di sistema del browser", true);
      return;
    }

    scanPageLinksBtn.disabled = true;
    scanPageLinksBtn.textContent = "⏳ Analisi in corso...";

    try {
      const injectionResults = await chrome.scripting.executeScript({
        target: { tabId: activeTab.id },
        func: extractPageLinksInContent,
      });

      const extracted = (injectionResults && injectionResults[0] && injectionResults[0].result) || [];
      processExtractedLinks(extracted);

      switchTab("grabber");
      showToast(`Trovati ${allGrabbedItems.length} file scaricabili`);
    } catch (err) {
      console.warn("[Getly Popup] Scripting execution failed:", err);
      showToast("Impossibile analizzare i link di questa pagina", true);
    } finally {
      scanPageLinksBtn.disabled = false;
      scanPageLinksBtn.textContent = "🔍 Analizza tutti i link della pagina";
    }
  });

  /**
   * Pure content script function executed in the active webpage DOM.
   * Extracts links from <a>, <video>, <audio>, and <source> tags.
   */
  function extractPageLinksInContent() {
    const extracted = [];
    const seenUrls = new Set();

    function addUrl(rawUrl, tag) {
      if (!rawUrl || typeof rawUrl !== "string") return;
      const trimmed = rawUrl.trim();
      if (!trimmed) return;
      if (
        trimmed.startsWith("javascript:") ||
        trimmed.startsWith("mailto:") ||
        trimmed.startsWith("tel:") ||
        trimmed.startsWith("data:") ||
        trimmed.startsWith("blob:") ||
        trimmed.startsWith("about:")
      ) {
        return;
      }

      try {
        const fullUrl = new URL(trimmed, document.baseURI).href;
        if (!fullUrl.startsWith("http://") && !fullUrl.startsWith("https://")) {
          return;
        }
        if (seenUrls.has(fullUrl)) return;
        seenUrls.add(fullUrl);

        extracted.push({ url: fullUrl, tag: tag });
      } catch (_) {}
    }

    // 1. <a> tags
    document.querySelectorAll("a[href]").forEach((el) => {
      addUrl(el.getAttribute("href") || el.href, "a");
    });

    // 2. <video> tags
    document.querySelectorAll("video").forEach((el) => {
      addUrl(el.getAttribute("src") || el.src || el.currentSrc, "video");
    });

    // 3. <audio> tags
    document.querySelectorAll("audio").forEach((el) => {
      addUrl(el.getAttribute("src") || el.src || el.currentSrc, "audio");
    });

    // 4. <source> tags
    document.querySelectorAll("source[src]").forEach((el) => {
      addUrl(el.getAttribute("src") || el.src, "source");
    });

    return extracted;
  }

  /**
   * Classifies extracted links by file extension and assigns categories.
   */
  function processExtractedLinks(extractedList) {
    const items = [];
    const seenUrls = new Set();

    for (const entry of extractedList) {
      const url = entry.url;
      if (seenUrls.has(url)) continue;

      const parsed = parseDownloadableItem(url);
      if (parsed) {
        seenUrls.add(url);
        items.push(parsed);
      }
    }

    allGrabbedItems = items;
    updateCategoryCounts();
    renderGrabberList();
  }

  /**
   * Matches URL or filename against downloadable extensions.
   */
  function parseDownloadableItem(rawUrl) {
    try {
      const parsed = new URL(rawUrl);
      let pathname = parsed.pathname || "";
      pathname = pathname.replace(/\/+$/, "");
      let rawFilename = pathname.split("/").pop() || "";
      rawFilename = decodeURIComponent(rawFilename).split("?")[0].split("#")[0];

      // 1. Direct match on pathname filename
      const lowerName = rawFilename.toLowerCase();
      for (const [category, extList] of Object.entries(CATEGORY_DEFINITIONS)) {
        for (const ext of extList) {
          if (lowerName.endsWith(ext)) {
            return {
              url: rawUrl,
              filename: rawFilename,
              extension: ext.replace(/^\./, "").toUpperCase(),
              category: category,
              selected: true,
            };
          }
        }
      }

      // 2. Fallback check on query params (e.g. ?file=archive.zip or ?file-url=https://.../foo.mp4)
      const IGNORED_PARAMS = new Set([
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "ref", "referrer", "affiliate", "subid", "session_id", "analytics", "onboarding-flow"
      ]);
      const VALID_FILE_PARAM_KEYS = new Set([
        "file", "url", "file-url", "file_url", "download", "download_url",
        "link", "src", "target", "uri", "path", "media", "attachment", "source"
      ]);

      for (const [paramKey, paramVal] of parsed.searchParams.entries()) {
        if (!paramVal) continue;
        const lowerKey = paramKey.toLowerCase();
        if (lowerKey.startsWith("utm_") || IGNORED_PARAMS.has(lowerKey)) {
          continue;
        }

        const decoded = decodeURIComponent(paramVal);
        let isAbsoluteInnerUrl = false;
        let innerUrlHref = "";
        try {
          const testUrl = new URL(decoded);
          if (testUrl.protocol === "http:" || testUrl.protocol === "https:") {
            isAbsoluteInnerUrl = true;
            innerUrlHref = testUrl.href;
          }
        } catch (_) {}

        // Accept if param key is a known file parameter or if param contains an absolute URL
        if (!isAbsoluteInnerUrl && !VALID_FILE_PARAM_KEYS.has(lowerKey)) {
          continue;
        }

        const cleanParam = decoded.split("?")[0].split("#")[0];
        const paramName = cleanParam.split("/").pop() || "";
        const lowerParam = paramName.toLowerCase();
        for (const [category, extList] of Object.entries(CATEGORY_DEFINITIONS)) {
          for (const ext of extList) {
            if (lowerParam.endsWith(ext)) {
              const effectiveUrl = isAbsoluteInnerUrl ? innerUrlHref : rawUrl;
              return {
                url: effectiveUrl,
                filename: paramName,
                extension: ext.replace(/^\./, "").toUpperCase(),
                category: category,
                selected: true,
              };
            }
          }
        }
      }
    } catch (_) {}

    return null;
  }

  function updateCategoryCounts() {
    const counts = {
      Tutti: allGrabbedItems.length,
      Archivi: 0,
      Documenti: 0,
      Video: 0,
      Programmi: 0,
    };

    allGrabbedItems.forEach((item) => {
      if (counts[item.category] !== undefined) {
        counts[item.category]++;
      }
    });

    grabberCountBadge.textContent = counts.Tutti;
    countTutti.textContent = `(${counts.Tutti})`;
    countArchivi.textContent = `(${counts.Archivi})`;
    countDocumenti.textContent = `(${counts.Documenti})`;
    countVideo.textContent = `(${counts.Video})`;
    countProgrammi.textContent = `(${counts.Programmi})`;
  }

  // Category Pills Filtering
  categoryFilterBar.addEventListener("click", (e) => {
    const pill = e.target.closest(".filter-pill");
    if (!pill) return;

    categoryFilterBar.querySelectorAll(".filter-pill").forEach((p) => p.classList.remove("active"));
    pill.classList.add("active");

    activeCategory = pill.getAttribute("data-category") || "Tutti";
    renderGrabberList();
  });

  function getVisibleItems() {
    if (activeCategory === "Tutti") {
      return allGrabbedItems;
    }
    return allGrabbedItems.filter((i) => i.category === activeCategory);
  }

  function renderGrabberList() {
    const visibleItems = getVisibleItems();

    // Clean existing
    grabberList.querySelectorAll(".grabber-item").forEach((el) => el.remove());

    if (visibleItems.length === 0) {
      emptyGrabber.classList.remove("hidden");
      grabberActions.classList.add("hidden");
      updateSelectionSummary();
      return;
    }

    emptyGrabber.classList.add("hidden");
    grabberActions.classList.remove("hidden");

    visibleItems.forEach((item) => {
      const card = document.createElement("div");
      card.className = "grabber-item";

      const badgeCategoryClass = (item.category || "altro").toLowerCase();

      card.innerHTML = `
        <div class="grabber-item-checkbox">
          <input type="checkbox" class="link-checkbox" ${item.selected ? "checked" : ""} />
        </div>
        <div class="grabber-item-meta" title="${escapeHtml(item.url)}">
          <span class="grabber-item-name">${escapeHtml(item.filename)}</span>
          <div class="grabber-item-details">
            <span class="badge ${badgeCategoryClass}">${escapeHtml(item.extension)}</span>
            <span class="grabber-item-url">${escapeHtml(formatShortUrl(item.url))}</span>
          </div>
        </div>
      `;

      const checkbox = card.querySelector(".link-checkbox");
      checkbox.addEventListener("change", () => {
        item.selected = checkbox.checked;
        updateSelectionSummary();
      });

      grabberList.appendChild(card);
    });

    updateSelectionSummary();
  }

  // Select All Checkbox Handler
  selectAllLinks.addEventListener("change", () => {
    const visibleItems = getVisibleItems();
    const isChecked = selectAllLinks.checked;

    visibleItems.forEach((item) => {
      item.selected = isChecked;
    });

    grabberList.querySelectorAll(".link-checkbox").forEach((cb) => {
      cb.checked = isChecked;
    });

    updateSelectionSummary();
  });

  function updateSelectionSummary() {
    const visibleItems = getVisibleItems();
    const totalSelected = allGrabbedItems.filter((i) => i.selected).length;

    selectedCountText.textContent = `${totalSelected} selezionati`;

    if (visibleItems.length === 0) {
      selectAllLinks.checked = false;
      selectAllLinks.indeterminate = false;
    } else {
      const visibleSelected = visibleItems.filter((i) => i.selected).length;
      if (visibleSelected === visibleItems.length) {
        selectAllLinks.checked = true;
        selectAllLinks.indeterminate = false;
      } else if (visibleSelected === 0) {
        selectAllLinks.checked = false;
        selectAllLinks.indeterminate = false;
      } else {
        selectAllLinks.checked = false;
        selectAllLinks.indeterminate = true;
      }
    }

    downloadBatchBtn.disabled = totalSelected === 0;
    downloadBatchBtn.textContent = `⬇️ Scarica selezionati con Getly (${totalSelected})`;
  }

  // -------------------------------------------------------------------------
  // 6. Batch Download Action
  // -------------------------------------------------------------------------
  downloadBatchBtn.addEventListener("click", () => {
    const selectedItems = allGrabbedItems.filter((i) => i.selected);
    if (selectedItems.length === 0) {
      showToast("Nessun link selezionato", true);
      return;
    }

    downloadBatchBtn.disabled = true;
    downloadBatchBtn.textContent = "⏳ Invio a Getly in corso...";

    chrome.runtime.sendMessage(
      {
        type: "DOWNLOAD_BATCH",
        items: selectedItems.map((item) => ({
          url: item.url,
          filename: item.filename,
          category: item.category,
        })),
        referer: activeTab ? activeTab.url : "",
      },
      (res) => {
        downloadBatchBtn.disabled = false;
        downloadBatchBtn.textContent = `⬇️ Scarica selezionati con Getly (${selectedItems.length})`;

        if (res && res.success) {
          showToast(`Inviati ${res.count || selectedItems.length} file a Getly!`);
        } else {
          showToast(`Errore: ${res?.error || "Getly demone offline"}`, true);
        }
      }
    );
  });

  // -------------------------------------------------------------------------
  // Helpers
  // -------------------------------------------------------------------------
  let toastTimer = null;
  function showToast(message, isError = false) {
    if (toastTimer) clearTimeout(toastTimer);
    toast.textContent = message;
    toast.className = `toast ${isError ? "error" : ""}`;
    toastTimer = setTimeout(() => {
      toast.className = "toast hidden";
    }, 2800);
  }

  function formatShortUrl(fullUrl) {
    if (!fullUrl) return "";
    try {
      const parsed = new URL(fullUrl);
      return `${parsed.hostname}${parsed.pathname}`;
    } catch (_) {
      return fullUrl;
    }
  }

  function escapeHtml(str) {
    if (!str) return "";
    return str
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
});
