import base64
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional
import urllib.error
import urllib.request
import http.cookiejar

try:
    import quickjs
    QUICKJS_AVAILABLE = True
except ImportError:
    quickjs = None
    QUICKJS_AVAILABLE = False

try:
    import bs4
    BS4_AVAILABLE = True
except ImportError:
    bs4 = None
    BS4_AVAILABLE = False

from .models import ResolvedFile, ResolvedResource
from core.system.paths import get_extensions_storage_dir

logger = logging.getLogger(__name__)

# In-memory cookie jar store per resolution session to retain cookies across redirects / multi-fetch
_SESSION_COOKIE_JARS: Dict[str, http.cookiejar.CookieJar] = {}


def _get_or_create_cookie_jar(session_id: str) -> http.cookiejar.CookieJar:
    if session_id not in _SESSION_COOKIE_JARS:
        _SESSION_COOKIE_JARS[session_id] = http.cookiejar.CookieJar()
    return _SESSION_COOKIE_JARS[session_id]


def _py_http_fetch(url: str, options_json: str, session_id: str = "default") -> str:
    """Synchronous HTTP fetch bridge callable from inside QuickJS sandbox with cookie jar and redirect support."""
    try:
        opts = json.loads(options_json) if options_json else {}
        method = str(opts.get("method") or "GET").upper()
        headers = opts.get("headers") or {}
        body = opts.get("body")
        timeout = float(opts.get("timeout") or 20.0)

        data = None
        if body is not None:
            if isinstance(body, str):
                data = body.encode("utf-8")
            elif isinstance(body, bytes):
                data = body

        header_keys_lower = {str(k).lower(): k for k in headers.keys()}
        clean_headers = {str(k): str(v) for k, v in headers.items()}
        if "user-agent" not in header_keys_lower:
            clean_headers["User-Agent"] = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            )

        cookie_jar = _get_or_create_cookie_jar(session_id)
        cookie_processor = urllib.request.HTTPCookieProcessor(cookie_jar)

        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        https_handler = urllib.request.HTTPSHandler(context=ctx)

        opener = urllib.request.build_opener(cookie_processor, https_handler)

        req = urllib.request.Request(url, data=data, headers=clean_headers, method=method)

        with opener.open(req, timeout=timeout) as resp:
            resp_bytes = resp.read()
            resp_text = resp_bytes.decode("utf-8", errors="replace")
            resp_headers: Dict[str, str] = {}
            for k, v in resp.headers.items():
                resp_headers[k.lower()] = v
                resp_headers[k] = v

            final_url = resp.geturl()
            status_code = getattr(resp, "status", 200)
            reason = getattr(resp, "reason", "OK")

            return json.dumps({
                "ok": 200 <= status_code < 300,
                "status": status_code,
                "statusText": reason or "OK",
                "headers": resp_headers,
                "url": final_url,
                "body": resp_text,
                "bytes_base64": base64.b64encode(resp_bytes).decode("ascii"),
            })

    except urllib.error.HTTPError as he:
        err_body = he.read().decode("utf-8", errors="replace") if hasattr(he, "read") else ""
        resp_headers = {}
        if hasattr(he, "headers") and he.headers:
            for k, v in he.headers.items():
                resp_headers[k.lower()] = v
                resp_headers[k] = v

        return json.dumps({
            "ok": False,
            "status": he.code,
            "statusText": he.reason or "HTTP Error",
            "headers": resp_headers,
            "url": url,
            "body": err_body,
            "bytes_base64": base64.b64encode(err_body.encode("utf-8")).decode("ascii"),
        })

    except Exception as exc:
        logger.debug("Fetch error in extension sandbox for %s: %s", url, exc)
        return json.dumps({
            "ok": False,
            "status": 500,
            "statusText": str(exc),
            "headers": {},
            "url": url,
            "body": str(exc),
            "error": str(exc),
            "bytes_base64": "",
        })


def _py_atob(b64_str: str) -> str:
    """Decodes base64 string to text with utf-8 decoding."""
    try:
        raw = base64.b64decode(b64_str)
        return raw.decode("utf-8", errors="replace")
    except Exception:
        # Fallback raw latin-1
        try:
            return base64.b64decode(b64_str).decode("latin-1")
        except Exception:
            return ""


def _py_btoa(text_str: str) -> str:
    """Encodes text to base64 string using utf-8."""
    try:
        return base64.b64encode(text_str.encode("utf-8")).decode("ascii")
    except Exception:
        return ""


def _py_hash(algo: str, text: str) -> str:
    """Computes cryptographic digest (md5, sha1, sha256)."""
    norm_algo = algo.lower().replace("-", "")
    data = text.encode("utf-8")
    if norm_algo == "md5":
        return hashlib.md5(data).hexdigest()
    elif norm_algo in ("sha1", "sha"):
        return hashlib.sha1(data).hexdigest()
    elif norm_algo in ("sha256", "sha2"):
        return hashlib.sha256(data).hexdigest()
    elif norm_algo == "sha512":
        return hashlib.sha512(data).hexdigest()
    return hashlib.sha256(data).hexdigest()


def _py_random_bytes(count: int) -> str:
    """Returns random bytes encoded as JSON array of integers."""
    n = max(0, min(int(count), 65536))
    return json.dumps(list(os.urandom(n)))


