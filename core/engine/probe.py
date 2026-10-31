import asyncio
import os
import re
import urllib.parse
from dataclasses import dataclass
from typing import Optional
import aiohttp
import logging

logger = logging.getLogger(__name__)


def get_browser_headers(
    url: Optional[str] = None,
    referer: Optional[str] = None,
    cookies: Optional[str] = None,
    user_agent: Optional[str] = None,
    custom_headers: Optional[dict] = None,
) -> dict:
    """
    Constructs a complete set of standard browser headers including Anti-Hotlinking Referer,
    session Cookies, User-Agent, and Sec-Ch-Ua / Sec-Fetch client hints to bypass common CDN protections.
    Respects custom_headers and prevents sending mismatched Chromium client hints on mobile/VisionOS UAs
    or token-gated googlevideo CDN streams.
    """
    effective_ua = user_agent
    effective_referer = referer
    effective_cookies = cookies

    if custom_headers:
        for k, v in custom_headers.items():
            lk = k.lower()
            if lk == "user-agent" and v and not effective_ua:
                effective_ua = v
            elif lk == "referer" and v and not effective_referer:
                effective_referer = v
            elif lk == "cookie" and v and not effective_cookies:
                effective_cookies = v

    if not effective_ua:
        effective_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"

    headers = {
        "User-Agent": effective_ua,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }

    is_googlevideo = bool(url and "googlevideo.com" in url.lower())
    is_chromium_desktop = "Chrome" in effective_ua and "Windows" in effective_ua and not is_googlevideo

    if is_chromium_desktop:
        headers.update({
            "Sec-Ch-Ua": '"Chromium";v="125", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
        })

    if effective_referer:
        if "twimg.com" in effective_referer.lower():
            headers["Referer"] = "https://x.com/"
        elif "googlevideo.com" in effective_referer.lower():
            headers["Referer"] = "https://www.youtube.com/"
        else:
            headers["Referer"] = effective_referer
    elif url:
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc and "twimg.com" in parsed.netloc.lower():
            headers["Referer"] = "https://x.com/"
        elif is_googlevideo:
            headers["Referer"] = "https://www.youtube.com/"
        elif parsed.scheme and parsed.netloc:
            # Dynamic Referer derived from URL root (e.g. 'https://testfile.org/')
            headers["Referer"] = f"{parsed.scheme}://{parsed.netloc}/"

    if effective_cookies:
        headers["Cookie"] = effective_cookies

    if custom_headers:
        for ck, cv in custom_headers.items():
            for hk in list(headers.keys()):
                if hk.lower() == ck.lower():
                    del headers[hk]
            headers[ck] = cv

    return headers


DEFAULT_HTTP_HEADERS = get_browser_headers()


class ProbeError(aiohttp.ClientError):
    """Raised when probing a remote resource fails due to connection or HTTP errors."""
    pass


@dataclass
class RemoteResourceInfo:
    url: str
    total_size: int
    supports_ranges: bool
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    filename: str = "downloaded_file"
    content_type: Optional[str] = None


