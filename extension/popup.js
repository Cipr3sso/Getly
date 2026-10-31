document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("interceptToggle");
  const hostStatus = document.getElementById("hostStatus");
  const daemonStatus = document.getElementById("daemonStatus");
  const manualUrl = document.getElementById("manualUrl");
  const sendBtn = document.getElementById("sendBtn");
  const feedback = document.getElementById("feedback");

  // Load toggle state
  chrome.storage.local.get({ enabled: true }, (items) => {
    toggle.checked = items.enabled !== false;
  });

  toggle.addEventListener("change", () => {
    chrome.storage.local.set({ enabled: toggle.checked });
  });

  // Check Native Messaging Host
  chrome.runtime.sendMessage({ type: "CHECK_NATIVE_HOST" }, (res) => {
    if (res && res.connected) {
      hostStatus.textContent = "Connesso";
      hostStatus.className = "status-badge online";
    } else {
      hostStatus.textContent = "Non Connesso";
      hostStatus.className = "status-badge offline";
    }
  });

  // Check local CNL / daemon endpoint (port 9666)
  fetch("http://127.0.0.1:9666/flash/check")
    .then((r) => r.text())
    .then((t) => {
      if (t.includes("success")) {
        daemonStatus.textContent = "In Ascolto (9666)";
        daemonStatus.className = "status-badge online";
      } else {
        daemonStatus.textContent = "Offline (Fallback DB)";
        daemonStatus.className = "status-badge offline";
      }
    })
    .catch(() => {
      daemonStatus.textContent = "Offline (Fallback DB)";
      daemonStatus.className = "status-badge offline";
    });

  // Manual URL send
  sendBtn.addEventListener("click", () => {
    const url = manualUrl.value.trim();
    if (!url || (!url.startsWith("http://") && !url.startsWith("https://"))) {
      showFeedback("Inserisci un URL http:// o https:// valido", false);
      return;
    }

    sendBtn.disabled = true;
    showFeedback("Invio in corso...", true);

    chrome.runtime.sendMessage(
      {
        type: "MANUAL_DOWNLOAD",
        payload: {
          url: url,
          user_agent: navigator.userAgent,
        },
      },
      (res) => {
        sendBtn.disabled = false;
        if (res && res.success && res.response && res.response.status === "ok") {
          showFeedback("Download aggiunto con successo!", true);
          manualUrl.value = "";
        } else {
          showFeedback("Errore nell'invio al download manager", false);
        }
      }
    );
  });

  function showFeedback(msg, isSuccess) {
    feedback.textContent = msg;
    feedback.className = "feedback-msg " + (isSuccess ? "success" : "error");
  }
});