def _py_dom_query(html: str, selector: str, query_all: bool) -> str:
    """Lightweight DOM query using BeautifulSoup."""
    if not BS4_AVAILABLE:
        return json.dumps([]) if query_all else "null"
    try:
        soup = bs4.BeautifulSoup(html, "html.parser")
        if query_all:
            elements = soup.select(selector)
            results = []
            for el in elements:
                attrs = dict(el.attrs)
                # Flatten list attributes (e.g. class)
                for k, v in list(attrs.items()):
                    if isinstance(v, list):
                        attrs[k] = " ".join(v)
                    else:
                        attrs[k] = str(v)
                results.append({
                    "tag": el.name,
                    "text": el.get_text(),
                    "innerHTML": "".join(str(c) for c in el.contents),
                    "outerHTML": str(el),
                    "attrs": attrs,
                })
            return json.dumps(results)
        else:
            el = soup.select_one(selector)
            if not el:
                return "null"
            attrs = dict(el.attrs)
            for k, v in list(attrs.items()):
                if isinstance(v, list):
                    attrs[k] = " ".join(v)
                else:
                    attrs[k] = str(v)
            return json.dumps({
                "tag": el.name,
                "text": el.get_text(),
                "innerHTML": "".join(str(c) for c in el.contents),
                "outerHTML": str(el),
                "attrs": attrs,
            })
    except Exception as exc:
        logger.debug("DOM query failed for selector '%s': %s", selector, exc)
        return json.dumps([]) if query_all else "null"


def _get_extension_storage_path(ext_id: str) -> Path:
    storage_dir = get_extensions_storage_dir()
    ext_dir = storage_dir / ext_id
    ext_dir.mkdir(parents=True, exist_ok=True)
    return ext_dir / "storage.json"


def _py_storage_get(ext_id: str, key: str) -> str:
    try:
        f = _get_extension_storage_path(ext_id)
        if f.exists():
            data = json.loads(f.read_text(encoding="utf-8"))
            val = data.get(key)
            return json.dumps(val)
    except Exception as exc:
        logger.debug("Storage get error: %s", exc)
    return "null"


def _py_storage_set(ext_id: str, key: str, val_json: str) -> bool:
    try:
        f = _get_extension_storage_path(ext_id)
        data = {}
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        val = json.loads(val_json) if val_json else None
        data[key] = val
        f.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception as exc:
        logger.debug("Storage set error: %s", exc)
        return False


