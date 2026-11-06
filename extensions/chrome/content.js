/**
 * Getly - Media Download Floating Panel (Content Script)
 * Automatically detects HTML5 video players (YouTube, Vimeo, Twitch, web videos)
 * AND active audio/music streams (YouTube Music, SoundCloud, Bandcamp, Spotify, podcast players, HTML5 audio),
 * displaying an acrylic floating download bar inspired by IDM with modern WinUI 3 aesthetics.
 *
 * Features:
 * - Smart detection: Identifies Video vs. Audio/Music playback and adapts UI, icons, and quality options.
 * - 100% Opaque solid background (#0f172a): zero ghost buttons visible from underneath.
 * - Draggable & Movable: Drag anywhere across the player container or viewport via the drag handle (⠿).
 * - Persistent position: remembers user's preferred spot across media items and page loads.
 * - Full bubble-phase event isolation: stops clicks/pointers from bleeding into underlying web players.
 * - Real-time playback event listener: triggers floating panel when audio/music playback begins.
 * - Root-level anchoring: Never gets clipped by container overflow:hidden (e.g. SoundCloud, Spotify).
 * - Real-time Network Sniffer Integration: Instantly reacts when background worker intercepts audio streams.
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

  // Registry of attached media elements to avoid duplicate panels
  // WeakMap<HTMLMediaElement, MediaPanelInstance>
  const activePanels = new WeakMap();
  let globalWebAudioPanel = null;

  // =========================================================================
  // 1. Main-World Hook (Catches in-memory `new Audio()` and detached elements)
  // =========================================================================
  function injectMainWorldHook() {
    try {
      const script = document.createElement("script");
      script.textContent = `(${function () {
        if (window.__GETLY_MAIN_HOOK_INSTALLED__) return;
        window.__GETLY_MAIN_HOOK_INSTALLED__ = true;

        const notify = (type, elem) => {
          try {
            const src = elem.currentSrc || elem.src || "";
            const isAudio =
              elem.tagName.toLowerCase() === "audio" ||
              (elem.videoWidth === 0 && elem.videoHeight === 0);
            window.dispatchEvent(
              new CustomEvent("__GETLY_MEDIA_EVENT__", {
                detail: {
                  type: type,
                  tagName: elem.tagName.toLowerCase(),
                  mediaType: isAudio ? "audio" : "video",
                  src: src,
                },
              })
            );
          } catch (_) {}
        };

        const origPlay = HTMLMediaElement.prototype.play;
        HTMLMediaElement.prototype.play = function () {
          notify("PLAY", this);
          return origPlay.apply(this, arguments);
        };

        const origPause = HTMLMediaElement.prototype.pause;
        HTMLMediaElement.prototype.pause = function () {
          notify("PAUSE", this);
          return origPause.apply(this, arguments);
        };
      }})();`;
      (document.head || document.documentElement).appendChild(script);
      script.remove();
    } catch (_) {}
  }

  injectMainWorldHook();

  // Listen to main-world audio/video playback events
  window.addEventListener("__GETLY_MEDIA_EVENT__", (e) => {
    const detail = e.detail;
    if (!detail) return;
    if (detail.type === "PLAY") {
      onPlaybackDetected(detail.mediaType, detail.src);
    }
  });

  /**
   * Identifies whether the media element represents Audio / Music or Video
   */
  function determineMediaType(media) {
    if (media && media.tagName && media.tagName.toLowerCase() === "audio") {
      return "audio";
    }

    const host = window.location.hostname.toLowerCase();
    if (
      host.includes("soundcloud.com") ||
      host.includes("music.youtube.com") ||
      host.includes("bandcamp.com") ||
      host.includes("mixcloud.com") ||
      host.includes("audiomack.com") ||
      host.includes("spotify.com") ||
      host.includes("deezer.com")
    ) {
      return "audio";
    }

    if (
      media &&
      media.tagName &&
      media.tagName.toLowerCase() === "video" &&
      media.videoWidth === 0 &&
      media.videoHeight === 0 &&
      !media.paused &&
      media.currentTime > 0
    ) {
      return "audio";
    }

    return "video";
  }

  /**
   * Helper to safely extract title from page or player
   */
  function extractMediaTitle(media, mediaType) {
    // 1. SoundCloud specific title & artist (Bottom bar and Track Page)
    if (window.location.hostname.includes("soundcloud.com")) {
      const cleanScText = (el) => {
        if (!el) return "";
        let t = el.getAttribute("title") || "";
        if (!t.trim()) {
          const clone = el.cloneNode(true);
          clone.querySelectorAll(".sc-visuallyhidden").forEach((n) => n.remove());
          t = clone.textContent.trim();
        }
        t = t.replace(/^Current track:\s*/i, "").replace(/^Current track_\s*/i, "").trim();
        const half = Math.floor(t.length / 2);
        if (half > 3 && t.slice(0, half) === t.slice(half)) {
          t = t.slice(0, half).trim();
        }
        return t;
      };

      const bottomTitle = document.querySelector(".playbackSoundBadge__titleLink, a.playbackSoundBadge__title");
      const bottomArtist = document.querySelector(".playbackSoundBadge__lightLink, a.playbackSoundBadge__username");
      const trackName = cleanScText(bottomTitle);
      if (trackName) {
        const artistName = cleanScText(bottomArtist);
        const artistPrefix = artistName ? `${artistName} - ` : "";
        return `${artistPrefix}${trackName}`;
      }

      const heroTitle = document.querySelector(".soundTitle__title span, h1.soundTitle__title");
      const heroArtist = document.querySelector(".soundTitle__username");
      const heroTrack = cleanScText(heroTitle);
      if (heroTrack) {
        const heroArt = cleanScText(heroArtist);
        const artistPrefix = heroArt ? `${heroArt} - ` : "";
        return `${artistPrefix}${heroTrack}`;
      }
    }

    // 2. YouTube Music specific title & artist
    if (window.location.hostname.includes("music.youtube.com")) {
      const ytMusicTitle = document.querySelector(
        "ytmusic-player-bar .title, ytmusic-player-bar .yt-formatted-string.title, .middle-controls .title"
      );
      if (ytMusicTitle && ytMusicTitle.textContent.trim()) {
        const artist = document.querySelector("ytmusic-player-bar .byline, .middle-controls .byline");
        const artistText = artist && artist.textContent.trim() ? ` - ${artist.textContent.trim()}` : "";
        return `${ytMusicTitle.textContent.trim()}${artistText}`;
      }
    }

    // 3. Standard YouTube video title
    const ytTitle = document.querySelector(
      "h1.ytd-watch-metadata yt-formatted-string, #title h1 yt-formatted-string, ytd-reel-player-header-renderer h2"
    );
    if (ytTitle && ytTitle.textContent.trim()) {
      return ytTitle.textContent.trim();
    }

    // 4. Bandcamp title & artist
    const bcTitle = document.querySelector(".trackTitle, .title_link, #name-section .trackTitle");
    if (bcTitle && bcTitle.textContent.trim()) {
      const bcArtist = document.querySelector("#name-section h3 span a");
      const artistText = bcArtist && bcArtist.textContent.trim() ? `${bcArtist.textContent.trim()} - ` : "";
      return `${artistText}${bcTitle.textContent.trim()}`;
    }

    // 5. Spotify Web title & artist
    const spTitle = document.querySelector("[data-testid='now-playing-widget'] [data-testid='context-item-info-title']");
    if (spTitle && spTitle.textContent.trim()) {
      const spArtist = document.querySelector(
        "[data-testid='now-playing-widget'] [data-testid='context-item-info-subartists']"
      );
      const artistText = spArtist && spArtist.textContent.trim() ? `${spArtist.textContent.trim()} - ` : "";
      return `${artistText}${spTitle.textContent.trim()}`;
    }

    // 6. Direct media filename if present and not blob
    const src = media ? media.currentSrc || media.src || "" : "";
    if (src && !src.startsWith("blob:") && !src.startsWith("data:")) {
      try {
        const parsed = new URL(src);
        const name = parsed.pathname.split("/").pop();
        if (name && name.includes(".")) {
          return decodeURIComponent(name).split("?")[0].replace(/\.[^/.]+$/, "");
        }
      } catch (_) {}
    }

    // 7. OpenGraph or Twitter meta tag
    const ogTitle = document.querySelector('meta[property="og:title"], meta[name="twitter:title"]');
    if (ogTitle && ogTitle.content && ogTitle.content.trim()) {
      return ogTitle.content.trim();
    }

    // 8. Document title cleaned from common branding suffixes
    let title = document.title || (mediaType === "audio" ? "audio" : "video");
    title = title
      .replace(/\s*-\s*YouTube Music$/, "")
      .replace(/\s*-\s*YouTube$/, "")
      .replace(/\s*\|\s*SoundCloud$/, "")
      .replace(/\s*\|\s*Bandcamp$/, "")
      .replace(/\s*-\s*Spotify$/, "")
      .replace(/\s*-\s*Vimeo$/, "")
      .trim();
    return title;
  }

  /**
   * Helper to resolve canonical media URL
   */
  function resolveMediaTargetUrl(media) {
    // On SoundCloud, resolve exact playing track from bottom player bar link if present
    if (window.location.hostname.includes("soundcloud.com")) {
      const scLink = document.querySelector(".playbackSoundBadge__titleLink, a.playbackSoundBadge__title");
      if (scLink && scLink.href && scLink.href.startsWith("http")) {
        return scLink.href.split("?")[0];
      }
    }

    // On YouTube Music, resolve exact playing video/track link
    if (window.location.hostname.includes("music.youtube.com")) {
      const ytMusicLink = document.querySelector("ytmusic-player-bar a.yt-simple-endpoint[href*='watch?v=']");
      if (ytMusicLink && ytMusicLink.href) {
        return ytMusicLink.href;
      }
    }

    const loc = window.location.href;

    // On YouTube & YouTube Music, watch & shorts URLs are the source of truth for yt-dlp
    if (
      loc.includes("youtube.com/watch") ||
      loc.includes("youtube.com/shorts") ||
      loc.includes("music.youtube.com/watch") ||
      loc.includes("youtu.be/")
    ) {
      return loc;
    }

    // Generic direct source if present and not blob/data
    const src = media ? media.currentSrc || media.src || "" : "";
    if (src && !src.startsWith("blob:") && !src.startsWith("data:")) {
      return src;
    }

    // Fallback to page URL (SoundCloud, Bandcamp, Spotify, Vimeo, Twitch, Twitter, TikTok)
    return loc;
  }

  /**
   * MediaPanelInstance represents the floating bar attached to a specific video or audio element
   */
  class MediaPanelInstance {
    constructor(media, mediaType, options = {}) {
      this.media = media;
      this.mediaType = mediaType || determineMediaType(media);
      this.options = options;
      this.host = null;
      this.shadow = null;
      this.hideTimer = null;
      this.isMenuOpen = false;
      this.isMouseOverPanel = false;
      this.isDismissed = false;
      this.isFixed = false;
      this.container = null;

      this.init();
    }

    init() {
      // Find the best container or player bar
      this.container = this.options.container || this.findPlayerContainer();
      if (!this.container) this.container = document.body;

      // Audio panels are ALWAYS fixed to document.body so they are never clipped by overflow:hidden
      const isAudio = this.mediaType === "audio";
      this.isFixed = isAudio || this.container === document.body || this.container === document.documentElement;

      if (!this.isFixed) {
        const containerStyle = window.getComputedStyle(this.container);
        if (containerStyle.position === "static") {
          this.container.style.position = "relative";
        }
      }

      // Create host element with highest isolation
      this.host = document.createElement("div");
      this.host.className = `getly-media-panel-host getly-panel-${this.mediaType}`;
      this.host.setAttribute("data-getly-panel", "1");
      this.host.setAttribute("data-media-type", this.mediaType);

      // Critical root styles applied directly on the host in page DOM
      this.host.style.setProperty("all", "initial", "important");
      this.host.style.setProperty("display", "block", "important");
      this.host.style.setProperty("position", this.isFixed ? "fixed" : "absolute", "important");
      this.host.style.setProperty("z-index", "2147483647", "important");
      this.host.style.setProperty("pointer-events", "auto", "important");
      this.host.style.setProperty("isolation", "isolate", "important");

      // Set initial position: try to restore user saved position or default
      this.restorePosition(this.container);

      // Attach isolated Shadow DOM to guarantee zero CSS conflict
      this.shadow = this.host.attachShadow({ mode: "open" });
      this.render();
      this.bindEvents(this.container);

      // If fixed, ALWAYS append directly to document.body to avoid clipping
      if (this.isFixed) {
        document.body.appendChild(this.host);
      } else {
        try {
          this.container.appendChild(this.host);
        } catch (_) {
          document.body.appendChild(this.host);
        }
      }

      // Initial transient fade-in preview (6s for audio, 3.5s for video)
      this.showPanel();
      this.scheduleHide(this.mediaType === "audio" ? 6000 : 3500);
    }

    restorePosition(container) {
      let saved = null;
      try {
        const raw = localStorage.getItem(`getly_floating_panel_pos_${this.mediaType}`);
        if (raw) saved = JSON.parse(raw);
      } catch (_) {}

      const containerRect = container ? container.getBoundingClientRect() : { width: 0, height: 0, top: 0, bottom: 0 };
      const isHorizontalBottomBar =
        this.mediaType === "audio" &&
        containerRect.height > 0 &&
        containerRect.bottom >= window.innerHeight - 80;

      if (
        saved &&
        typeof saved.xRatio === "number" &&
        typeof saved.yRatio === "number" &&
        window.innerWidth > 200
      ) {
        const baseWidth = this.isFixed ? window.innerWidth : Math.max(containerRect.width, 200);
        const baseHeight = this.isFixed ? window.innerHeight : Math.max(containerRect.height, 100);
        const left = Math.max(8, Math.min(baseWidth - 160, saved.xRatio * baseWidth));
        const top = Math.max(8, Math.min(baseHeight - 40, saved.yRatio * baseHeight));
        this.host.style.setProperty("right", "auto", "important");
        this.host.style.setProperty("bottom", "auto", "important");
        this.host.style.setProperty("left", `${left}px`, "important");
        this.host.style.setProperty("top", `${top}px`, "important");
      } else if (isHorizontalBottomBar && this.isFixed) {
        // Floating fixed right above the bottom player bar (e.g. SoundCloud, YouTube Music, Spotify)
        const barHeight = Math.max(containerRect.height, 48);
        const bottomOffset = Math.round(barHeight + 12);
        this.host.style.setProperty("bottom", `${bottomOffset}px`, "important");
        this.host.style.setProperty("right", "24px", "important");
        this.host.style.setProperty("top", "auto", "important");
        this.host.style.setProperty("left", "auto", "important");
      } else if (this.isFixed) {
        // Floating fixed widget in bottom-right corner for web audio
        this.host.style.setProperty("bottom", "64px", "important");
        this.host.style.setProperty("right", "24px", "important");
        this.host.style.setProperty("top", "auto", "important");
        this.host.style.setProperty("left", "auto", "important");
      } else {
        // Default top-right inside video player
        this.host.style.setProperty("top", "14px", "important");
        this.host.style.setProperty("right", "16px", "important");
        this.host.style.setProperty("left", "auto", "important");
        this.host.style.setProperty("bottom", "auto", "important");
      }
    }

    findPlayerContainer() {
      // 1. YouTube video player
      if (
        this.mediaType === "video" &&
        window.location.hostname.includes("youtube.com") &&
        !window.location.hostname.includes("music.youtube.com")
      ) {
        const ytPlayer = document.querySelector(
          "#movie_player, .html5-video-player, #player-container, ytd-reel-video-renderer"
        );
        if (ytPlayer && (!this.media || ytPlayer.contains(this.media) || ytPlayer === this.media.parentElement)) {
          return ytPlayer;
        }
      }

      // 2. SoundCloud bottom player bar
      if (window.location.hostname.includes("soundcloud.com")) {
        const scBar = document.querySelector(".playControls, .playControls__elements");
        if (scBar) return scBar;
        const heroPlayer = document.querySelector(".soundTitle__title, .waveformCommentsNode, .listenArtworkWrapper");
        if (heroPlayer) return heroPlayer;
      }

      // 3. YouTube Music bar container
      if (window.location.hostname.includes("music.youtube.com")) {
        const ytMusicBar = document.querySelector("ytmusic-player-bar, #player-bar-background");
        if (ytMusicBar) return ytMusicBar;
      }

      // 4. Spotify Web
      if (window.location.hostname.includes("spotify.com")) {
        const spBar = document.querySelector("footer[data-testid='now-playing-bar'], [data-testid='now-playing-widget']");
        if (spBar) return spBar;
      }

      // 5. Bandcamp player
      if (window.location.hostname.includes("bandcamp.com")) {
        const bcPlayer = document.querySelector(".inline_player, #trackInfoInner, .audioplayer");
        if (bcPlayer) return bcPlayer;
      }

      // 6. Look upwards from media element
      if (this.media) {
        let parent = this.media.parentElement;
        while (parent && parent !== document.body && parent !== document.documentElement) {
          const style = window.getComputedStyle(parent);
          const pos = style.position;
          const cls = (parent.className || "").toString().toLowerCase();
          const id = (parent.id || "").toLowerCase();

          if (
            cls.includes("player") ||
            cls.includes("video-js") ||
            cls.includes("plyr") ||
            cls.includes("jwplayer") ||
            cls.includes("audioplayer") ||
            cls.includes("playback") ||
            cls.includes("controls") ||
            id.includes("player") ||
            pos === "relative" ||
            pos === "absolute" ||
            pos === "fixed"
          ) {
            return parent;
          }
          parent = parent.parentElement;
        }
      }

      // Fallback: document.body
      return document.body;
    }

    render() {
      const isAudio = this.mediaType === "audio";
      const accentColor = isAudio ? "#FBBF24" : "#38BDF8";
      const accentHoverBg = isAudio ? "rgba(251, 191, 36, 0.20)" : "rgba(56, 189, 248, 0.22)";
      const brandGlow = isAudio ? "rgba(251, 191, 36, 0.5)" : "rgba(56, 189, 248, 0.5)";

      const styles = `
        * {
          box-sizing: border-box;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI Variable Display", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
        }

        :host {
          all: initial;
          display: block !important;
          z-index: 2147483647 !important;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI Variable Display", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
          user-select: none;
          -webkit-user-select: none;
          pointer-events: auto !important;
          isolation: isolate !important;
        }

        .panel-wrap, .pill, .menu, .menu-item, .btn-main, .btn-dropdown, .btn-close, button, input, select, textarea, span, div {
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI Variable Text", "Segoe UI Variable Display", "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif !important;
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

        .panel-wrap.dragging {
          transition: none !important;
          cursor: grabbing !important;
        }

        /* Pill bar container - 100% OPAQUE to prevent any underlying player buttons from showing through */
        .pill {
          display: inline-flex;
          align-items: center;
          height: 34px;
          padding: 2px 4px 2px 6px;
          background: #0f172a !important; /* Solid dark slate */
          border: 1px solid rgba(255, 255, 255, 0.22);
          border-radius: 8px;
          box-shadow: 0 14px 32px rgba(0, 0, 0, 0.85), 0 2px 8px rgba(0, 0, 0, 0.6);
          transition: border-color 0.18s ease, box-shadow 0.18s ease;
          pointer-events: auto !important;
          user-select: none;
        }

        .pill:hover {
          border-color: ${accentColor};
          box-shadow: 0 16px 36px rgba(0, 0, 0, 0.95), 0 0 14px ${brandGlow};
        }

        .panel-wrap.dragging .pill {
          border-color: ${accentColor} !important;
          box-shadow: 0 18px 40px rgba(0, 0, 0, 0.95), 0 0 18px ${brandGlow} !important;
          cursor: grabbing !important;
        }

        /* Movable Drag Handle */
        .pill-drag-handle {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 16px;
          height: 26px;
          cursor: grab;
          color: #94A3B8;
          margin-right: 3px;
          transition: color 0.15s ease;
          touch-action: none;
        }

        .pill-drag-handle:hover {
          color: ${accentColor};
        }

        .pill-drag-handle:active, .panel-wrap.dragging .pill-drag-handle {
          cursor: grabbing !important;
          color: ${accentColor};
        }

        .drag-icon {
          width: 12px;
          height: 18px;
          fill: currentColor;
          pointer-events: none;
        }

        /* Primary action button */
        .btn-main {
          display: inline-flex;
          align-items: center;
          gap: 7px;
          height: 26px;
          padding: 0 8px 0 4px;
          background: transparent;
          border: none;
          border-radius: 5px;
          color: #F8FAFC;
          font-size: 12px;
          font-weight: 600;
          cursor: pointer;
          outline: none;
          transition: background 0.15s ease, color 0.15s ease;
          pointer-events: auto !important;
        }

        .btn-main:hover {
          background: rgba(255, 255, 255, 0.12);
          color: ${accentColor};
        }

        .btn-main:active {
          transform: scale(0.97);
        }

        /* Brand icon */
        .brand-icon {
          width: 16px;
          height: 16px;
          fill: none;
          stroke: ${accentColor};
          stroke-width: 2.2;
          stroke-linecap: round;
          stroke-linejoin: round;
          filter: drop-shadow(0 0 4px ${brandGlow});
          pointer-events: none;
        }

        /* Divider inside pill */
        .pill-divider {
          width: 1px;
          height: 16px;
          background: rgba(255, 255, 255, 0.18);
          margin: 0 3px;
          pointer-events: none;
        }

        /* Dropdown arrow button & Close button */
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
          pointer-events: auto !important;
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
          pointer-events: none;
        }

        /* Dropdown Menu - 100% OPAQUE */
        .menu {
          display: none;
          width: 230px;
          background: #0f172a !important; /* Solid opaque */
          border: 1px solid rgba(255, 255, 255, 0.22);
          border-radius: 8px;
          padding: 6px;
          box-shadow: 0 18px 40px rgba(0, 0, 0, 0.9);
          animation: menuFadeIn 0.15s ease-out;
          pointer-events: auto !important;
          box-sizing: border-box;
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
          pointer-events: auto !important;
        }

        .menu-item:hover {
          background: ${accentHoverBg};
          color: #FFFFFF;
        }

        .item-left {
          display: flex;
          align-items: center;
          gap: 6px;
        }

        .item-icon {
          font-size: 12px;
          color: ${accentColor};
        }

        .item-badge {
          font-size: 10px;
          font-weight: 600;
          padding: 1px 5px;
          border-radius: 4px;
          background: rgba(255, 255, 255, 0.12);
          color: #94A3B8;
        }

        .menu-item:hover .item-badge {
          background: ${accentHoverBg};
          color: #FFFFFF;
        }

        .menu-divider {
          height: 1px;
          background: rgba(255, 255, 255, 0.12);
          margin: 4px 0;
        }

        /* Feedback Toast */
        .toast {
          display: none;
          align-items: center;
          gap: 6px;
          padding: 6px 12px;
          background: #059669 !important; /* Emerald solid */
          border-radius: 6px;
          color: #FFFFFF;
          font-size: 12px;
          font-weight: 600;
          box-shadow: 0 8px 20px rgba(5, 150, 105, 0.5);
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

      // Musical double-note for audio, download arrow for video
      const brandIconSvg = isAudio
        ? `<svg class="brand-icon" viewBox="0 0 24 24">
             <path d="M9 18V5l12-2v13" />
             <circle cx="6" cy="18" r="3" fill="currentColor" />
             <circle cx="18" cy="16" r="3" fill="currentColor" />
           </svg>`
        : `<svg class="brand-icon" viewBox="0 0 24 24">
             <path d="M12 3v12m0 0l-4-4m4 4l4-4M5 19h14" />
           </svg>`;

      const btnTitle = isAudio ? "Scarica audio con Getly" : "Scarica con Getly";
      const btnLabel = isAudio ? "Scarica audio" : "Scarica video";

      const menuItems = isAudio
        ? `
          <div class="menu-header">Qualità & Formato Audio</div>
          <button class="menu-item" data-format="audio">
            <span class="item-left"><span class="item-icon">★</span> Migliore (Auto)</span>
            <span class="item-badge">Max</span>
          </button>
          <button class="menu-item" data-format="audio_mp3">
            <span class="item-left"><span class="item-icon">🎵</span> MP3 Alta Qualità</span>
            <span class="item-badge">320k</span>
          </button>
          <button class="menu-item" data-format="audio_m4a">
            <span class="item-left"><span class="item-icon">🎵</span> M4A / AAC Originale</span>
            <span class="item-badge">M4A</span>
          </button>
          <button class="menu-item" data-format="audio_flac">
            <span class="item-left"><span class="item-icon">🎵</span> FLAC Senza Perdita</span>
            <span class="item-badge">Lossless</span>
          </button>
        `
        : `
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
        `;

      const template = `
        <style>${styles}</style>
        <div class="panel-wrap" id="panelWrap">
          <!-- Main Pill -->
          <div class="pill ${isAudio ? "audio-pill" : ""}" id="pillBar">
            <!-- Movable Drag Handle -->
            <div class="pill-drag-handle" id="dragHandle" title="Trascina per spostare la barra">
              <svg class="drag-icon" viewBox="0 0 24 24">
                <circle cx="8" cy="6" r="1.6" />
                <circle cx="16" cy="6" r="1.6" />
                <circle cx="8" cy="12" r="1.6" />
                <circle cx="16" cy="12" r="1.6" />
                <circle cx="8" cy="18" r="1.6" />
                <circle cx="16" cy="18" r="1.6" />
              </svg>
            </div>

            <button class="btn-main" id="btnMain" title="${btnTitle}">
              ${brandIconSvg}
              <span id="btnText">${btnLabel}</span>
            </button>

            <div class="pill-divider"></div>

            <button class="btn-dropdown" id="btnDropdown" title="Scegli opzioni di formato e qualità">
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
            ${menuItems}
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
      const pillBar = this.shadow.getElementById("pillBar");
      const dragHandle = this.shadow.getElementById("dragHandle");
      const btnMain = this.shadow.getElementById("btnMain");
      const btnDropdown = this.shadow.getElementById("btnDropdown");
      const btnClose = this.shadow.getElementById("btnClose");
      const qualityMenu = this.shadow.getElementById("qualityMenu");
      const sniffedContainer = this.shadow.getElementById("sniffedContainer");
      const sniffedList = this.shadow.getElementById("sniffedList");

      // Prevent mouse/pointer/touch event from escaping to the underlying page
      const BUBBLE_STOP_EVENTS = [
        "pointerdown",
        "pointerup",
        "pointercancel",
        "mousedown",
        "mouseup",
        "click",
        "dblclick",
        "contextmenu",
        "touchstart",
        "touchend",
        "touchmove",
      ];

      BUBBLE_STOP_EVENTS.forEach((evt) => {
        this.host.addEventListener(evt, (e) => {
          e.stopPropagation();
        });
      });

      // Hover on host maintains visibility
      this.host.addEventListener("mouseenter", () => {
        this.isMouseOverPanel = true;
        this.showPanel();
        this.clearHideTimer();
      });

      this.host.addEventListener("mouseleave", () => {
        this.isMouseOverPanel = false;
        if (!this.isMenuOpen && !isDragging) {
          this.scheduleHide(2000);
        }
      });

      // Hover on video/audio container reveals panel
      if (container && container !== document.body) {
        const onContainerEnter = () => {
          if (!this.isDismissed && floatingButtonEnabled) {
            this.showPanel();
            this.clearHideTimer();
          }
        };

        const onContainerLeave = () => {
          if (!this.isMouseOverPanel && !this.isMenuOpen && !isDragging) {
            this.scheduleHide(1800);
          }
        };

        container.addEventListener("mouseenter", onContainerEnter);
        container.addEventListener("mouseleave", onContainerLeave);
        container.addEventListener("mousemove", () => {
          if (!this.isDismissed && floatingButtonEnabled) {
            this.showPanel();
            if (!this.isMouseOverPanel && !this.isMenuOpen && !isDragging) {
              this.scheduleHide(2500);
            }
          }
        });
      }

      // Drag & Drop System
      let isDragging = false;
      let hasDragged = false;
      let startPointerX = 0;
      let startPointerY = 0;
      let startHostLeft = 0;
      let startHostTop = 0;
      let lastDragEndTime = 0;

      const startDrag = (e) => {
        if (e.button !== 0) return; // Left mouse click only

        isDragging = true;
        hasDragged = false;
        startPointerX = e.clientX;
        startPointerY = e.clientY;

        const hostRect = this.host.getBoundingClientRect();
        const baseRect = this.isFixed
          ? { left: 0, top: 0, width: window.innerWidth, height: window.innerHeight }
          : container.getBoundingClientRect();

        startHostLeft = hostRect.left - baseRect.left;
        startHostTop = hostRect.top - baseRect.top;

        this.host.style.setProperty("right", "auto", "important");
        this.host.style.setProperty("bottom", "auto", "important");
        this.host.style.setProperty("left", `${startHostLeft}px`, "important");
        this.host.style.setProperty("top", `${startHostTop}px`, "important");

        panelWrap.classList.add("dragging");
        this.clearHideTimer();

        window.addEventListener("pointermove", onPointerMove, { capture: true, passive: false });
        window.addEventListener("pointerup", onPointerUp, { capture: true, passive: false });
        window.addEventListener("pointercancel", onPointerUp, { capture: true, passive: false });
      };

      const onPointerMove = (e) => {
        if (!isDragging) return;

        const dx = e.clientX - startPointerX;
        const dy = e.clientY - startPointerY;

        if (!hasDragged && (Math.abs(dx) > 3 || Math.abs(dy) > 3)) {
          hasDragged = true;
        }

        if (hasDragged) {
          e.preventDefault();
          e.stopPropagation();

          const baseRect = this.isFixed
            ? { width: window.innerWidth, height: window.innerHeight }
            : container.getBoundingClientRect();
          const hostRect = this.host.getBoundingClientRect();

          let newLeft = startHostLeft + dx;
          let newTop = startHostTop + dy;

          const maxLeft = Math.max(8, baseRect.width - hostRect.width - 8);
          const maxTop = Math.max(8, baseRect.height - hostRect.height - 8);

          newLeft = Math.max(8, Math.min(maxLeft, newLeft));
          newTop = Math.max(8, Math.min(maxTop, newTop));

          this.host.style.setProperty("left", `${newLeft}px`, "important");
          this.host.style.setProperty("top", `${newTop}px`, "important");

          if (newLeft < 200) {
            panelWrap.style.alignItems = "flex-start";
          } else {
            panelWrap.style.alignItems = "flex-end";
          }
        }
      };

      const onPointerUp = (e) => {
        if (!isDragging) return;
        isDragging = false;
        panelWrap.classList.remove("dragging");

        window.removeEventListener("pointermove", onPointerMove, { capture: true });
        window.removeEventListener("pointerup", onPointerUp, { capture: true });
        window.removeEventListener("pointercancel", onPointerUp, { capture: true });

        if (hasDragged) {
          lastDragEndTime = Date.now();
          e.preventDefault();
          e.stopPropagation();

          const baseWidth = this.isFixed ? window.innerWidth : container.getBoundingClientRect().width;
          const baseHeight = this.isFixed ? window.innerHeight : container.getBoundingClientRect().height;
          const currentLeft = parseFloat(this.host.style.left) || 8;
          const currentTop = parseFloat(this.host.style.top) || 8;
          const xRatio = currentLeft / Math.max(baseWidth, 1);
          const yRatio = currentTop / Math.max(baseHeight, 1);

          try {
            localStorage.setItem(
              `getly_floating_panel_pos_${this.mediaType}`,
              JSON.stringify({ xRatio, yRatio })
            );
          } catch (_) {}
        }
      };

      dragHandle.addEventListener("pointerdown", (e) => {
        e.stopPropagation();
        e.preventDefault();
        startDrag(e);
      });

      pillBar.addEventListener("pointerdown", (e) => {
        const path = e.composedPath ? e.composedPath() : [e.target];
        const isButton = path.some((el) => {
          if (!el || !el.id) return false;
          return el.id === "btnMain" || el.id === "btnDropdown" || el.id === "btnClose";
        });
        if (isButton) return;
        e.stopPropagation();
        startDrag(e);
      });

      btnMain.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (Date.now() - lastDragEndTime < 250) return;
        this.triggerDownload(this.mediaType === "audio" ? "audio" : "best");
      });

      btnDropdown.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        if (Date.now() - lastDragEndTime < 250) return;

        this.isMenuOpen = !this.isMenuOpen;
        if (this.isMenuOpen) {
          qualityMenu.classList.add("open");
          this.populateSniffedStreams(sniffedContainer, sniffedList);
        } else {
          qualityMenu.classList.remove("open");
        }
      });

      qualityMenu.querySelectorAll(".menu-item").forEach((btn) => {
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          e.preventDefault();
          const format = btn.getAttribute("data-format") || (this.mediaType === "audio" ? "audio" : "best");
          this.isMenuOpen = false;
          qualityMenu.classList.remove("open");
          this.triggerDownload(format);
        });
      });

      btnClose.addEventListener("click", (e) => {
        e.stopPropagation();
        e.preventDefault();
        this.isDismissed = true;
        this.hidePanel();
      });

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

        const streams = response.media.slice(-4);
        streams.forEach((item) => {
          const itemBtn = document.createElement("button");
          itemBtn.className = "menu-item";
          const isAudioBadge =
            (item.formatBadge || "").toLowerCase() === "mp3" || (item.formatBadge || "").toLowerCase() === "audio";
          itemBtn.innerHTML = `
            <span class="item-left">
              <span class="item-icon">${isAudioBadge ? "🎵" : "⚡"}</span>
              <span style="max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${item.filename}</span>
            </span>
            <span class="item-badge">${item.formatBadge || "MEDIA"}</span>
          `;
          itemBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            e.preventDefault();
            this.isMenuOpen = false;
            this.shadow.getElementById("qualityMenu").classList.remove("open");
            chrome.runtime.sendMessage(
              {
                type: "DOWNLOAD_MEDIA_ITEM",
                item: item,
                referer: window.location.href,
              },
              (res) => {
                if (res && res.success) {
                  this.showToast("Inviato a Getly!");
                } else {
                  this.showToast("Errore di invio", true);
                }
              }
            );
          });
          listEl.appendChild(itemBtn);
        });
      });
    }

    triggerDownload(format) {
      const targetUrl = resolveMediaTargetUrl(this.media);
      const title = extractMediaTitle(this.media, this.mediaType);

      const btnText = this.shadow.getElementById("btnText");
      const defaultText = this.mediaType === "audio" ? "Scarica audio" : "Scarica video";
      if (btnText) btnText.textContent = "Invio...";

      chrome.runtime.sendMessage(
        {
          type: "DOWNLOAD_MEDIA_FROM_PAGE",
          mediaType: this.mediaType,
          url: targetUrl,
          title: title,
          format: format,
          referer: window.location.href,
          mediaSrc: this.media ? this.media.currentSrc || this.media.src || "" : "",
        },
        (response) => {
          if (btnText) btnText.textContent = defaultText;

          if (response && response.success) {
            const isAudioDownload = this.mediaType === "audio" || (format && format.startsWith("audio"));
            this.showToast(isAudioDownload ? "🎵 Audio inviato a Getly!" : "✓ Inviato a Getly!");
          } else {
            const err = response && response.error ? response.error : "Getly offline";
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
        toast.style.background = "#DC2626";
        toast.style.boxShadow = "0 8px 20px rgba(220, 38, 38, 0.4)";
      } else {
        toast.style.background = "#059669";
        toast.style.boxShadow = "0 8px 20px rgba(5, 150, 105, 0.4)";
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
    document.querySelectorAll(".getly-media-panel-host").forEach((host) => {
      host.style.display = enabled ? "block" : "none";
    });
  }

  // =========================================================================
  // 2. Web Player Monitor (SoundCloud, YouTube Music, Spotify, Bandcamp)
  // =========================================================================
  function ensureWebAudioPanel(container, siteName) {
    if (!globalWebAudioPanel || !document.body.contains(globalWebAudioPanel.host)) {
      globalWebAudioPanel = new MediaPanelInstance(null, "audio", { container });
    } else {
      globalWebAudioPanel.showPanel();
      globalWebAudioPanel.scheduleHide(6000);
    }
  }

  function checkActiveMusicPlayers() {
    // 1. SoundCloud: check bottom play bar or sound playing state
    if (window.location.hostname.includes("soundcloud.com")) {
      const isPlaying = Boolean(
        document.querySelector(
          ".playControl.playing, button.playControl[title*='Pause'], button.playControls__play[aria-label*='Pause'], .sc-button-pause, .playButton.playing"
        )
      );
      const hasTrackInPlayer = Boolean(
        document.querySelector(".playbackSoundBadge__titleLink, .soundTitle__title")
      );
      if (isPlaying || hasTrackInPlayer) {
        const scBar = document.querySelector(".playControls, .playControls__elements") || document.body;
        ensureWebAudioPanel(scBar, "soundcloud");
      }
      return;
    }

    // 2. YouTube Music
    if (window.location.hostname.includes("music.youtube.com")) {
      const bar = document.querySelector("ytmusic-player-bar");
      const isPlaying = Boolean(
        bar &&
          (bar.hasAttribute("is-playing") ||
            document.querySelector("ytmusic-player-bar #play-pause-button[title*='Paus']"))
      );
      if (isPlaying && bar) {
        ensureWebAudioPanel(bar, "ytmusic");
      }
      return;
    }

    // 3. Spotify Web
    if (window.location.hostname.includes("spotify.com")) {
      const isPlaying = Boolean(
        document.querySelector("button[data-testid='control-button-playpause'][aria-label*='Paus']")
      );
      if (isPlaying) {
        const spBar =
          document.querySelector("footer[data-testid='now-playing-bar'], [data-testid='now-playing-widget']") ||
          document.body;
        ensureWebAudioPanel(spBar, "spotify");
      }
      return;
    }

    // 4. Bandcamp
    if (window.location.hostname.includes("bandcamp.com")) {
      const isPlaying = Boolean(document.querySelector(".playbutton.playing, .playing .playbutton"));
      if (isPlaying) {
        const bcPlayer = document.querySelector(".inline_player, #trackInfoInner") || document.body;
        ensureWebAudioPanel(bcPlayer, "bandcamp");
      }
      return;
    }
  }

  // =========================================================================
  // 3. Network Sniffer Real-time Listener (from background.js)
  // =========================================================================
  if (typeof chrome !== "undefined" && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request) => {
      if (request.type === "MEDIA_SNIFFED" && request.mediaItem) {
        const item = request.mediaItem;
        const badge = (item.formatBadge || "").toLowerCase();
        const mime = (item.mimeType || "").toLowerCase();
        const url = (item.url || "").toLowerCase();

        const isAudio =
          badge === "mp3" ||
          badge === "audio" ||
          mime.startsWith("audio/") ||
          url.includes(".mp3") ||
          url.includes(".m4a") ||
          url.includes(".aac") ||
          url.includes(".flac") ||
          url.includes(".wav") ||
          url.includes(".ogg") ||
          url.includes(".opus") ||
          window.location.hostname.includes("soundcloud.com") ||
          window.location.hostname.includes("music.youtube.com") ||
          window.location.hostname.includes("spotify.com") ||
          window.location.hostname.includes("bandcamp.com");

        if (isAudio) {
          const container =
            document.querySelector(".playControls, ytmusic-player-bar, .playback-bar, [data-testid='now-playing-widget']") ||
            document.body;
          ensureWebAudioPanel(container, "sniffer");
        }
      }
    });
  }

  function onPlaybackDetected(mediaType, src) {
    if (mediaType === "audio") {
      const container =
        document.querySelector(".playControls, ytmusic-player-bar, .playback-bar, [data-testid='now-playing-widget']") ||
        document.body;
      ensureWebAudioPanel(container, "playback-event");
    }
  }

  /**
   * Evaluates a DOM media element (video or audio) and attaches a floating panel if appropriate
   */
  function handleMediaElement(media) {
    if (!media || !(media instanceof HTMLMediaElement)) return;
    if (activePanels.has(media)) return;

    const isAudio = media.tagName.toLowerCase() === "audio";
    const isVideo = media.tagName.toLowerCase() === "video";
    const type = determineMediaType(media);

    if (isVideo) {
      const rect = media.getBoundingClientRect();
      const isVisible = rect.width >= 160 || rect.height >= 100 || media.offsetWidth >= 160;
      const isYouTubePlayer =
        window.location.hostname.includes("youtube.com") &&
        media.closest("#movie_player, .html5-video-player") !== null;
      const isMusicSite = type === "audio";

      if (!isVisible && !isYouTubePlayer && !isMusicSite) {
        return;
      }
    } else if (isAudio) {
      const hasSrc = Boolean(media.src || media.currentSrc || media.querySelector("source"));
      const isPlaying = !media.paused && media.currentTime > 0;
      const hasControls = media.hasAttribute("controls");

      if (!hasSrc && !isPlaying && !hasControls) {
        return;
      }
    }

    const panel = new MediaPanelInstance(media, type);
    activePanels.set(media, panel);
  }

  /**
   * Scans document for both video and audio elements
   */
  function scanAndAttachMedia() {
    checkActiveMusicPlayers();

    const mediaElements = document.querySelectorAll("video, audio");
    mediaElements.forEach((el) => {
      handleMediaElement(el);
    });
  }

  // Initial scan
  scanAndAttachMedia();

  // Real-time playback event listener (capture phase catches ANY audio/video starting playback)
  const onMediaPlaybackStarted = (e) => {
    const target = e.target;
    if (!target || !(target instanceof HTMLMediaElement)) return;

    let panel = activePanels.get(target);
    if (!panel) {
      handleMediaElement(target);
      panel = activePanels.get(target);
    }
    if (panel) {
      panel.showPanel();
      panel.scheduleHide(panel.mediaType === "audio" ? 6000 : 3500);
    }
  };

  document.addEventListener("play", onMediaPlaybackStarted, true);
  document.addEventListener("playing", onMediaPlaybackStarted, true);

  // Periodic and MutationObserver monitoring for dynamically loaded players (SPA, SoundCloud, YouTube)
  const observer = new MutationObserver(() => {
    scanAndAttachMedia();
  });

  observer.observe(document.body || document.documentElement, {
    childList: true,
    subtree: true,
  });

  // SPA navigation events
  window.addEventListener("yt-navigate-finish", () => {
    setTimeout(scanAndAttachMedia, 600);
  });
  window.addEventListener("spfdone", () => {
    setTimeout(scanAndAttachMedia, 600);
  });

  // Re-scan periodically (every 1.5s) to catch delayed audio/video rendering or track change
  setInterval(scanAndAttachMedia, 1500);
})();
