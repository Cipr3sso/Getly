"""
Batch URL Pattern Generator for Getly.
Supports numeric intervals [01-15], [1-50:2] with zero-padding preservation,
alphabetic intervals [a-z], [A-Z], and multiline pattern expansion.
"""

import re
from typing import List


NUMERIC_RANGE_REGEX = re.compile(r"\[(\d+)-(\d+)(?::(\d+))?\]")
ALPHA_RANGE_REGEX = re.compile(r"\[([a-zA-Z])-([a-zA-Z])(?::(\d+))?\]")


def generate_batch_urls(pattern_or_text: str, max_limit: int = 1000) -> List[str]:
    """
    Expands pattern strings containing numeric or alphabetic intervals into a list of URLs.
    Examples:
        - "https://example.com/archive_[01-05].zip" -> archive_01.zip ... archive_05.zip
        - "https://cdn.site/data_[1-10:2].tar.gz" -> data_1.tar.gz, data_3.tar.gz ...
        - "https://example.com/part_[a-e].mp4" -> part_a.mp4 ... part_e.mp4
    Also expands multiple lines or whitespace-separated pattern lines.
    """
    if not pattern_or_text:
        return []

    lines = [ln.strip() for ln in pattern_or_text.splitlines() if ln.strip()]
    results: List[str] = []

    for line in lines:
        expanded_lines = _expand_single_pattern(line, max_limit - len(results))
        results.extend(expanded_lines)
        if len(results) >= max_limit:
            break

    return results[:max_limit]


def _expand_single_pattern(line: str, remaining_quota: int) -> List[str]:
    if remaining_quota <= 0:
        return []

    # 1. Check for numeric range [01-15] or [1-50:2]
    num_match = NUMERIC_RANGE_REGEX.search(line)
    if num_match:
        start_str, end_str, step_str = num_match.groups()
        start = int(start_str)
        end = int(end_str)
        step = int(step_str) if step_str else 1
        if step <= 0:
            step = 1

        # Preserve leading zero padding if specified
        use_padding = (start_str.startswith("0") and len(start_str) > 1) or (end_str.startswith("0") and len(end_str) > 1)
        pad_len = max(len(start_str), len(end_str)) if use_padding else 0

        urls = []
        if start <= end:
            curr = start
            while curr <= end:
                val_str = f"{curr:0{pad_len}d}" if pad_len > 0 else str(curr)
                url = line[:num_match.start()] + val_str + line[num_match.end():]
                urls.append(url)
                curr += step
                if len(urls) >= remaining_quota:
                    break
        else:
            curr = start
            while curr >= end:
                val_str = f"{curr:0{pad_len}d}" if pad_len > 0 else str(curr)
                url = line[:num_match.start()] + val_str + line[num_match.end():]
                urls.append(url)
                curr -= step
                if len(urls) >= remaining_quota:
                    break
        return urls

    # 2. Check for alphabetic range [a-z] or [A-Z]
    alpha_match = ALPHA_RANGE_REGEX.search(line)
    if alpha_match:
        start_char, end_char, step_str = alpha_match.groups()
        step = int(step_str) if step_str else 1
        if step <= 0:
            step = 1

        start_ord = ord(start_char)
        end_ord = ord(end_char)

        urls = []
        if start_ord <= end_ord:
            curr = start_ord
            while curr <= end_ord:
                url = line[:alpha_match.start()] + chr(curr) + line[alpha_match.end():]
                urls.append(url)
                curr += step
                if len(urls) >= remaining_quota:
                    break
        else:
            curr = start_ord
            while curr >= end_ord:
                url = line[:alpha_match.start()] + chr(curr) + line[alpha_match.end():]
                urls.append(url)
                curr -= step
                if len(urls) >= remaining_quota:
                    break
        return urls

    # No pattern found, return the plain URL
    return [line]
