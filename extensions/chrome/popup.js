document.addEventListener("DOMContentLoaded", async () => {
  const statusBadge = document.getElementById("statusBadge");
  const statusText = document.getElementById("statusText");
  const interceptionToggle = document.getElementById("interceptionToggle");
  const mediaCountBadge = document.getElementById("mediaCountBadge");
  const mediaList = document.getElementById("mediaList");
  const emptyMedia = document.getElementById("emptyMedia");
  const bulkActions = document.getElementById("bulkActions");
  const downloadAllBtn = document.getElementById("downloadAllBtn");
  const clearMediaBtn = document.getElementById("clearMediaBtn");
  const toast = document.getElementById("toast");

  let activeTab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    activeTab = tabs[0];
  } catch (err) {
    console.error("Failed to query active tab:", err);
  }

  // 1. Load & Bind Interception Toggle
  chrome.storage.local.get({ interceptionEnabled: true }, (items) => {
    interceptionToggle.checked = items.interceptionEnabled !== false;
  });

  interceptionToggle.addEventListener("change", () => {
    const isChecked = interceptionToggle.checked;
    chrome.storage.local.set({ interceptionEnabled: isChecked }, () => {
      showToast(isChecked ? "Cattura download abilitata" : "Cattura download disabilitata");
    });
  });

  // 2. Check Getly Desktop Daemon Health
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

  // 3. Load Captured Media for Current Tab
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
      // Remove any rendered media cards
      document.querySelectorAll(".media-item").forEach((el) => el.remove());
      return;
    }

    emptyMedia.classList.add("hidden");
    bulkActions.classList.remove("hidden");

    // Clean existing
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

  // 4. Download Single Media Item
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

  // 5. Bulk Download All Media
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

  // 6. Clear Tab Media
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

  // Helper Toast
  let toastTimer = null;
  function showToast(message, isError = false) {
    if (toastTimer) clearTimeout(toastTimer);
    toast.textContent = message;
    toast.className = `toast ${isError ? "error" : ""}`;
    toastTimer = setTimeout(() => {
      toast.className = "toast hidden";
    }, 2800);
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
