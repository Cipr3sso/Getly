/**
 * Getly - Video Download Floating Panel (Content Script)
 * Automatically detects HTML5 video players (YouTube, Vimeo, Twitch, and web videos)
 * and displays an elegant, acrylic floating download bar inspired by IDM,
 * styled with modern Fluent / WinUI 3 aesthetics inside an isolated Shadow DOM.
 */

(function () {
  "use strict";

  // Prevent multiple injections in the same frame
  if (window.__GETLY_CONTENT_SCRIPT_LOADED__) return;
  window.__GETLY_CONTENT_SCRIPT_LOADED__ = true;

  let floatingButtonEnabled = true;

  // Retrieve user settings
  if (typeof chrome !== "undefined" && chrome.storage && chrome.storage.local) {
    chrome.storage.local.get(["floatingButtonEnabled"], (res) => {
      if (res && res.floatingButtonEnabled !== undefined) {
        floatingButtonEnabled = res.floatingButtonEnabled;
      }
    });

    chrome.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.floatingButtonEnabled) {
        floatingButtonEnabled = changes.floatingButtonEnabled.newValue !== false;
        toggleAllPanelsVisibility(floatingButtonEnabled);
      }
    });
  }

  // Registry of attached video elements to avoid duplicate panels
  // WeakMap<HTMLVideoElement, VideoPanelInstance>
  const activePanels = new WeakMap();

  /**
   * Helper to safely extract video title from page
   */
  function extractVideoTitle() {
    // 1. YouTube specific title
    const ytTitle = document.querySelector("h1.ytd-watch-metadata yt-formatted-string, #title h1 yt-formatted-string, ytd-reel-player-header-renderer h2");
    if (ytTitle && ytTitle.textContent.trim()) {
      return ytTitle.textContent.trim();
    }

    // 2. OpenGraph or Twitter meta tag
    const ogTitle = document.querySelector('meta[property="og:title"], meta[name="twitter:title"]');
    if (ogTitle && ogTitle.content && ogTitle.content.trim()) {
      return ogTitle.content.trim();
    }

    // 3. Document title cleaned from common branding suffixes
    let title = document.title || "video";
    title = title.replace(/\s*-\s*YouTube$/, "").replace(/\s*-\s*Vimeo$/, "").trim();
    return title;
  }

  /**
   * Helper to resolve canonical video URL
   */
  function resolveVideoTargetUrl(video) {
    const loc = window.location.href;

    // On YouTube, watch & shorts URLs are the source of truth for yt-dlp
    if (loc.includes("youtube.com/watch") || loc.includes("youtube.com/shorts") || loc.includes("youtu.be/")) {
      return loc;
    }

    // Generic direct source if present and not blob
    const src = video.currentSrc || video.src || "";
    if (src && !src.startsWith("blob:") && !src.startsWith("data:")) {
      return src;
    }

    // Fallback to page URL (e.g. for Vimeo, Twitter, Twitch, TikTok, Dailymotion)
    return loc;
  }

  /**
   * VideoPanelInstance represents the floating bar attached to a specific video
   */
  class VideoPanelInstance {
    constructor(video) {
      this.video = video;
      this.host = null;
      this.shadow = null;
      this.hideTimer = null;
      this.isMenuOpen = false;
      this.isMouseOverPanel = false;
      this.isDismissed = false;

      this.init();
    }

    init() {
      // Find the best container to anchor the panel
      const container = this.findPlayerContainer();
      if (!container) return;

      // Create host element
      this.host = document.createElement("div");
      this.host.className = "getly-video-panel-host";
      this.host.setAttribute("data-getly-panel", "1");

      // Attach isolated Shadow DOM to guarantee zero CSS conflict
      this.shadow = this.host.attachShadow({ mode: "open" });
      this.render();
      this.bindEvents(container);

      // Append host into the container
      try {
        container.appendChild(this.host);
      } catch (_) {
        container.parentElement?.appendChild(this.host);
      }

      // Initial transient fade-in preview for 3.5s
      this.showPanel();
      this.scheduleHide(3500);
    }

    findPlayerContainer() {
      // 1. YouTube player container
      if (window.location.hostname.includes("youtube.com")) {
        const ytPlayer = document.querySelector("#movie_player, .html5-video-player, #player-container, ytd-reel-video-renderer");
        if (ytPlayer && (ytPlayer.contains(this.video) || ytPlayer === this.video.parentElement)) {
          return ytPlayer;
        }
      }

      // 2. Generic containers with player class
      let parent = this.video.parentElement;
      while (parent && parent !== document.body && parent !== document.documentElement) {
        const style = window.getComputedStyle(parent);
        const pos = style.position;
        const cls = (parent.className || "").toString().toLowerCase();

        if (
          cls.includes("player") ||
          cls.includes("video-js") ||
          cls.includes("plyr") ||
          cls.includes("jwplayer") ||
          pos === "relative" ||
          pos === "absolute"
        ) {
          return parent;
        }
        parent = parent.parentElement;
      }

      // Fallback: direct parent
      return this.video.parentElement || document.body;
    }

    render() {
      const styles = `
        :host {
          all: initial;
          position: absolute;
          top: 14px;
          right: 14px;
          z-index: 2147483647;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          user-select: none;
          -webkit-user-select: none;
          pointer-events: auto;
        }

        .panel-wrap {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 6px;
          opacity: 0;
          transform: translateY(-4px) scale(0.98);
          transition: opacity 0.22s cubic-bezier(0.16, 1, 0.3, 1), transform 0.22s cubic-bezier(0.16, 1, 0.3, 1);
          pointer-events: none;
        }

        .panel-wrap.visible {
          opacity: 1;
          transform: translateY(0) scale(1);
          pointer-events: auto;
        }

        /* Pill bar container with Fluent acrylic glass */
        .pill {
          display: inline-flex;
          align-items: center;
          height: 32px;
          padding: 2px 4px;
          background: rgba(15, 23, 42, 0.88);
          backdrop-filter: blur(14px);
          -webkit-backdrop-filter: blur(14px);
          border: 1px solid rgba(255, 255, 255, 0.16);
          border-radius: 8px;
          box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.55), 0 4px 6px -2px rgba(0, 0, 0, 0.3);
          transition: background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease;
        }

        .pill:hover {
          background: rgba(15, 23, 42, 0.96);
          border-color: rgba(56, 189, 248, 0.5);
          box-shadow: 0 12px 28px -4px rgba(0, 0, 0, 0.65), 0 0 12px rgba(56, 189, 248, 0.25);
        }

        /* Primary action button */
        .btn-main {
          display: inline-flex;
          align-items: center;
          gap: 7px;
          height: 26px;
          padding: 0 8px 0 6px;
          background: transparent;
          border: none;
          border-radius: 5px;
          color: #F8FAFC;
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          outline: none;
          transition: background 0.15s ease, color 0.15s ease;
        }

        .btn-main:hover {
          background: rgba(255, 255, 255, 0.12);
          color: #38BDF8;
        }

        .btn-main:active {
          transform: scale(0.97);
        }

        /* Brand logo/arrow icon */
        .brand-icon {
          width: 16px;
          height: 16px;
          fill: none;
          stroke: #38BDF8;
          stroke-width: 2.2;
          stroke-linecap: round;
          stroke-linejoin: round;
          filter: drop-shadow(0 0 4px rgba(56, 189, 248, 0.4));
        }

        /* Divider inside pill */
        .pill-divider {
          width: 1px;
          height: 16px;
          background: rgba(255, 255, 255, 0.15);
          margin: 0 2px;
        }

        /* Dropdown arrow button */
        .btn-dropdown, .btn-close {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          height: 24px;
          background: transparent;
          border: none;
          border-radius: 5px;
          color: #94A3B8;
          cursor: pointer;
          outline: none;
          transition: background 0.15s ease, color 0.15s ease;
        }

        .btn-dropdown:hover, .btn-close:hover {
          background: rgba(255, 255, 255, 0.12);
          color: #F8FAFC;
        }

        .btn-close:hover {
          color: #F87171;
        }

        .arrow-icon, .close-icon {
          width: 13px;
          height: 13px;
          fill: none;
          stroke: currentColor;
          stroke-width: 2;
          stroke-linecap: round;
          stroke-linejoin: round;
        }

        /* Quality Dropdown Menu */
        .menu {
          display: none;
          width: 220px;
          background: rgba(15, 23, 42, 0.96);
          backdrop-filter: blur(20px);
          -webkit-backdrop-filter: blur(20px);
          border: 1px solid rgba(255, 255, 255, 0.16);
          border-radius: 8px;
          padding: 6px;
          box-shadow: 0 16px 36px rgba(0, 0, 0, 0.65);
          animation: menuFadeIn 0.15s ease-out;
        }

        .menu.open {
          display: block;
        }

        @keyframes menuFadeIn {
          from { opacity: 0; transform: translateY(-6px); }
          to { opacity: 1; transform: translateY(0); }
        }

        .menu-header {
          padding: 6px 8px 4px 8px;
          font-size: 11px;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: #64748B;
        }

        .menu-item {
          display: flex;
          align-items: center;
          justify-content: space-between;
          width: 100%;
          height: 30px;
          padding: 0 8px;
          background: transparent;
          border: none;
          border-radius: 5px;
          color: #E2E8F0;
          font-size: 12px;
          font-weight: 500;
          cursor: pointer;
          outline: none;
          box-sizing: border-box;
          transition: background 0.12s ease, color 0.12s ease;
        }

        .menu-item:hover {
          background: rgba(56, 189, 248, 0.18);
          color: #FFFFFF;
        }

        .item-left {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .item-icon {
          font-size: 12px;
          color: #38BDF8;
        }

        .item-badge {
          font-size: 10px;
          font-weight: 600;
          padding: 1px 5px;
          border-radius: 4px;
          background: rgba(255, 255, 255, 0.1);
          color: #94A3B8;
        }

        .menu-item:hover .item-badge {
          background: rgba(56, 189, 248, 0.35);
          color: #E0F2FE;
        }

        .menu-divider {
          height: 1px;
          background: rgba(255, 255, 255, 0.1);
          margin: 4px 0;
        }

        /* Feedback Toast */
        .toast {
          display: none;
          align-items: center;
          gap: 6px;
          padding: 6px 12px;
          background: rgba(16, 185, 129, 0.92);
          backdrop-filter: blur(12px);
          border-radius: 6px;
          color: #FFFFFF;
          font-size: 12px;
          font-weight: 600;
          box-shadow: 0 8px 20px rgba(16, 185, 129, 0.35);
          animation: toastPop 0.2s cubic-bezier(0.16, 1, 0.3, 1);
        }

        .toast.visible {
          display: flex;
        }

        @keyframes toastPop {
          from { opacity: 0; transform: scale(0.9); }
          to { opacity: 1; transform: scale(1); }
        }
      `;

      const template = `
        <style>${styles}</style>
        <div class="panel-wrap" id="panelWrap">
          <!-- Main Pill -->
          <div class="pill" id="pillBar">
            <button class="btn-main" id="btnMain" title="Scarica con Getly">
              <svg class="brand-icon" viewBox="0 0 24 24">
                <path d="M12 3v12m0 0l-4-4m4 4l4-4M5 19h14" />
              </svg>
              <span id="btnText">Scarica video</span>
            </button>
            <div class="pill-divider"></div>
            <button class="btn-dropdown" id="btnDropdown" title="Scegli qualità o solo audio">
              <svg class="arrow-icon" viewBox="0 0 24 24">
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            <button class="btn-close" id="btnClose" title="Nascondi per questa sessione">
              <svg class="close-icon" viewBox="0 0 24 24">
                <path d="M18 6L6 18M6 6l12 12" />
              </svg>
            </button>
          </div>

          <!-- Toast feedback -->
          <div class="toast" id="feedbackToast">
            <span>✓</span>
            <span id="toastMsg">Inviato a Getly!</span>
          </div>

          <!-- Quality Dropdown Menu -->
          <div class="menu" id="qualityMenu">
            <div class="menu-header">Risoluzione & Qualità</div>
            <button class="menu-item" data-format="best">
              <span class="item-left"><span class="item-icon">★</span> Migliore (Auto)</span>
              <span class="item-badge">Max</span>
            </button>
            <button class="menu-item" data-format="1080p">
              <span class="item-left"><span class="item-icon">HD</span> 1080p Full HD</span>
              <span class="item-badge">MP4</span>
            </button>
            <button class="menu-item" data-format="720p">
              <span class="item-left"><span class="item-icon">HD</span> 720p HD</span>
              <span class="item-badge">MP4</span>
            </button>
            <button class="menu-item" data-format="480p">
              <span class="item-left"><span class="item-icon">SD</span> 480p SD</span>
              <span class="item-badge">MP4</span>
            </button>
            <div class="menu-divider"></div>
            <button class="menu-item" data-format="audio">
              <span class="item-left"><span class="item-icon">🎵</span> Solo Audio</span>
              <span class="item-badge">MP3 / M4A</span>
            </button>
            <!-- Dynamic sniffed streams list if available -->
            <div id="sniffedContainer" style="display: none;">
              <div class="menu-divider"></div>
              <div class="menu-header">Flussi di rete intercettati</div>
              <div id="sniffedList"></div>
            </div>
          </div>
        </div>
      `;

      this.shadow.innerHTML = template;
    }

    bindEvents(container) {
      const panelWrap = this.shadow.getElementById("panelWrap");
      const btnMain = this.shadow.getElementById("btnMain");
      const btnDropdown = this.shadow.getElementById("btnDropdown");
      const btnClose = this.shadow.getElementById("btnClose");
      const qualityMenu = this.shadow.getElementById("qualityMenu");
      const sniffedContainer = this.shadow.getElementById("sniffedContainer");
      const sniffedList = this.shadow.getElementById("sniffedList");

      // Stop propagation of all clicks inside the panel so the underlying player does not play/pause
      this.host.addEventListener("click", (e) => e.stopPropagation());
      this.host.addEventListener("mousedown", (e) => e.stopPropagation());
      this.host.addEventListener("mouseup", (e) => e.stopPropagation());
      this.host.addEventListener("dblclick", (e) => e.stopPropagation());

      // Track mouse hovering over panel
      this.host.addEventListener("mouseenter", () => {
        this.isMouseOverPanel = true;
        this.showPanel();
        this.clearHideTimer();
      });

      this.host.addEventListener("mouseleave", () => {
        this.isMouseOverPanel = false;
        if (!this.isMenuOpen) {
          this.scheduleHide(2000);
        }
      });

      // Hover on video container reveals panel
      const onContainerEnter = () => {
        if (!this.isDismissed && floatingButtonEnabled) {
          this.showPanel();
          this.clearHideTimer();
        }
      };

      const onContainerLeave = () => {
        if (!this.isMouseOverPanel && !this.isMenuOpen) {
          this.scheduleHide(1800);
        }
      };

      container.addEventListener("mouseenter", onContainerEnter);
      container.addEventListener("mouseleave", onContainerLeave);
      container.addEventListener("mousemove", () => {
        if (!this.isDismissed && floatingButtonEnabled) {
          this.showPanel();
          if (!this.isMouseOverPanel && !this.isMenuOpen) {
            this.scheduleHide(2500);
          }
        }
      });

      // Main button click: download best format
      btnMain.addEventListener("click", () => {
        this.triggerDownload("best");
      });

      // Dropdown toggle
      btnDropdown.addEventListener("click", () => {
        this.isMenuOpen = !this.isMenuOpen;
        if (this.isMenuOpen) {
          qualityMenu.classList.add("open");
          this.populateSniffedStreams(sniffedContainer, sniffedList);
        } else {
          qualityMenu.classList.remove("open");
        }
      });

      // Menu items click
      qualityMenu.querySelectorAll(".menu-item").forEach((btn) => {
        btn.addEventListener("click", () => {
          const format = btn.getAttribute("data-format") || "best";
          this.isMenuOpen = false;
          qualityMenu.classList.remove("open");
          this.triggerDownload(format);
        });
      });

      // Dismiss button
      btnClose.addEventListener("click", () => {
        this.isDismissed = true;
        this.hidePanel();
      });

      // Close menu if clicked outside inside document
      document.addEventListener("click", (e) => {
        if (this.isMenuOpen && !this.host.contains(e.target)) {
          this.isMenuOpen = false;
          qualityMenu.classList.remove("open");
          if (!this.isMouseOverPanel) {
            this.scheduleHide(1500);
          }
        }
      });
    }

    populateSniffedStreams(containerEl, listEl) {
      if (typeof chrome === "undefined" || !chrome.runtime || !chrome.runtime.sendMessage) return;

      chrome.runtime.sendMessage({ type: "GET_TAB_MEDIA" }, (response) => {
        if (chrome.runtime.lastError || !response || !Array.isArray(response.media) || response.media.length === 0) {
          containerEl.style.display = "none";
          return;
        }

        containerEl.style.display = "block";
        listEl.innerHTML = "";

        // List up to 4 most recent streams
        const streams = response.media.slice(-4);
        streams.forEach((item) => {
          const itemBtn = document.createElement("button");
          itemBtn.className = "menu-item";
          itemBtn.innerHTML = `
            <span class="item-left">
              <span class="item-icon">⚡</span>
              <span style="max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${item.filename}</span>
            </span>
            <span class="item-badge">${item.formatBadge || "MEDIA"}</span>
          `;
          itemBtn.addEventListener("click", () => {
            this.isMenuOpen = false;
            this.shadow.getElementById("qualityMenu").classList.remove("open");
            chrome.runtime.sendMessage({
              type: "DOWNLOAD_MEDIA_ITEM",
              item: item,
              referer: window.location.href,
            }, (res) => {
              if (res && res.success) {
                this.showToast("Inviato a Getly!");
              } else {
                this.showToast("Errore di invio", true);
              }
            });
          });
          listEl.appendChild(itemBtn);
        });
      });
    }

    triggerDownload(format) {
      const targetUrl = resolveVideoTargetUrl(this.video);
      const title = extractVideoTitle();

      const btnText = this.shadow.getElementById("btnText");
      if (btnText) btnText.textContent = "Invio...";

      chrome.runtime.sendMessage(
        {
          type: "DOWNLOAD_VIDEO_FROM_PAGE",
          url: targetUrl,
          title: title,
          format: format,
          referer: window.location.href,
          videoSrc: this.video.currentSrc || this.video.src || "",
        },
        (response) => {
          if (btnText) btnText.textContent = "Scarica video";

          if (response && response.success) {
            this.showToast(format === "audio" ? "🎵 Audio inviato!" : "✓ Inviato a Getly!");
          } else {
            const err = (response && response.error) ? response.error : "Getly offline";
            this.showToast(`Errore: ${err}`, true);
          }
        }
      );
    }

    showToast(message, isError = false) {
      const toast = this.shadow.getElementById("feedbackToast");
      const toastMsg = this.shadow.getElementById("toastMsg");
      if (!toast || !toastMsg) return;

      toastMsg.textContent = message;
      if (isError) {
        toast.style.background = "rgba(239, 68, 68, 0.92)";
        toast.style.boxShadow = "0 8px 20px rgba(239, 68, 68, 0.35)";
      } else {
        toast.style.background = "rgba(16, 185, 129, 0.92)";
        toast.style.boxShadow = "0 8px 20px rgba(16, 185, 129, 0.35)";
      }

      toast.classList.add("visible");
      setTimeout(() => {
        toast.classList.remove("visible");
      }, 3000);
    }

    showPanel() {
      if (this.isDismissed || !floatingButtonEnabled) return;
      const wrap = this.shadow.getElementById("panelWrap");
      if (wrap) wrap.classList.add("visible");
    }

    hidePanel() {
      if (this.isMenuOpen || this.isMouseOverPanel) return;
      const wrap = this.shadow.getElementById("panelWrap");
      if (wrap) wrap.classList.remove("visible");
    }

    scheduleHide(delayMs) {
      this.clearHideTimer();
      this.hideTimer = setTimeout(() => {
        this.hidePanel();
      }, delayMs);
    }

    clearHideTimer() {
      if (this.hideTimer) {
        clearTimeout(this.hideTimer);
        this.hideTimer = null;
      }
    }
  }

  function toggleAllPanelsVisibility(enabled) {
    document.querySelectorAll(".getly-video-panel-host").forEach((host) => {
      host.style.display = enabled ? "block" : "none";
    });
  }

  /**
   * Scans document for video elements and attaches the floating panel
   */
  function scanAndAttachVideos() {
    const videos = document.querySelectorAll("video");
    videos.forEach((video) => {
      // Ignore tiny icons, hidden videos, or tracking pixels
      const rect = video.getBoundingClientRect();
      const isVisible = rect.width >= 160 || rect.height >= 100 || video.offsetWidth >= 160;

      // Special allowance on YouTube main player
      const isYouTubePlayer = window.location.hostname.includes("youtube.com") &&
        (video.closest("#movie_player, .html5-video-player") !== null);

      if (!isVisible && !isYouTubePlayer) {
        return;
      }

      if (!activePanels.has(video)) {
        const panel = new VideoPanelInstance(video);
        activePanels.set(video, panel);
      }
    });
  }

  // Initial scan
  scanAndAttachVideos();

  // Periodic and MutationObserver monitoring for dynamically loaded videos (SPA, YouTube navigation)
  const observer = new MutationObserver(() => {
    scanAndAttachVideos();
  });

  observer.observe(document.body || document.documentElement, {
    childList: true,
    subtree: true,
  });

  // YouTube SPA navigation events
  window.addEventListener("yt-navigate-finish", () => {
    setTimeout(scanAndAttachVideos, 800);
  });
  window.addEventListener("spfdone", () => {
    setTimeout(scanAndAttachVideos, 800);
  });

  // Re-scan periodically at low frequency (every 2.5s) to catch delayed video player rendering
  setInterval(scanAndAttachVideos, 2500);

})();