BOOTSTRAP_JS = """
// Getly Web Standard & Gopeed Runtime Polyfill
(function() {
    // 0. DOM Events & Web Platform Foundation
    globalThis.Event = class Event {
        constructor(type, opts = {}) {
            this.type = String(type);
            this.bubbles = !!opts.bubbles;
            this.cancelable = !!opts.cancelable;
            this.defaultPrevented = false;
            this.target = null;
            this.currentTarget = null;
            this.timeStamp = Date.now();
        }
        preventDefault() {
            if (this.cancelable) this.defaultPrevented = true;
        }
        stopPropagation() {}
        stopImmediatePropagation() {}
    };

    globalThis.CustomEvent = class CustomEvent extends globalThis.Event {
        constructor(type, opts = {}) {
            super(type, opts);
            this.detail = opts.detail !== undefined ? opts.detail : null;
        }
    };

    globalThis.EventTarget = class EventTarget {
        constructor() {
            this._listeners = {};
        }
        addEventListener(type, listener, opts) {
            if (!listener) return;
            const t = String(type);
            if (!this._listeners[t]) this._listeners[t] = [];
            const once = opts && typeof opts === 'object' ? !!opts.once : false;
            this._listeners[t].push({ listener, once });
        }
        removeEventListener(type, listener, opts) {
            const t = String(type);
            if (!this._listeners[t]) return;
            this._listeners[t] = this._listeners[t].filter(l => l.listener !== listener);
        }
        dispatchEvent(event) {
            if (!event || !event.type) return true;
            const t = String(event.type);
            event.target = this;
            event.currentTarget = this;
            const list = this._listeners[t] ? this._listeners[t].slice() : [];
            for (let item of list) {
                if (item.once) {
                    this.removeEventListener(t, item.listener);
                }
                try {
                    if (typeof item.listener === 'function') {
                        item.listener.call(this, event);
                    } else if (item.listener && typeof item.listener.handleEvent === 'function') {
                        item.listener.handleEvent(event);
                    }
                } catch (e) {
                    console.error("EventListener error:", e);
                }
            }
            if (typeof this["on" + t] === 'function') {
                try {
                    this["on" + t].call(this, event);
                } catch (e) {
                    console.error("on" + t + " error:", e);
                }
            }
            return !event.defaultPrevented;
        }
    };

    globalThis.AbortSignal = class AbortSignal extends globalThis.EventTarget {
        constructor() {
            super();
            this.aborted = false;
            this.reason = undefined;
            this.onabort = null;
        }
        throwIfAborted() {
            if (this.aborted) {
                throw this.reason || new Error("This operation was aborted");
            }
        }
        static abort(reason) {
            const sig = new AbortSignal();
            sig.aborted = true;
            sig.reason = reason || new Error("This operation was aborted");
            return sig;
        }
        static timeout(ms) {
            const sig = new AbortSignal();
            setTimeout(() => {
                sig.aborted = true;
                sig.reason = new Error("TimeoutError: The operation timed out.");
                sig.dispatchEvent(new globalThis.Event("abort"));
            }, ms);
            return sig;
        }
    };

    globalThis.AbortController = class AbortController {
        constructor() {
            this.signal = new globalThis.AbortSignal();
        }
        abort(reason) {
            if (this.signal.aborted) return;
            this.signal.aborted = true;
            this.signal.reason = reason || new Error("This operation was aborted");
            this.signal.dispatchEvent(new globalThis.Event("abort"));
        }
    };

    // Environment & Platform Globals
    globalThis.self = globalThis;
    globalThis.window = globalThis;
    globalThis.global = globalThis;

    globalThis.performance = {
        now: () => Date.now()
    };

    globalThis.navigator = {
        userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        language: "en-US",
        languages: ["en-US", "en"],
        platform: "Win32",
        onLine: true
    };

    globalThis.location = {
        href: "https://www.youtube.com",
        protocol: "https:",
        host: "www.youtube.com",
        hostname: "www.youtube.com",
        port: "",
        pathname: "/",
        search: "",
        hash: "",
        origin: "https://www.youtube.com"
    };

    globalThis.process = {
        env: { NODE_ENV: "production" },
        nextTick: (fn, ...a) => Promise.resolve().then(() => fn(...a)),
        version: "v18.0.0",
        versions: { node: "18.0.0" },
        browser: true
    };

    // Headers, Request & Response
    globalThis.Headers = class Headers {
        constructor(init) {
            this._map = {};
            if (init instanceof Headers) {
                init.forEach((v, k) => this.set(k, v));
            } else if (Array.isArray(init)) {
                for (let pair of init) {
                    if (Array.isArray(pair) && pair.length >= 2) this.append(pair[0], pair[1]);
                }
            } else if (init && typeof init === 'object') {
                for (let k in init) this.set(k, init[k]);
            }
        }
        append(name, value) {
            const key = String(name).toLowerCase();
            const val = String(value);
            if (this._map[key]) {
                this._map[key] += ", " + val;
            } else {
                this._map[key] = val;
            }
        }
        set(name, value) {
            this._map[String(name).toLowerCase()] = String(value);
        }
        get(name) {
            const val = this._map[String(name).toLowerCase()];
            return val !== undefined ? val : null;
        }
        has(name) {
            return String(name).toLowerCase() in this._map;
        }
        delete(name) {
            delete this._map[String(name).toLowerCase()];
        }
        forEach(callback, thisArg) {
            for (let k in this._map) {
                callback.call(thisArg, this._map[k], k, this);
            }
        }
        keys() { return Object.keys(this._map); }
        values() { return Object.values(this._map); }
        entries() { return Object.entries(this._map); }
        raw() { return Object.assign({}, this._map); }
        [Symbol.iterator]() { return this.entries()[Symbol.iterator](); }
    };

    globalThis.Request = class Request {
        constructor(input, init = {}) {
            if (input instanceof Request) {
                this.url = input.url;
                this.method = (init.method || input.method || "GET").toUpperCase();
                this.headers = new globalThis.Headers(init.headers || input.headers);
                this.body = init.body !== undefined ? init.body : input.body;
                this.signal = init.signal || input.signal || null;
            } else {
                this.url = String(input);
                this.method = (init.method || "GET").toUpperCase();
                this.headers = new globalThis.Headers(init.headers);
                this.body = init.body !== undefined ? init.body : null;
                this.signal = init.signal || null;
            }
        }
        clone() {
            return new Request(this.url, {
                method: this.method,
                headers: this.headers,
                body: this.body,
                signal: this.signal
            });
        }
    };

    globalThis.Response = class Response {
        constructor(body = null, init = {}) {
            this._body = body;
            this.status = init.status !== undefined ? Number(init.status) : 200;
            this.statusText = init.statusText !== undefined ? String(init.statusText) : "OK";
            this.ok = this.status >= 200 && this.status < 300;
            this.headers = init.headers instanceof globalThis.Headers ? init.headers : new globalThis.Headers(init.headers);
            this.url = init.url || "";
        }
        text() {
            if (typeof this._body === 'string') return Promise.resolve(this._body);
            if (this._body instanceof Uint8Array || this._body instanceof ArrayBuffer) {
                return Promise.resolve(new globalThis.TextDecoder().decode(this._body));
            }
            return Promise.resolve(String(this._body || ""));
        }
        json() {
            return this.text().then(t => JSON.parse(t || "{}"));
        }
        arrayBuffer() {
            if (this._body instanceof ArrayBuffer) return Promise.resolve(this._body);
            if (this._body && this._body.buffer instanceof ArrayBuffer) return Promise.resolve(this._body.buffer);
            const str = String(this._body || "");
            const buf = new Uint8Array(str.length);
            for (let i = 0; i < str.length; i++) buf[i] = str.charCodeAt(i);
            return Promise.resolve(buf.buffer);
        }
        clone() {
            return new Response(this._body, {
                status: this.status,
                statusText: this.statusText,
                headers: this.headers,
                url: this.url
            });
        }
    };

    // Blob, File, FormData & Streams
    globalThis.Blob = class Blob {
        constructor(parts = [], opts = {}) {
            this.type = opts.type || "";
            let size = 0;
            for (let p of parts) {
                if (typeof p === 'string') size += p.length;
                else if (p && p.byteLength) size += p.byteLength;
                else if (p && p.size) size += p.size;
            }
            this.size = size;
            this._parts = parts;
        }
        async text() {
            let out = "";
            for (let p of this._parts) {
                if (typeof p === 'string') out += p;
                else if (p instanceof Uint8Array || p instanceof ArrayBuffer) {
                    out += new globalThis.TextDecoder().decode(p);
                } else if (p && typeof p.text === 'function') {
                    out += await p.text();
                }
            }
            return out;
        }
        async arrayBuffer() {
            const t = await this.text();
            const b = new Uint8Array(t.length);
            for (let i = 0; i < t.length; i++) b[i] = t.charCodeAt(i);
            return b.buffer;
        }
    };

    globalThis.File = class File extends globalThis.Blob {
        constructor(parts, name, opts = {}) {
            super(parts, opts);
            this.name = String(name);
            this.lastModified = opts.lastModified || Date.now();
        }
    };

    globalThis.FormData = class FormData {
        constructor() {
            this._data = [];
        }
        append(name, value, filename) {
            this._data.push({ name: String(name), value, filename });
        }
        set(name, value, filename) {
            this._data = this._data.filter(d => d.name !== String(name));
            this._data.push({ name: String(name), value, filename });
        }
        get(name) {
            const item = this._data.find(d => d.name === String(name));
            return item ? item.value : null;
        }
        getAll(name) {
            return this._data.filter(d => d.name === String(name)).map(d => d.value);
        }
        has(name) {
            return this._data.some(d => d.name === String(name));
        }
        delete(name) {
            this._data = this._data.filter(d => d.name !== String(name));
        }
        forEach(cb, thisArg) {
            for (let d of this._data) cb.call(thisArg, d.value, d.name, this);
        }
    };

    globalThis.ReadableStream = class ReadableStream {
        constructor(underlyingSource = {}) {
            this.locked = false;
            this._source = underlyingSource;
        }
        getReader() {
            this.locked = true;
            return {
                read: () => Promise.resolve({ done: true, value: undefined }),
                releaseLock: () => { this.locked = false; }
            };
        }
    };

    globalThis.MessageError = class MessageError extends Error {
        constructor(message) {
            super(message);
            this.name = "MessageError";
        }
    };

    // CommonJS compatibility
    globalThis.module = { exports: {} };
    globalThis.exports = globalThis.module.exports;

    // Logging bridge
    globalThis.console = {
        log: function(...args) {
            __py_log("INFO", args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(" "));
        },
        warn: function(...args) {
            __py_log("WARN", args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(" "));
        },
        error: function(...args) {
            __py_log("ERROR", args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(" "));
        },
        debug: function(...args) {
            __py_log("DEBUG", args.map(a => typeof a === 'object' ? JSON.stringify(a) : String(a)).join(" "));
        }
    };

    // Base64 & Unicode Bridges
    globalThis.atob = function(b64) {
        return __py_atob(String(b64));
    };
    globalThis.btoa = function(str) {
        return __py_btoa(String(str));
    };

    // TextEncoder & TextDecoder
    globalThis.TextEncoder = class TextEncoder {
        encode(str) {
            const s = String(str);
            const utf8 = [];
            for (let i = 0; i < s.length; i++) {
                let charcode = s.charCodeAt(i);
                if (charcode < 0x80) utf8.push(charcode);
                else if (charcode < 0x800) {
                    utf8.push(0xc0 | (charcode >> 6), 0x80 | (charcode & 0x3f));
                } else if (charcode < 0xd800 || charcode >= 0xe000) {
                    utf8.push(0xe0 | (charcode >> 12), 0x80 | ((charcode >> 6) & 0x3f), 0x80 | (charcode & 0x3f));
                } else {
                    i++;
                    charcode = 0x10000 + (((charcode & 0x3ff) << 10) | (s.charCodeAt(i) & 0x3ff));
                    utf8.push(0xf0 | (charcode >> 18), 0x80 | ((charcode >> 12) & 0x3f), 0x80 | ((charcode >> 6) & 0x3f), 0x80 | (charcode & 0x3f));
                }
            }
            return new Uint8Array(utf8);
        }
    };

    globalThis.TextDecoder = class TextDecoder {
        decode(bytes) {
            if (!bytes) return "";
            if (typeof bytes === 'string') return bytes;
            const arr = Array.from(bytes);
            let out = "";
            let i = 0;
            while (i < arr.length) {
                const c = arr[i++];
                if (c < 128) {
                    out += String.fromCharCode(c);
                } else if (c > 191 && c < 224) {
                    const c2 = arr[i++];
                    out += String.fromCharCode(((c & 31) << 6) | (c2 & 63));
                } else if (c > 223 && c < 240) {
                    const c2 = arr[i++];
                    const c3 = arr[i++];
                    out += String.fromCharCode(((c & 15) << 12) | ((c2 & 63) << 6) | (c3 & 63));
                } else {
                    const c2 = arr[i++];
                    const c3 = arr[i++];
                    const c4 = arr[i++];
                    let u = (((c & 7) << 18) | ((c2 & 63) << 12) | ((c3 & 63) << 6) | (c4 & 63)) - 0x10000;
                    out += String.fromCharCode((u >> 10) + 0xD800, (u & 0x3FF) + 0xDC00);
                }
            }
            return out;
        }
    };

    // URLSearchParams Implementation
    globalThis.URLSearchParams = class URLSearchParams {
        constructor(init) {
            this._entries = [];
            this._onUpdate = null;
            if (typeof init === 'string') {
                let q = init;
                if (q.startsWith('?')) q = q.slice(1);
                if (q) {
                    const pairs = q.split('&');
                    for (let p of pairs) {
                        if (!p) continue;
                        const idx = p.indexOf('=');
                        if (idx >= 0) {
                            this._entries.push([decodeURIComponent(p.slice(0, idx)), decodeURIComponent(p.slice(idx + 1))]);
                        } else {
                            this._entries.push([decodeURIComponent(p), ""]);
                        }
                    }
                }
            } else if (Array.isArray(init)) {
                for (let pair of init) {
                    if (Array.isArray(pair) && pair.length >= 2) {
                        this._entries.push([String(pair[0]), String(pair[1])]);
                    }
                }
            } else if (init && typeof init === 'object') {
                for (let k in init) {
                    this._entries.push([String(k), String(init[k])]);
                }
            }
        }

        _notify() {
            if (typeof this._onUpdate === 'function') {
                this._onUpdate();
            }
        }

        append(name, value) {
            this._entries.push([String(name), String(value)]);
            this._notify();
        }

        set(name, value) {
            const key = String(name);
            let found = false;
            const newEntries = [];
            for (let pair of this._entries) {
                if (pair[0] === key) {
                    if (!found) {
                        newEntries.push([key, String(value)]);
                        found = true;
                    }
                } else {
                    newEntries.push(pair);
                }
            }
            if (!found) {
                newEntries.push([key, String(value)]);
            }
            this._entries = newEntries;
            this._notify();
        }

        get(name) {
            const key = String(name);
            for (let pair of this._entries) {
                if (pair[0] === key) return pair[1];
            }
            return null;
        }

        getAll(name) {
            const key = String(name);
            return this._entries.filter(p => p[0] === key).map(p => p[1]);
        }

        has(name) {
            const key = String(name);
            return this._entries.some(p => p[0] === key);
        }

        delete(name) {
            const key = String(name);
            this._entries = this._entries.filter(p => p[0] !== key);
            this._notify();
        }

        keys() {
            return this._entries.map(p => p[0]);
        }

        values() {
            return this._entries.map(p => p[1]);
        }

        entries() {
            return this._entries.slice();
        }

        forEach(callback, thisArg) {
            for (let pair of this._entries) {
                callback.call(thisArg, pair[1], pair[0], this);
            }
        }

        toString() {
            return this._entries.map(p => encodeURIComponent(p[0]) + "=" + encodeURIComponent(p[1])).join("&");
        }
    };

    // URL Implementation
    globalThis.URL = class URL {
        constructor(url, base) {
            let full = String(url);
            if (base) {
                const baseStr = String(base);
                if (!full.includes("://")) {
                    const b = new URL(baseStr);
                    if (full.startsWith("//")) {
                        full = b.protocol + full;
                    } else if (full.startsWith("/")) {
                        full = b.origin + full;
                    } else {
                        const pathParts = b.pathname.split("/");
                        pathParts.pop();
                        full = b.origin + pathParts.join("/") + "/" + full;
                    }
                }
            }

            const regex = /^(https?:|file:|magnet:)?(\\/\\/([^\\/?#:]+)(?::(\\d+))?)?([^?#]*)(?:\\?([^#]*))?(?:#(.*))?$/;
            const m = full.match(regex);
            if (!m) {
                throw new TypeError("Invalid URL: " + full);
            }

            this._protocol = m[1] || "https:";
            this._hostname = m[3] || "";
            this._port = m[4] || "";
            this._pathname = m[5] || "/";
            if (!this._pathname.startsWith("/")) this._pathname = "/" + this._pathname;
            this._hash = m[7] ? "#" + m[7] : "";

            const searchStr = m[6] ? "?" + m[6] : "";
            this._searchParams = new globalThis.URLSearchParams(searchStr);
            this._searchParams._onUpdate = () => {
                const s = this._searchParams.toString();
                this._search = s ? "?" + s : "";
            };
            this._search = searchStr;
        }

        get protocol() { return this._protocol; }
        set protocol(val) { this._protocol = String(val).endsWith(":") ? String(val) : String(val) + ":"; }

        get hostname() { return this._hostname; }
        set hostname(val) { this._hostname = String(val); }

        get port() { return this._port; }
        set port(val) { this._port = String(val); }

        get host() { return this._hostname + (this._port ? ":" + this._port : ""); }
        set host(val) {
            const parts = String(val).split(":");
            this._hostname = parts[0];
            this._port = parts[1] || "";
        }

        get origin() {
            return this._protocol + "//" + this.host;
        }

        get pathname() { return this._pathname; }
        set pathname(val) {
            let s = String(val);
            if (!s.startsWith("/")) s = "/" + s;
            this._pathname = s;
        }

        get search() {
            const s = this._searchParams.toString();
            return s ? "?" + s : "";
        }
        set search(val) {
            let s = String(val);
            if (s.startsWith("?")) s = s.slice(1);
            this._searchParams = new globalThis.URLSearchParams(s);
            this._searchParams._onUpdate = () => {
                const updated = this._searchParams.toString();
                this._search = updated ? "?" + updated : "";
            };
        }

        get searchParams() {
            return this._searchParams;
        }

        get hash() { return this._hash; }
        set hash(val) {
            let h = String(val);
            this._hash = h ? (h.startsWith("#") ? h : "#" + h) : "";
        }

        get href() {
            return this.origin + this.pathname + this.search + this.hash;
        }
        set href(val) {
            const u = new URL(val);
            this._protocol = u.protocol;
            this._hostname = u.hostname;
            this._port = u.port;
            this._pathname = u.pathname;
            this.search = u.search;
            this._hash = u.hash;
        }

        toString() { return this.href; }
        toJSON() { return this.href; }
    };

    // Timers & Async Polyfills
    let _timerCounter = 1;
    const _activeTimers = new Set();
    globalThis.setTimeout = function(fn, ms) {
        const id = _timerCounter++;
        _activeTimers.add(id);
        Promise.resolve().then(() => {
            if (_activeTimers.has(id)) {
                try { fn(); } catch (e) { console.error("setTimeout callback error:", e); }
                _activeTimers.delete(id);
            }
        });
        return id;
    };
    globalThis.clearTimeout = function(id) {
        _activeTimers.delete(id);
    };
    globalThis.queueMicrotask = function(fn) {
        Promise.resolve().then(fn);
    };

    // Crypto & Hashing
    globalThis.crypto = {
        getRandomValues: function(typedArray) {
            if (!typedArray || typeof typedArray.length !== 'number') return typedArray;
            const raw = JSON.parse(__py_random_bytes(typedArray.length));
            for (let i = 0; i < typedArray.length; i++) {
                typedArray[i] = raw[i];
            }
            return typedArray;
        },
        subtle: {
            digest: function(algorithm, data) {
                const algo = typeof algorithm === 'string' ? algorithm : (algorithm.name || 'SHA-256');
                let str = "";
                if (typeof data === 'string') str = data;
                else if (data instanceof Uint8Array || Array.isArray(data)) {
                    str = new globalThis.TextDecoder().decode(data);
                } else if (data && data.buffer) {
                    str = new globalThis.TextDecoder().decode(new Uint8Array(data.buffer));
                }
                const hex = __py_hash(algo, str);
                const bytes = [];
                for (let i = 0; i < hex.length; i += 2) {
                    bytes.push(parseInt(hex.substr(i, 2), 16));
                }
                return Promise.resolve(new Uint8Array(bytes).buffer);
            }
        }
    };

    // Convenience Global Hashes
    globalThis.md5 = function(s) { return __py_hash("md5", String(s)); };
    globalThis.sha1 = function(s) { return __py_hash("sha1", String(s)); };
    globalThis.sha256 = function(s) { return __py_hash("sha256", String(s)); };

    // DOMParser & Lightweight Scraping
    class DOMNode {
        constructor(raw) {
            this._raw = raw || {};
            this.tagName = (this._raw.tag || "").toUpperCase();
            this.innerText = this._raw.text || "";
            this.textContent = this._raw.text || "";
            this.innerHTML = this._raw.innerHTML || "";
            this.outerHTML = this._raw.outerHTML || "";
            this.attributes = this._raw.attrs || {};
        }

        getAttribute(name) {
            const n = String(name).toLowerCase();
            for (let k in this.attributes) {
                if (k.toLowerCase() === n) return this.attributes[k];
            }
            return null;
        }

        hasAttribute(name) {
            return this.getAttribute(name) !== null;
        }

        querySelector(sel) {
            const raw = JSON.parse(__py_dom_query(this.outerHTML, sel, false));
            return raw ? new DOMNode(raw) : null;
        }

        querySelectorAll(sel) {
            const rawList = JSON.parse(__py_dom_query(this.outerHTML, sel, true));
            return (rawList || []).map(r => new DOMNode(r));
        }
    }

    globalThis.DOMParser = class DOMParser {
        parseFromString(str, type) {
            const html = String(str);
            return {
                querySelector: function(sel) {
                    const raw = JSON.parse(__py_dom_query(html, sel, false));
                    return raw ? new DOMNode(raw) : null;
                },
                querySelectorAll: function(sel) {
                    const rawList = JSON.parse(__py_dom_query(html, sel, true));
                    return (rawList || []).map(r => new DOMNode(r));
                },
                getElementById: function(id) {
                    return this.querySelector("#" + id);
                },
                getElementsByTagName: function(tag) {
                    return this.querySelectorAll(tag);
                },
                body: new DOMNode({ tag: "body", outerHTML: html, innerHTML: html, text: "" }),
                title: (html.match(/<title>([^<]*)<\\/title>/i) || [])[1] || ""
            };
        }
    };

    // Full fetch() Implementation with Request / Response support
    globalThis.fetch = function(input, init = {}) {
        return new Promise((resolve) => {
            let urlStr = "";
            let method = "GET";
            let headersObj = {};
            let body = undefined;
            let timeout = 25.0;

            if (input instanceof globalThis.Request) {
                urlStr = input.url;
                method = (init.method || input.method || "GET").toUpperCase();
                if (input.headers) {
                    input.headers.forEach((v, k) => { headersObj[k] = v; });
                }
                body = init.body !== undefined ? init.body : input.body;
            } else if (input && typeof input === 'object' && input.url) {
                urlStr = String(input.url);
                method = (init.method || input.method || "GET").toUpperCase();
                if (input.headers) {
                    if (typeof input.headers.forEach === 'function') {
                        input.headers.forEach((v, k) => { headersObj[k] = v; });
                    } else if (typeof input.headers === 'object') {
                        Object.assign(headersObj, input.headers);
                    }
                }
                body = init.body !== undefined ? init.body : input.body;
            } else {
                urlStr = String(input);
                method = (init.method || "GET").toUpperCase();
                body = init.body;
            }

            if (init.headers) {
                if (init.headers instanceof globalThis.Headers) {
                    init.headers.forEach((v, k) => { headersObj[k] = v; });
                } else if (typeof init.headers === 'object') {
                    for (let k in init.headers) headersObj[k] = String(init.headers[k]);
                }
            }

            if (init.timeout) timeout = Number(init.timeout);

            const optionsPayload = {
                method: method,
                headers: headersObj,
                body: typeof body === 'object' ? JSON.stringify(body) : (body !== undefined ? String(body) : undefined),
                timeout: timeout
            };

            const sessionId = globalThis.__current_session_id || "default";
            const raw = __py_http_fetch(urlStr, JSON.stringify(optionsPayload), sessionId);
            const res = JSON.parse(raw);
            const respHeaders = new globalThis.Headers(res.headers || {});

            let bodyContent = res.body || "";
            const responseObj = new globalThis.Response(bodyContent, {
                status: res.status,
                statusText: res.statusText,
                headers: respHeaders,
                url: res.url || urlStr
            });
            responseObj.ok = res.ok;
            responseObj.url = res.url || urlStr;
            responseObj.arrayBuffer = () => {
                const b64 = res.bytes_base64 || "";
                if (!b64) return Promise.resolve(new ArrayBuffer(0));
                const bin = atob(b64);
                const buf = new Uint8Array(bin.length);
                for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
                return Promise.resolve(buf.buffer);
            };

            resolve(responseObj);
        });
    };

    // Gopeed Official API Namespace
    globalThis.gopeed = {
        events: {
            _onResolveHandler: null,
            _onStartHandler: null,
            _onErrorHandler: null,
            onResolve: function(cb) { this._onResolveHandler = cb; },
            onStart: function(cb) { this._onStartHandler = cb; },
            onError: function(cb) { this._onErrorHandler = cb; }
        },
        net: {
            fetch: globalThis.fetch
        },
        settings: {},
        storage: {
            get: function(key) {
                const extId = globalThis.__current_ext_id || "default";
                const raw = __py_storage_get(extId, String(key));
                return JSON.parse(raw);
            },
            set: function(key, val) {
                const extId = globalThis.__current_ext_id || "default";
                __py_storage_set(extId, String(key), JSON.stringify(val));
            }
        },
        logger: {
            debug: function(...args) { console.debug(...args); },
            info: function(...args) { console.log(...args); },
            warn: function(...args) { console.warn(...args); },
            error: function(...args) { console.error(...args); }
        }
    };
})();
"""


