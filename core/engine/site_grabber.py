"""
Site Explorer & Web Grabber Engine.
Analyzes any web page, extracts all downloadable files and multimedia assets,
resolves absolute URLs, deduplicates them, and classifies them into categories.
Includes native PrivateBin client-side AES-GCM decryption for paste links,
support for fragment-based filenames, and file hoster link recognition.
"""

import asyncio
import base64
from dataclasses import dataclass
from html.parser import HTMLParser
import json
import logging
import os
import posixpath
import re
from typing import Dict, List, Optional, Set, Tuple
import urllib.parse
import zlib
import aiohttp

from core.scheduler.categories import detect_category, CATEGORY_OTHER

logger = logging.getLogger(__name__)

# Common web page code extensions to ignore as downloadable files
IGNORED_EXTENSIONS: Set[str] = {
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".rss",
    ".htm", ".html", ".php", ".asp", ".aspx", ".jsp",
}

# Tracking / ad domains to filter out
IGNORED_HOST_KEYWORDS: List[str] = [
    "google-analytics", "googletagmanager", "doubleclick",
    "facebook.net", "clarity.ms", "hotjar", "analytics",
    "adservice", "popads", "trafficjunky",
]

# Known file hosters
KNOWN_HOSTERS: Set[str] = {
    "fuckingfast.co",
    "datanodes.to",
    "mega.nz",
    "mediafire.com",
    "rapidgator.net",
    "1fichier.com",
    "qiwi.gg",
    "gofile.io",
    "pixeldrain.com",
    "krakenfiles.com",
    "filecrypt.co",
    "filecrypt.cc",
    "multiup.io",
    "send.cm",
}

B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_decode(v: str) -> bytes:
    """Decodes a Base58 string (used by PrivateBin encryption keys)."""
    origlen = len(v)
    v = v.lstrip(B58_ALPHABET[0])
    newlen = len(v)
    acc = 0
    for char in v:
        acc = acc * 58 + B58_ALPHABET.index(char)
    result = []
    while acc > 0:
        acc, mod = divmod(acc, 256)
        result.append(mod)
    pad = b"\x00" * (origlen - newlen)
    return pad + bytes(reversed(result))


async def _decrypt_privatebin_paste(session: aiohttp.ClientSession, url: str) -> Optional[str]:
    """
    Decrypts client-side AES-256-GCM encrypted PrivateBin pastes (v1 and v2 protocols).
    Extracts paste ID and key from URL fragment and queries JSON endpoint.
    """
    try:
        parsed = urllib.parse.urlparse(url)
        fragment = parsed.fragment
        if not fragment:
            return None
        if "&" in fragment:
            fragment = fragment.split("&")[0]

        query = parsed.query
        paste_id = None
        if query:
            if "=" in query:
                qs = urllib.parse.parse_qs(query)
                paste_id = qs.get("pasteid", [None])[0] or qs.get("id", [None])[0]
            if not paste_id and re.match(r"^[0-9a-fA-F]{16,64}$", query):
                paste_id = query

        if not paste_id:
            parts = [p for p in parsed.path.strip("/").split("/") if p]
            if parts and re.match(r"^[0-9a-fA-F]{16,64}$", parts[-1]):
                paste_id = parts[-1]

        if not paste_id:
            return None

        api_url = f"{parsed.scheme}://{parsed.netloc}/?{paste_id}"
        headers = {
            "X-Requested-With": "JSONHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Referer": url,
        }

        async with session.get(api_url, headers=headers) as resp:
            if resp.status != 200:
                alt_api_url = f"{parsed.scheme}://{parsed.netloc}/?pasteid={paste_id}"
                async with session.get(alt_api_url, headers=headers) as alt_resp:
                    if alt_resp.status != 200:
                        return None
                    data = await alt_resp.json(content_type=None)
            else:
                data = await resp.json(content_type=None)

        if not data or data.get("status") != 0 or "ct" not in data or "adata" not in data:
            return None

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes

        adata_spec = data["adata"]
        spec = adata_spec[0]
        iv = base64.b64decode(spec[0])
        salt = base64.b64decode(spec[1])
        iterations = spec[2]
        keysize = spec[3]
        compression = spec[7] if len(spec) > 7 else "zlib"

        raw_key = _base58_decode(fragment).rjust(32, b"\x00")

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=keysize // 8,
            salt=salt,
            iterations=iterations,
        )
        derived_key = kdf.derive(raw_key)

        aad = json.dumps(adata_spec, separators=(",", ":")).encode("utf-8")
        ct_full = base64.b64decode(data["ct"])

        aesgcm = AESGCM(derived_key)
        decrypted_bytes = aesgcm.decrypt(iv, ct_full, aad)

        if compression == "zlib":
            try:
                decrypted_bytes = zlib.decompress(decrypted_bytes)
            except Exception:
                decrypted_bytes = zlib.decompress(decrypted_bytes, -zlib.MAX_WBITS)

        decrypted_text = decrypted_bytes.decode("utf-8", errors="ignore")
        if data.get("v", 1) > 1:
            try:
                paste_obj = json.loads(decrypted_text)
                return paste_obj.get("paste", decrypted_text)
            except Exception:
                return decrypted_text
        return decrypted_text
    except Exception as exc:
        logger.debug("Failed to decrypt PrivateBin paste %s: %s", url, exc)
        return None


