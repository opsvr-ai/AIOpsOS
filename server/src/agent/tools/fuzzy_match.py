"""Fuzzy text matching for skill self-improvement.

Provides whitespace-tolerant, indentation-flexible find-and-replace
so the agent can patch SKILL.md files even when it generates slightly
different formatting.
"""

from __future__ import annotations

from difflib import SequenceMatcher


def _normalize(text: str) -> str:
    """Strip leading/trailing whitespace from each line."""
    return "\n".join(line.strip() for line in text.splitlines())


def fuzzy_find_blocks(
    text: str, pattern: str, threshold: float = 0.7
) -> list[tuple[int, int, float]]:
    """Find fuzzy matches of a pattern within a text.

    Splits the text into paragraphs (double-newline delimited) and compares
    each against the pattern using SequenceMatcher. Normalizes leading/
    trailing whitespace per line before comparison.

    Returns list of (start_index, end_index, confidence) tuples sorted by
    confidence descending.
    """
    if not pattern or not text:
        return []

    normalized_pattern = _normalize(pattern)

    # Try exact match first
    exact = text.find(pattern)
    if exact >= 0:
        return [(exact, exact + len(pattern), 1.0)]

    # Try normalized exact match
    normalized_text = _normalize(text)
    norm_exact = normalized_text.find(normalized_pattern)
    if norm_exact >= 0:
        return [(norm_exact, norm_exact + len(normalized_pattern), 0.98)]

    # Sliding window over paragraphs
    paragraphs = text.split("\n\n")
    results: list[tuple[int, int, float]] = []
    offset = 0

    for para in paragraphs:
        para_start = offset
        norm_para = _normalize(para)
        if not norm_para.strip():
            offset += len(para) + 2
            continue

        ratio = SequenceMatcher(None, norm_para, normalized_pattern).ratio()
        if ratio >= threshold:
            results.append((para_start, para_start + len(para), ratio))
        offset += len(para) + 2

    # Also try sliding line windows
    lines = text.splitlines()
    chunk_size = max(len(pattern.splitlines()), 1)

    for i in range(len(lines) - chunk_size + 1):
        chunk = "\n".join(lines[i : i + chunk_size])
        norm_chunk = _normalize(chunk)
        ratio = SequenceMatcher(None, norm_chunk, normalized_pattern).ratio()
        if ratio >= threshold:
            # Find byte offset of this chunk
            idx = 0
            for j in range(i):
                idx = text.find("\n", idx) + 1
            results.append((idx, idx + len(chunk), ratio))

    # Deduplicate by start index, keep highest confidence
    deduped: dict[int, tuple[int, int, float]] = {}
    for start, end, conf in results:
        if start not in deduped or conf > deduped[start][2]:
            deduped[start] = (start, end, conf)

    results = list(deduped.values())
    results.sort(key=lambda x: x[2], reverse=True)
    return results


def fuzzy_find_and_replace(
    text: str, old: str, new: str, threshold: float = 0.7, replace_all: bool = False
) -> tuple[str, int, float]:
    """Find and replace text using fuzzy matching.

    Returns (result_text, replacement_count, confidence).
    Raises ValueError if no match found or multiple close matches
    without replace_all.
    """
    matches = fuzzy_find_blocks(text, old, threshold)
    if not matches:
        raise ValueError(
            f"No match found for pattern (threshold={threshold}). "
            "Try a more exact match."
        )

    if len(matches) > 1 and not replace_all:
        best = matches[0]
        second = matches[1]
        if second[2] > best[2] * 0.95:
            raise ValueError(
                f"Multiple close matches found ({len(matches)}). "
                "Use a more specific pattern or set replace_all=True."
            )

    # Process in reverse order to preserve indices
    if replace_all:
        count = 0
        result = text
        for start, end, conf in sorted(matches, key=lambda x: x[0], reverse=True):
            if conf >= threshold:
                result = result[:start] + new + result[end:]
                count += 1
        best_conf = max(m[2] for m in matches if m[2] >= threshold)
        return result, count, best_conf

    best = matches[0]
    result = text[: best[0]] + new + text[best[1] :]
    return result, 1, best[2]
