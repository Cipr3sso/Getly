"""
Site Explorer & Web Grabber Engine.
Analyzes any web page, extracts all downloadable files and multimedia assets,
resolves absolute URLs, deduplicates them, and classifies them into categories.
"""

import asyncio
from dataclasses import dataclass
from html.parser import HTMLParser
import logging
import os
import posixpath
import re
from typing import Dict, List, Optional, Set
import urllib.parse
import aiohttp

from core.scheduler.categories import detect_category, CATEGORY_OTHER

logger = logging.getLogger(__name__)

# Common web page code extensions to ignore
IGNORED_EXTENSIONS: Set[str] = {
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".rss",
    ".htm", ".html", ".php", ".asp", ".aspx", ".jsp",
}

# Tracking / ad domains to filter out
IGNORED_HOST_KEYWORDS: List[str] = [
    "google-analytics", "googletagmanager", "doubleclick",
    "facebook.net", "clarity.ms", "hotjar", "analytics",
]


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
    """

    @staticmethod
    def _is_valid_candidate(url: str) -> bool:
        """Determines if a candidate URL is a potential file download."""
        if not url:
            return False
        clean = url.strip()
        lower = clean.lower()

        # Reject protocols that are not web downloads
        if lower.startswith(("javascript:", "mailto:", "tel:", "data:", "#", "magnet:", "about:")):
            return False

        # Only accept http / https
        if not (lower.startswith("http://") or lower.startswith("https://")):
            return False

        # Check ignored domains
        for keyword in IGNORED_HOST_KEYWORDS:
            if keyword in lower:
                return False

        # Check path extension
        try:
            parsed = urllib.parse.urlparse(clean)
            path_ext = posixpath.splitext(parsed.path)[1].lower()
            if path_ext in IGNORED_EXTENSIONS:
                return False
        except Exception:
            pass

        return True

    @staticmethod
    def _synthesize_filename(url: str, category: str, fallback_index: int) -> str:
        """Derives a clean filename from the URL, or synthesizes one if path is vague."""
        try:
            parsed = urllib.parse.urlparse(url)
            query = urllib.parse.parse_qs(parsed.query)

            for key in ("filename", "file", "name", "fn", "download"):
                if key in query and query[key]:
                    cand = query[key][0]
                    if "." in cand:
                        return os.path.basename(cand.replace("\\", "/"))

            base = posixpath.basename(parsed.path)
            if base and "." in base:
                # Remove query artifacts if any
                clean_base = re.split(r"[?#]", base)[0]
                if clean_base:
                    return urllib.parse.unquote(clean_base)
        except Exception:
            pass

        # Fallback naming
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

        # 1. Parse HTML with _HTMLResourceExtractor
        parser = _HTMLResourceExtractor()
        try:
            parser.feed(html_content)
        except Exception as exc:
            logger.debug("HTML parser error (ignoring and proceeding): %s", exc)

        # 2. Extract additional URLs using regex (matches absolute https?:// URLs in script/json)
        regex_urls = re.findall(r'https?://[^\s"\'<>]+', html_content)
        for u in regex_urls:
            parser.raw_items.append((u, "regex"))

        # 3. Resolve relative to absolute and deduplicate
        seen_urls: Set[str] = set()
        results: List[GrabbedResource] = []
        counter = 1

        for raw_link, tag in parser.raw_items:
            try:
                abs_url = urllib.parse.urljoin(final_url, raw_link)
            except Exception:
                continue

            if not self._is_valid_candidate(abs_url):
                continue

            # Strip fragments
            parsed = urllib.parse.urlparse(abs_url)
            clean_abs = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, parsed.query, ""))

            if clean_abs in seen_urls:
                continue
            seen_urls.add(clean_abs)

            # Determine category and filename
            cat = detect_category(clean_abs)
            # If tag is image or video, promote category if Altro
            if cat == CATEGORY_OTHER:
                if tag in ("image", "img"):
                    cat = "Immagini_ISO"
                elif tag in ("video",):
                    cat = "Video"
                elif tag in ("audio",):
                    cat = "Audio"

            fn = self._synthesize_filename(clean_abs, cat, counter)
            counter += 1

            results.append(GrabbedResource(
                url=clean_abs,
                filename=fn,
                category=cat,
                source_tag=tag,
            ))

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
        downloadable files and media discovered across pages.
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
        counter = 1

        timeout = aiohttp.ClientTimeout(total=timeout_sec)
        connector = aiohttp.TCPConnector(ssl=False)

        async with aiohttp.ClientSession(timeout=timeout, headers=headers, connector=connector) as session:
            while to_visit and len(visited_pages) < max_pages:
                curr_url, curr_depth = to_visit.pop(0)
                if curr_url in visited_pages:
                    continue
                visited_pages.add(curr_url)

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

                # 2. Regex search for direct media URLs
                regex_urls = re.findall(r'https?://[^\s"\'<>]+', html_content)
                for u in regex_urls:
                    parser.raw_items.append((u, "regex"))

                # 3. Categorize resources and discover links for deeper crawl
                for raw_link, tag in parser.raw_items:
                    try:
                        abs_url = urllib.parse.urljoin(final_url, raw_link)
                    except Exception:
                        continue

                    # Clean fragments
                    p = urllib.parse.urlparse(abs_url)
                    clean_abs = urllib.parse.urlunparse((p.scheme, p.netloc, p.path, p.params, p.query, ""))
                    lower_abs = clean_abs.lower()

                    if not (lower_abs.startswith("http://") or lower_abs.startswith("https://")):
                        continue

                    path_ext = posixpath.splitext(p.path)[1].lower()

                    # Is this a candidate downloadable file?
                    if self._is_valid_candidate(clean_abs) and path_ext not in IGNORED_EXTENSIONS:
                        if clean_abs not in seen_resource_urls:
                            seen_resource_urls.add(clean_abs)
                            cat = detect_category(clean_abs)
                            if cat == CATEGORY_OTHER:
                                if tag in ("image", "img"):
                                    cat = "Immagini_ISO"
                                elif tag in ("video",):
                                    cat = "Video"
                                elif tag in ("audio",):
                                    cat = "Audio"

                            fn = self._synthesize_filename(clean_abs, cat, counter)
                            counter += 1
                            all_resources.append(
                                GrabbedResource(
                                    url=clean_abs,
                                    filename=fn,
                                    category=cat,
                                    source_tag=tag,
                                )
                            )

                    # If we haven't reached depth limit and this is a link (<a href>), enqueue subpage
                    if tag == "link" and curr_depth < depth and len(seen_to_visit) < max_pages * 2:
                        link_host = p.netloc.lower()
                        is_allowed_crawl_host = (
                            link_host == base_host
                            or link_host.endswith("." + base_host)
                            or "paste" in link_host
                            or "mirror" in link_host
                        )
                        if is_allowed_crawl_host:
                            if path_ext in ("", ".html", ".htm", ".php", ".asp", ".aspx", ".jsp"):
                                if clean_abs not in visited_pages and clean_abs not in seen_to_visit:
                                    seen_to_visit.add(clean_abs)
                                    to_visit.append((clean_abs, curr_depth + 1))

        logger.info(
            "SiteGrabber crawl completed: %d resources discovered across %d pages (Depth: %d)",
            len(all_resources),
            len(visited_pages),
            depth,
        )
        return all_resources