class QuickJSSandbox:
    """
    Sandboxed JavaScript execution environment using QuickJS.
    Executes Gopeed extension scripts, providing full Web APIs, fetch(),
    URL, crypto, DOMParser, and gopeed official contract.
    """

    def __init__(self, memory_limit_mb: int = 48):
        if not QUICKJS_AVAILABLE:
            raise RuntimeError("QuickJS is not available in Python environment.")
        self.memory_limit_mb = memory_limit_mb

    def _create_context(
        self,
        settings: Optional[Dict[str, Any]] = None,
        ext_id: str = "default",
        session_id: str = "default",
    ) -> Any:
        cx = quickjs.Context()
        cx.set_memory_limit(self.memory_limit_mb * 1024 * 1024)

        # Logging callback
        def py_log(level: str, msg: str):
            if level == "ERROR":
                logger.error("[ExtensionJS:%s] %s", ext_id, msg)
            elif level == "WARN":
                logger.warning("[ExtensionJS:%s] %s", ext_id, msg)
            else:
                logger.debug("[ExtensionJS:%s] %s", ext_id, msg)

        cx.add_callable("__py_log", py_log)
        cx.add_callable("__py_http_fetch", _py_http_fetch)
        cx.add_callable("__py_atob", _py_atob)
        cx.add_callable("__py_btoa", _py_btoa)
        cx.add_callable("__py_hash", _py_hash)
        cx.add_callable("__py_random_bytes", _py_random_bytes)
        cx.add_callable("__py_dom_query", _py_dom_query)
        cx.add_callable("__py_storage_get", _py_storage_get)
        cx.add_callable("__py_storage_set", _py_storage_set)

        # Initialize bootstrap JS environment
        cx.eval(BOOTSTRAP_JS)

        # Set session and extension identifier globals
        cx.eval(f"globalThis.__current_ext_id = {json.dumps(ext_id)};")
        cx.eval(f"globalThis.__current_session_id = {json.dumps(session_id)};")

        # Inject settings if present
        if settings:
            settings_json = json.dumps(settings)
            cx.eval(f"globalThis.gopeed.settings = {settings_json};")

        return cx

    def resolve_url(
        self,
        script_code: str,
        raw_url: str,
        headers: Optional[Dict[str, str]] = None,
        settings: Optional[Dict[str, Any]] = None,
        ext_id: str = "default",
        max_jobs: int = 5000,
    ) -> Optional[ResolvedResource]:
        """
        Runs an extension script in the sandbox and triggers onResolve(ctx).
        Returns the resolved direct download resource, or None if not resolved.
        """
        session_id = f"{ext_id}_{int(time.time() * 1000)}"
        cx = self._create_context(settings=settings, ext_id=ext_id, session_id=session_id)

        try:
            # 1. Execute Extension Code
            cx.eval(script_code)

            # 2. Setup Context Object and Run Resolver
            req_data = {
                "url": raw_url,
                "headers": headers or {},
                "extra": {},
                "labels": {},
            }
            req_json = json.dumps(req_data)

            runner_code = f"""
            var __resolved_result = null;
            var __resolved_error = null;

            (async () => {{
                try {{
                    const ctx = {{
                        req: {req_json},
                        res: null
                    }};

                    const handler = gopeed.events._onResolveHandler || 
                                    (typeof module.exports === 'function' ? module.exports : null);

                    if (typeof handler === 'function') {{
                        const ret = await handler(ctx);
                        __resolved_result = ctx.res || ret;
                    }} else {{
                        __resolved_error = "No onResolve handler or module.exports function registered";
                    }}
                }} catch (e) {{
                    __resolved_error = e.stack || String(e);
                }}
            }})();
            """
            cx.eval(runner_code)

            # 3. Drain QuickJS Microtask / Job Queue
            jobs_executed = 0
            while cx.execute_pending_job():
                jobs_executed += 1
                if jobs_executed > max_jobs:
                    logger.warning("QuickJS extension execution exceeded maximum pending job limit (%d)", max_jobs)
                    break

            # 4. Check for errors
            err = cx.eval("__resolved_error")
            if err:
                logger.warning("Extension '%s' onResolve error for '%s': %s", ext_id, raw_url, err)
                return None

            # 5. Extract Result JSON
            raw_res = cx.eval("JSON.stringify(__resolved_result)")
            if not raw_res or raw_res == "null" or raw_res == "undefined":
                return None

            res_dict = json.loads(raw_res)
            return self._parse_res_data(res_dict, raw_url)

        except Exception as exc:
            logger.error("Exception in QuickJS sandbox resolution for '%s': %s", raw_url, exc)
            return None
        finally:
            _SESSION_COOKIE_JARS.pop(session_id, None)

    def _parse_res_data(self, res_dict: Any, fallback_url: str) -> Optional[ResolvedResource]:
        """Parses the ctx.res object structure into a standardized ResolvedResource."""
        if not isinstance(res_dict, dict):
            return None

        filename = res_dict.get("name")
        direct_url = ""
        headers: Dict[str, str] = {}
        files_list: List[ResolvedFile] = []

        # 1. Check for files array (multi-file or single item)
        raw_files = res_dict.get("files")
        if isinstance(raw_files, list) and raw_files:
            for item in raw_files:
                if not isinstance(item, dict):
                    continue
                f_name = item.get("name")
                f_size = item.get("size")
                f_path = item.get("path")
                f_req = item.get("req") or {}
                f_url = ""
                f_method = "GET"
                f_headers = {}
                f_extra = {}

                if isinstance(f_req, dict):
                    f_url = str(f_req.get("url") or "")
                    f_method = str(f_req.get("method") or "GET").upper()
                    f_headers = f_req.get("headers") or f_req.get("header") or {}
                    f_extra = f_req.get("extra") or {}
                elif "url" in item:
                    f_url = str(item.get("url") or "")
                    f_headers = item.get("headers") or item.get("header") or {}

                if f_url:
                    files_list.append(
                        ResolvedFile(
                            name=f_name,
                            url=f_url,
                            headers={str(k): str(v) for k, v in f_headers.items()} if isinstance(f_headers, dict) else {},
                            size=int(f_size) if f_size is not None and str(f_size).isdigit() else None,
                            path=str(f_path) if f_path else None,
                            method=f_method,
                            extra=f_extra if isinstance(f_extra, dict) else {},
                        )
                    )

            if files_list:
                direct_url = files_list[0].url
                if not filename:
                    filename = files_list[0].name
                headers = dict(files_list[0].headers)

        # 2. Check for single req structure: { name: "...", req: { url: "...", headers: {} } }
        req_obj = res_dict.get("req")
        if isinstance(req_obj, dict):
            if not direct_url:
                direct_url = str(req_obj.get("url") or "")
            req_headers = req_obj.get("headers") or req_obj.get("header") or {}
            if isinstance(req_headers, dict):
                headers.update({str(k): str(v) for k, v in req_headers.items()})

        # 3. Direct URL at top-level: { name: "...", url: "..." }
        if not direct_url and "url" in res_dict:
            direct_url = str(res_dict["url"])

        # 4. Top-level headers
        top_headers = res_dict.get("headers") or res_dict.get("header")
        if isinstance(top_headers, dict):
            headers.update({str(k): str(v) for k, v in top_headers.items()})

        if not direct_url and not files_list:
            return None

        extra = res_dict.get("extra") if isinstance(res_dict.get("extra"), dict) else {}

        return ResolvedResource(
            url=direct_url or (files_list[0].url if files_list else fallback_url),
            filename=filename,
            headers=headers,
            files=files_list,
            extra=extra,
        )