class ResourceProbe:
    """Probes remote HTTP/HTTPS resources to detect size, range support, and metadata."""

    @classmethod
    async def probe(
        cls,
        session: aiohttp.ClientSession,
        url: str,
        referer: Optional[str] = None,
        cookies: Optional[str] = None,
        user_agent: Optional[str] = None,
        custom_headers: Optional[dict] = None,
    ) -> RemoteResourceInfo:
        """
        Executes HEAD or fallback GET Range: bytes=0-0 to discover file metadata.
        Uses realistic Chrome User-Agent, Sec-Ch-Ua headers, and dynamic/custom Referer and Cookies.
        Gracefully falls back to GET Range if HEAD is rejected (e.g. 403 Forbidden or 405).
        Strictly propagates connection and timeout errors to prevent 0-byte fallbacks.
        """
        resolved_url = url
        etag = None
        last_modified = None
        content_type = None
        filename = cls._extract_filename_from_url(url)
        supports_ranges = False
        total_size = 0
        head_failed = False
        head_error: Optional[Exception] = None

        # 1. Try HEAD request first with realistic browser headers, dynamic/custom Referer and Cookies
        head_headers = get_browser_headers(
            url,
            referer=referer,
            cookies=cookies,
            user_agent=user_agent,
            custom_headers=custom_headers,
        )
        try:
            async with session.head(url, headers=head_headers, allow_redirects=True) as resp:
                resolved_url = str(resp.url)
                if resp.status < 400:
                    etag = resp.headers.get("ETag")
                    last_modified = resp.headers.get("Last-Modified")
                    content_type = resp.headers.get("Content-Type")

                    # Extract Content-Length
                    cl = resp.headers.get("Content-Length")
                    if cl and cl.isdigit():
                        total_size = int(cl)

                    # Check Accept-Ranges
                    ar = resp.headers.get("Accept-Ranges", "").lower()
                    if "bytes" in ar:
                        supports_ranges = True

                    # Extract filename from Content-Disposition
                    cd = resp.headers.get("Content-Disposition")
                    if cd:
                        parsed_name = cls._parse_content_disposition(cd)
                        if parsed_name:
                            filename = parsed_name
                    elif resolved_url != url:
                        alt_name = cls._extract_filename_from_url(resolved_url)
                        if alt_name and alt_name != "downloaded_file":
                            filename = alt_name
                else:
                    head_failed = True
                    logger.debug(
                        "HEAD request rejected with HTTP %d for %s. Gracefully attempting GET Range: bytes=0-0 fallback.",
                        resp.status,
                        url,
                    )
        except (
            aiohttp.ClientConnectorError,
            aiohttp.ClientConnectionError,
            aiohttp.ClientPayloadError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
            ConnectionError,
            OSError,
        ) as exc:
            head_failed = True
            head_error = exc
            logger.debug(
                "HEAD request connection error for %s: %s. Gracefully attempting GET Range: bytes=0-0 fallback.",
                url,
                exc,
            )
        except Exception as exc:
            head_failed = True
            head_error = exc
            logger.debug("HEAD request error for %s: %s. Gracefully attempting GET Range: bytes=0-0 fallback.", url, exc)

        # 2. If HEAD failed (403, 405, 501, etc.) or didn't confirm ranges/size, test with GET Range: bytes=0-0
        if head_failed or not supports_ranges or total_size <= 0:
            try:
                get_headers = get_browser_headers(
                    resolved_url,
                    referer=referer,
                    cookies=cookies,
                    user_agent=user_agent,
                    custom_headers=custom_headers,
                )
                get_headers["Range"] = "bytes=0-0"
                async with session.get(resolved_url, headers=get_headers, allow_redirects=True) as resp:
                    resolved_url = str(resp.url)
                    if resp.status == 206:
                        supports_ranges = True
                        content_range = resp.headers.get("Content-Range", "")
                        # e.g., "bytes 0-0/10485760"
                        match = re.search(r"/(\d+)$", content_range)
                        if match:
                            total_size = int(match.group(1))

                        if not etag:
                            etag = resp.headers.get("ETag")
                        if not last_modified:
                            last_modified = resp.headers.get("Last-Modified")

                        cd = resp.headers.get("Content-Disposition")
                        if cd:
                            parsed_name = cls._parse_content_disposition(cd)
                            if parsed_name:
                                filename = parsed_name
                        elif resolved_url != url:
                            alt_name = cls._extract_filename_from_url(resolved_url)
                            if alt_name and alt_name != "downloaded_file":
                                filename = alt_name
                    elif resp.status == 200:
                        # Server returned entire content: ranges NOT supported
                        supports_ranges = False
                        cl = resp.headers.get("Content-Length")
                        if cl and cl.isdigit():
                            total_size = int(cl)
                        if not etag:
                            etag = resp.headers.get("ETag")
                        if not last_modified:
                            last_modified = resp.headers.get("Last-Modified")
                        cd = resp.headers.get("Content-Disposition")
                        if cd:
                            parsed_name = cls._parse_content_disposition(cd)
                            if parsed_name:
                                filename = parsed_name
                        elif resolved_url != url:
                            alt_name = cls._extract_filename_from_url(resolved_url)
                            if alt_name and alt_name != "downloaded_file":
                                filename = alt_name
                    elif resp.status in (403, 405, 416):
                        logger.debug(
                            "GET Range request returned %d for %s. Attempting fallback GET without Range header.",
                            resp.status,
                            resolved_url,
                        )
                        plain_headers = get_browser_headers(
                            resolved_url,
                            referer=referer,
                            cookies=cookies,
                            user_agent=user_agent,
                            custom_headers=custom_headers,
                        )
                        plain_headers.pop("Range", None)
                        async with session.get(resolved_url, headers=plain_headers, allow_redirects=True) as plain_resp:
                            resolved_url = str(plain_resp.url)
                            if plain_resp.status == 200:
                                supports_ranges = False
                                cl = plain_resp.headers.get("Content-Length")
                                if cl and cl.isdigit():
                                    total_size = int(cl)
                                if not etag:
                                    etag = plain_resp.headers.get("ETag")
                                if not last_modified:
                                    last_modified = plain_resp.headers.get("Last-Modified")
                                cd = plain_resp.headers.get("Content-Disposition")
                                if cd:
                                    parsed_name = cls._parse_content_disposition(cd)
                                    if parsed_name:
                                        filename = parsed_name
                                elif resolved_url != url:
                                    alt_name = cls._extract_filename_from_url(resolved_url)
                                    if alt_name and alt_name != "downloaded_file":
                                        filename = alt_name
                            else:
                                error_msg = f"Probe failed: server returned HTTP {plain_resp.status} for {url}"
                                logger.error(error_msg)
                                raise ProbeError(error_msg)
                    else:
                        # Server returned HTTP error on GET Range as well
                        error_msg = f"Probe failed: server returned HTTP {resp.status} for {url}"
                        logger.error(error_msg)
                        raise ProbeError(error_msg)
            except (
                aiohttp.ClientConnectorError,
                aiohttp.ClientConnectionError,
                aiohttp.ClientPayloadError,
                aiohttp.ServerTimeoutError,
                asyncio.TimeoutError,
                ConnectionError,
                OSError,
            ) as exc:
                # Do NOT swallow connection/timeout errors
                logger.error("Network connection error probing %s: %s", url, exc)
                raise ProbeError(f"Network error probing {url}: {exc}") from exc
            except ProbeError:
                raise
            except Exception as exc:
                logger.error("Unexpected error probing %s: %s", url, exc)
                raise ProbeError(f"Failed to probe {url}: {exc}") from exc

        # If resource size is still 0 and ranges not confirmed, check if connection failed
        if total_size <= 0 and not supports_ranges and head_failed:
            if head_error:
                raise ProbeError(f"Failed to reach host for {url}: {head_error}") from head_error
            raise ProbeError(f"Unable to discover resource metadata or size for {url}")

        logger.info(
            "Probed %s: size=%d bytes, ranges=%s, filename='%s'%s",
            url,
            total_size,
            supports_ranges,
            filename,
            f" (redirected to {resolved_url})" if resolved_url != url else "",
        )

        return RemoteResourceInfo(
            url=resolved_url,
            total_size=total_size,
            supports_ranges=supports_ranges,
            etag=etag,
            last_modified=last_modified,
            filename=filename,
            content_type=content_type,
        )

    @staticmethod
    def _parse_content_disposition(disposition: str) -> Optional[str]:
        """Parses filename from Content-Disposition header."""
        # e.g. attachment; filename="file.zip" or filename*=UTF-8''file%20name.zip
        fn_match = re.search(r"filename\*=UTF-8''([^;\r\n]+)", disposition, re.IGNORECASE)
        if fn_match:
            return urllib.parse.unquote(fn_match.group(1).strip("\"'"))

        fn_match = re.search(r'filename="?([^";\r\n]+)"?', disposition, re.IGNORECASE)
        if fn_match:
            return fn_match.group(1).strip("\"'")

        return None

    @staticmethod
    def _extract_filename_from_url(url: str) -> str:
        """Derives a fallback filename from the URL path, stripping query parameters and hash."""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path
        name = os.path.basename(path)
        if not name or name == "/":
            return "downloaded_file"
        if "?" in name:
            name = name.split("?")[0]
        if "#" in name:
            name = name.split("#")[0]
        if "&" in name:
            name = name.split("&")[0]
        name = urllib.parse.unquote(name)
        if not name or name == "/":
            return "downloaded_file"
        return name