@dataclass
class GrabbedResource:
    url: str
    filename: str
    category: str
    source_tag: str


class _HTMLResourceExtractor(HTMLParser):
    """Custom fast HTML parser that gathers raw candidate URLs and tags."""

    def __init__(self):
        super().__init__()
        self.raw_items: List[tuple[str, str]] = []  # (url, tag)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]):
        attr_dict = {k.lower(): v for k, v in attrs if v}

        if tag == "a":
            href = attr_dict.get("href")
            if href:
                self.raw_items.append((href, "link"))
        elif tag in ("img", "image"):
            src = attr_dict.get("src") or attr_dict.get("data-src") or attr_dict.get("data-original")
            if src:
                self.raw_items.append((src, "image"))
        elif tag == "video":
            src = attr_dict.get("src")
            if src:
                self.raw_items.append((src, "video"))
        elif tag == "audio":
            src = attr_dict.get("src")
            if src:
                self.raw_items.append((src, "audio"))
        elif tag == "source":
            src = attr_dict.get("src")
            if src:
                self.raw_items.append((src, "media"))

        # Check generic data-url or data-href
        for k, v in attr_dict.items():
            if k.startswith("data-") and ("url" in k or "href" in k or "download" in k):
                if v:
                    self.raw_items.append((v, tag))