def is_video_streaming_url(url: str) -> bool:
    """Checks whether a URL belongs to a video/streaming service (e.g. YouTube, Vimeo, etc.)."""
    u = url.lower()
    return any(host in u for host in (
        "youtube.com/watch",
        "youtube.com/shorts",
        "youtube.com/playlist",
        "youtu.be/",
        "m.youtube.com",
        "vimeo.com/",
        "dailymotion.com/",
        "tiktok.com/",
        "twitch.tv/",
    ))


def resolve_with_ytdlp(raw_url: str, source_address: Optional[str] = None) -> Optional[ResolvedResource]:
    """
    Extracts direct stream URLs and metadata for video/audio streams using yt-dlp.
    Serves as an automatic streaming fallback provider when JavaScript extensions
    encounter expired client tokens or remote extraction failures.
    Binds to source_address if provided to align IP signatures on token-gated CDNs.
    """
    # 1. Try Python module yt_dlp
    try:
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "skip_download": True,
            "format": "best[ext=mp4][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]/best/bestvideo+bestaudio/bestvideo/best",
        }
        if source_address:
            ydl_opts["source_address"] = source_address

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(raw_url, download=False)
            if info:
                title = info.get("title") or "video"
                direct_url = info.get("url")
                filesize = info.get("filesize") or info.get("filesize_approx")
                http_headers = info.get("http_headers") or {}
                ext = info.get("ext") or "mp4"

                formats = info.get("formats") or []
                if not direct_url and formats:
                    # Prefer progressive formats (audio + video) with direct url
                    prog = [f for f in formats if f.get("url") and f.get("vcodec") != "none" and f.get("acodec") != "none"]
                    cand = prog[-1] if prog else next((f for f in reversed(formats) if f.get("url")), None)
                    if cand:
                        direct_url = cand.get("url")
                        if cand.get("ext"):
                            ext = cand.get("ext")
                        filesize = cand.get("filesize") or cand.get("filesize_approx") or filesize
                        if cand.get("http_headers"):
                            http_headers.update(cand["http_headers"])

                if not filesize and direct_url and "clen=" in direct_url:
                    try:
                        from urllib.parse import parse_qs, urlparse
                        qs = parse_qs(urlparse(direct_url).query)
                        if "clen" in qs:
                            filesize = int(qs["clen"][0])
                    except Exception:
                        pass

                if direct_url:
                    filename = re.sub(r'[\\/*?:"<>|]', "_", f"{title}.{ext}")
                    res_file = ResolvedFile(
                        name=filename,
                        url=direct_url,
                        headers={str(k): str(v) for k, v in http_headers.items()},
                        size=int(filesize) if filesize else None,
                    )
                    return ResolvedResource(
                        url=direct_url,
                        filename=filename,
                        headers={str(k): str(v) for k, v in http_headers.items()},
                        files=[res_file],
                        extra={"provider": "yt-dlp", "original_url": raw_url},
                    )
    except Exception as exc:
        logger.debug("yt-dlp Python API extraction error for %s: %s", raw_url, exc)

    # 2. Try CLI yt-dlp executable
    ytdlp_bin = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if ytdlp_bin:
        try:
            cmd = [
                ytdlp_bin,
                "-J",
                "--no-playlist",
                "-f",
                "best[ext=mp4][acodec!=none][vcodec!=none]/best[acodec!=none][vcodec!=none]/best/bestvideo+bestaudio/bestvideo/best",
            ]
            if source_address:
                cmd.extend(["--source-address", source_address])
            cmd.append(raw_url)

            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if res.returncode == 0 and res.stdout:
                info = json.loads(res.stdout)
                title = info.get("title") or "video"
                direct_url = info.get("url")
                filesize = info.get("filesize") or info.get("filesize_approx")
                http_headers = info.get("http_headers") or {}
                ext = info.get("ext") or "mp4"

                formats = info.get("formats") or []
                if not direct_url and formats:
                    prog = [f for f in formats if f.get("url") and f.get("vcodec") != "none" and f.get("acodec") != "none"]
                    cand = prog[-1] if prog else next((f for f in reversed(formats) if f.get("url")), None)
                    if cand:
                        direct_url = cand.get("url")
                        if cand.get("ext"):
                            ext = cand.get("ext")
                        filesize = cand.get("filesize") or cand.get("filesize_approx") or filesize
                        if cand.get("http_headers"):
                            http_headers.update(cand["http_headers"])

                if not filesize and direct_url and "clen=" in direct_url:
                    try:
                        from urllib.parse import parse_qs, urlparse
                        qs = parse_qs(urlparse(direct_url).query)
                        if "clen" in qs:
                            filesize = int(qs["clen"][0])
                    except Exception:
                        pass

                if direct_url:
                    filename = re.sub(r'[\\/*?:"<>|]', "_", f"{title}.{ext}")
                    res_file = ResolvedFile(
                        name=filename,
                        url=direct_url,
                        headers={str(k): str(v) for k, v in http_headers.items()},
                        size=int(filesize) if filesize else None,
                    )
                    return ResolvedResource(
                        url=direct_url,
                        filename=filename,
                        headers={str(k): str(v) for k, v in http_headers.items()},
                        files=[res_file],
                        extra={"provider": "yt-dlp", "original_url": raw_url},
                    )
        except Exception as exc:
            logger.debug("yt-dlp CLI extraction error for %s: %s", raw_url, exc)

    return None