class SiteGrabber:
    """
    Crawls and analyzes a web page URL to identify downloadable files and media.
    Features PrivateBin client-side AES-GCM decryption, hash-fragment filename parsing,
    and file hoster recognition.
    """

    @staticmethod
    def _extract_file_info(url: str, source_tag: str = "link") -> Tuple[Optional[str], Optional[str], str]:
        """
        Derives (filename, extension, category) for a URL.
        Returns (None, None, CATEGORY_OTHER) if this URL is purely a web navigation page.
        """
        if not url:
            return (None, None, CATEGORY_OTHER)

        clean = url.strip()
        lower = clean.lower()

        # Reject non-http protocols
        if lower.startswith(("javascript:", "mailto:", "tel:", "data:", "#", "about:")):
            return (None, None, CATEGORY_OTHER)

        # Reject ignored ad / tracking domains
        for kw in IGNORED_HOST_KEYWORDS:
            if kw in lower:
                return (None, None, CATEGORY_OTHER)

        try:
            parsed = urllib.parse.urlparse(clean)
        except Exception:
            return (None, None, CATEGORY_OTHER)

        host = parsed.netloc.lower()

        # 1. Inspect fragment (used by hosters e.g. fuckingfast.co/<id>#<filename>)
        if parsed.fragment:
            frag = parsed.fragment.split("&")[0]
            unquoted_frag = urllib.parse.unquote(frag)
            if "." in unquoted_frag and len(unquoted_frag) > 3:
                frag_cand = os.path.basename(unquoted_frag.replace("\\", "/"))
                f_ext = posixpath.splitext(frag_cand)[1].lower()
                if f_ext and f_ext not in IGNORED_EXTENSIONS:
                    cat = detect_category(frag_cand)
                    return (frag_cand, f_ext, cat)

        # 2. Inspect query parameters for explicit filename
        if parsed.query:
            qs = urllib.parse.parse_qs(parsed.query)
            for key in ("filename", "file", "name", "fn", "download"):
                if key in qs and qs[key]:
                    cand = urllib.parse.unquote(qs[key][0])
                    if "." in cand:
                        cand_base = os.path.basename(cand.replace("\\", "/"))
                        q_ext = posixpath.splitext(cand_base)[1].lower()
                        if q_ext and q_ext not in IGNORED_EXTENSIONS:
                            cat = detect_category(cand_base)
                            return (cand_base, q_ext, cat)

        # 3. Inspect path basename
        path_base = posixpath.basename(parsed.path)
        if path_base and "." in path_base:
            clean_base = urllib.parse.unquote(re.split(r"[?#]", path_base)[0])
            p_ext = posixpath.splitext(clean_base)[1].lower()
            if p_ext and p_ext not in IGNORED_EXTENSIONS:
                cat = detect_category(clean_base)
                return (clean_base, p_ext, cat)

        # 4. Media tag origin (<img>, <video>, <audio>, <source>)
        if source_tag in ("image", "img"):
            clean_base = urllib.parse.unquote(path_base) if path_base else "image.jpg"
            if "." not in clean_base:
                clean_base += ".jpg"
            return (clean_base, ".jpg", "Immagini_ISO")
        elif source_tag in ("video",):
            clean_base = urllib.parse.unquote(path_base) if path_base else "video.mp4"
            if "." not in clean_base:
                clean_base += ".mp4"
            return (clean_base, ".mp4", "Video")
        elif source_tag in ("audio",):
            clean_base = urllib.parse.unquote(path_base) if path_base else "audio.mp3"
            if "." not in clean_base:
                clean_base += ".mp3"
            return (clean_base, ".mp3", "Audio")

        # 5. Known file hoster with non-empty resource path (e.g. fuckingfast.co/<id>, datanodes.to/<id>)
        is_known_hoster = any(h in host for h in KNOWN_HOSTERS)
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]
        if is_known_hoster and path_parts:
            res_name = path_parts[-1]
            cat = detect_category(res_name)
            if cat == CATEGORY_OTHER:
                cat = "Compressi"  # Default for game / file hosters
            return (res_name, "", cat)

        # Not a downloadable file: purely a web page link
        return (None, None, CATEGORY_OTHER)

    @classmethod
    def _is_valid_candidate(cls, url: str, source_tag: str = "link") -> bool:
        """Determines if a candidate URL is a downloadable file resource."""
        fn, _, _ = cls._extract_file_info(url, source_tag=source_tag)
        return fn is not None

    @classmethod
    def _synthesize_filename(cls, url: str, category: str, fallback_index: int, source_tag: str = "link") -> str:
        """Derives a clean filename from the URL, or synthesizes one if needed."""
        fn, _, _ = cls._extract_file_info(url, source_tag=source_tag)
        if fn:
            return fn

        ext = ".bin"
        if category == "Immagini_ISO":
            ext = ".jpg"
        elif category == "Video":
            ext = ".mp4"
        elif category == "Audio":
            ext = ".mp3"
        elif category == "Documenti":
            ext = ".pdf"
        elif category == "Compressi":
            ext = ".zip"
        elif category == "Programmi":
            ext = ".exe"

        return f"grabbed_file_{fallback_index:03d}{ext}"

    async def analyze_page(
        self,
        page_url: str,
        user_agent: Optional[str] = None,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        timeout_sec: float = 15.0,
    ) -> List[GrabbedResource]:
        """
        Fetches the HTML of page_url and extracts all downloadable resources.
        Supports PrivateBin encrypted pastes and hoster link parsing.
        """
        clean_url = page_url.strip()
        if not (clean_url.startswith("http://") or clean_url.startswith("https://")):
            clean_url = "https://" + clean_url

        headers = {
            "User-Agent": user_agent or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        if cookies:
            headers["Cookie"] = cookies

        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            # 1. Check if page_url is an encrypted PrivateBin paste
            parsed_in = urllib.parse.urlparse(clean_url)
            is_paste = "paste" in parsed_in.netloc.lower() or (
                parsed_in.fragment and re.match(r"^[0-9a-fA-F]{16,64}$", parsed_in.query)
            )

            if is_paste and parsed_in.fragment:
                decrypted = await _decrypt_privatebin_paste(session, clean_url)
                if decrypted:
                    logger.info("SiteGrabber: Decrypted PrivateBin paste (%d chars)", len(decrypted))
                    extracted_urls = re.findall(r"https?://[^\s\"\'<>]+", decrypted)
                    seen_paste_urls: Set[str] = set()
                    results: List[GrabbedResource] = []
                    for u in extracted_urls:
                        if u in seen_paste_urls:
                            continue
                        seen_paste_urls.add(u)
                        fn, ext, cat = self._extract_file_info(u, source_tag="paste")
                        if fn:
                            results.append(
                                GrabbedResource(
                                    url=u,
                                    filename=fn,
                                    category=cat,
                                    source_tag="paste",
                                )
                            )
                    if results:
                        logger.info(
                            "SiteGrabber: Extracted %d download links from PrivateBin paste %s",
                            len(results),
                            clean_url,
                        )
                        return results

            # 2. Fetch standard HTML
            try:
                async with session.get(clean_url, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        logger.warning("SiteGrabber: HTTP %d when loading %s", resp.status, clean_url)
                        return []
                    html_content = await resp.text(errors="ignore")
                    final_url = str(resp.url)
            except Exception as exc:
                logger.error("SiteGrabber: Error fetching %s: %s", clean_url, exc)
                return []

        # 3. Parse HTML with _HTMLResourceExtractor
        parser = _HTMLResourceExtractor()
        try:
            parser.feed(html_content)
        except Exception as exc:
            logger.debug("HTML parser error (ignoring and proceeding): %s", exc)

        # 4. Extract additional URLs using regex
        regex_urls = re.findall(r'https?://[^\s"\'<>]+', html_content)
        for u in regex_urls:
            parser.raw_items.append((u, "regex"))

        # 5. Resolve relative to absolute and deduplicate valid candidates
        seen_urls: Set[str] = set()
        results: List[GrabbedResource] = []

        for raw_link, tag in parser.raw_items:
            try:
                abs_url = urllib.parse.urljoin(final_url, raw_link)
            except Exception:
                continue

            # Validate that this is a real downloadable resource, not a plain web page
            fn, ext, cat = self._extract_file_info(abs_url, source_tag=tag)
            if not fn:
                continue

            if abs_url in seen_urls:
                continue
            seen_urls.add(abs_url)

            results.append(
                GrabbedResource(
                    url=abs_url,
                    filename=fn,
                    category=cat,
                    source_tag=tag,
                )
            )

        logger.info("SiteGrabber: Found %d downloadable resources from %s", len(results), clean_url)
        return results

    async def crawl(
        self,
        start_url: str,
        depth: int = 1,
        max_pages: int = 40,
        user_agent: Optional[str] = None,
        cookies: Optional[str] = None,
        referer: Optional[str] = None,
        timeout_sec: float = 12.0,
    ) -> List[GrabbedResource]:
        """
        Recursively crawls a website up to the specified depth and extracts all
        downloadable files and media discovered across pages. Supports PrivateBin
        paste decryption across crawled links.
        """
        clean_start = start_url.strip()
        if not (clean_start.startswith("http://") or clean_start.startswith("https://")):
            clean_start = "https://" + clean_start

        if depth <= 1:
            return await self.analyze_page(
                clean_start,
                user_agent=user_agent,
                cookies=cookies,
                referer=referer,
                timeout_sec=timeout_sec,
            )

        headers = {
            "User-Agent": user_agent
            or "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "it-IT,it;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        if referer:
            headers["Referer"] = referer
        if cookies:
            headers["Cookie"] = cookies

        start_parsed = urllib.parse.urlparse(clean_start)
        base_host = start_parsed.netloc.lower()

        visited_pages: Set[str] = set()
        to_visit: List[tuple[str, int]] = [(clean_start, 1)]
        seen_to_visit: Set[str] = {clean_start}

        seen_resource_urls: Set[str] = set()
        all_resources: List[GrabbedResource] = []

        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        connector = aiohttp.TCPConnector(ssl=False)

        async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
            while to_visit and len(visited_pages) < max_pages:
                curr_url, curr_depth = to_visit.pop(0)
                if curr_url in visited_pages:
                    continue
                visited_pages.add(curr_url)

                curr_parsed = urllib.parse.urlparse(curr_url)

                # Check if this subpage is a PrivateBin paste
                is_paste = "paste" in curr_parsed.netloc.lower() or (
                    curr_parsed.fragment and re.match(r"^[0-9a-fA-F]{16,64}$", curr_parsed.query)
                )

                if is_paste and curr_parsed.fragment:
                    decrypted = await _decrypt_privatebin_paste(session, curr_url)
                    if decrypted:
                        extracted_urls = re.findall(r"https?://[^\s\"\'<>]+", decrypted)
                        for u in extracted_urls:
                            if u in seen_resource_urls:
                                continue
                            seen_resource_urls.add(u)
                            fn, ext, cat = self._extract_file_info(u, source_tag="paste")
                            if fn:
                                all_resources.append(
                                    GrabbedResource(
                                        url=u,
                                        filename=fn,
                                        category=cat,
                                        source_tag="paste",
                                    )
                                )
                        continue

                # Normal web page loading
                try:
                    async with session.get(curr_url, allow_redirects=True) as resp:
                        if resp.status >= 400:
                            continue
                        content_type = resp.headers.get("Content-Type", "").lower()
                        if "text/html" not in content_type and "application/xhtml" not in content_type:
                            continue
                        html_content = await resp.text(errors="ignore")
                        final_url = str(resp.url)
                except Exception as exc:
                    logger.debug("SiteGrabber crawl failed to load %s: %s", curr_url, exc)
                    continue

                # 1. Parse HTML
                parser = _HTMLResourceExtractor()
                try:
                    parser.feed(html_content)
                except Exception:
                    pass

                # 2. Regex search for direct URLs in scripts/attributes
                regex_urls = re.findall(r'https?://[^\s"\'<>]+', html_content)
                for u in regex_urls:
                    parser.raw_items.append((u, "regex"))

                # 3. Categorize resources and discover links for deeper crawl
                for raw_link, tag in parser.raw_items:
                    try:
                        abs_url = urllib.parse.urljoin(final_url, raw_link)
                    except Exception:
                        continue

                    lower_abs = abs_url.lower()
                    if not (lower_abs.startswith("http://") or lower_abs.startswith("https://")):
                        continue

                    p = urllib.parse.urlparse(abs_url)

                    # Check if this link is a valid downloadable file resource
                    fn, ext, cat = self._extract_file_info(abs_url, source_tag=tag)
                    if fn:
                        if abs_url not in seen_resource_urls:
                            seen_resource_urls.add(abs_url)
                            all_resources.append(
                                GrabbedResource(
                                    url=abs_url,
                                    filename=fn,
                                    category=cat,
                                    source_tag=tag,
                                )
                            )

                    # If not reached depth limit and this is an HTML navigation link, enqueue subpage
                    if tag == "link" and curr_depth < depth and len(seen_to_visit) < max_pages * 2:
                        link_host = p.netloc.lower()
                        is_allowed_crawl_host = (
                            link_host == base_host
                            or link_host.endswith("." + base_host)
                            or "paste" in link_host
                            or "mirror" in link_host
                            or "forum" in link_host
                        )
                        if is_allowed_crawl_host:
                            # If it's a paste link with fragment, enqueue the full URL including fragment
                            if "paste" in link_host and p.fragment:
                                if abs_url not in visited_pages and abs_url not in seen_to_visit:
                                    seen_to_visit.add(abs_url)
                                    to_visit.append((abs_url, curr_depth + 1))
                            else:
                                path_ext = posixpath.splitext(p.path)[1].lower()
                                if path_ext in ("", ".html", ".htm", ".php", ".asp", ".aspx", ".jsp"):
                                    # Strip fragment for generic HTML pages
                                    clean_page_url = urllib.parse.urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))
                                    if clean_page_url not in visited_pages and clean_page_url not in seen_to_visit:
                                        seen_to_visit.add(clean_page_url)
                                        to_visit.append((clean_page_url, curr_depth + 1))

        logger.info(
            "SiteGrabber crawl completed: %d resources discovered across %d pages (Depth: %d)",
            len(all_resources),
            len(visited_pages),
            depth,
        )
        return all_resources
