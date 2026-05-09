"""PII scrubber — key-based hashing + value-level regex scrubbing.

Spec: .kiro/specs/agent-runtime-optimization-evolution, tasks 6.2 + 10.2 /
R-8.1 / R-8.2.

Two interlocking layers:

1. **Key-based** (task 6.2):
   :func:`sanitize_pii` walks ``dict`` / ``list`` containers recursively and
   hashes/redacts values whose **keys** match :data:`_SENSITIVE_KEYS`.
   Used by TrajectorySink to scrub tool-call args before persisting.

2. **Value-based** (task 10.2 / R-8.1):
   :func:`contains_pii` + :func:`scrub_pii` detect and replace raw
   personally-identifiable strings regardless of surrounding key.
   Used by ConsolidationWorker to downgrade ``team`` memories that
   contain email / IPv4/IPv6 / PAT-like token / credit card / phone
   number matches to ``personal`` scope.

The two layers compose: :func:`sanitize_pii` with ``scrub_values=True``
first hashes sensitive keys and then runs :func:`scrub_pii` on any
remaining string values (≤ 10 KB — larger strings are left alone to
avoid pathological regex cost).
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# ---------------------------------------------------------------------------
# Key-based layer (Phase C)
# ---------------------------------------------------------------------------

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "api_key",
        "apikey",
        "password",
        "passwd",
        "token",
        "access_token",
        "refresh_token",
        "secret",
        "secret_key",
        "authorization",
    }
)


# Placeholder tokens produced by :func:`_hash_placeholder`. Values that
# already match are left unchanged so the sanitiser is idempotent — see
# tests/security/test_trajectory_redaction.py::
# test_sanitiser_is_idempotent_on_already_scrubbed_data.
_PLACEHOLDER_RE = re.compile(r"^<sha256:[0-9a-f]{6,}>$")
_REDACTED_LITERAL = "<redacted>"


def _is_placeholder(value: Any) -> bool:
    return isinstance(value, str) and (
        value == _REDACTED_LITERAL or _PLACEHOLDER_RE.match(value) is not None
    )


def _hash_placeholder(value: Any) -> str:
    """Return a deterministic, short, non-reversible placeholder.

    ``None`` or the empty string collapses to the literal ``<redacted>``
    sentinel so downstream consumers can distinguish "field was present
    but empty" from "field was set to an opaque secret".
    """
    if value is None:
        return "<redacted>"
    s = str(value)
    if not s:
        return "<redacted>"
    digest = hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]
    return f"<sha256:{digest}>"


def _is_sensitive(key: str) -> bool:
    return key.lower() in _SENSITIVE_KEYS


# ---------------------------------------------------------------------------
# Value-based layer (Phase E, task 10.2 / R-8.1)
# ---------------------------------------------------------------------------

# RFC-lite email; anchored to word boundaries so "user@host" in the middle
# of a paragraph still matches.
_EMAIL_RE = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24}\b"
)

# Dotted-quad IPv4 with octet range check.
_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}"
    r"(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
)

# IPv6 — cover full form, compressed form, and dual IPv4 tail.  Must
# include at least one ``:`` so we don't match plain integers.
_IPV6_RE = re.compile(
    r"(?<![A-Za-z0-9:])"
    r"(?:"
    r"(?:[A-Fa-f0-9]{1,4}:){7}[A-Fa-f0-9]{1,4}"  # full
    r"|(?:[A-Fa-f0-9]{1,4}:){1,7}:"              # trailing ::
    r"|(?:[A-Fa-f0-9]{1,4}:){1,6}:[A-Fa-f0-9]{1,4}"
    r"|::(?:[A-Fa-f0-9]{1,4}:){0,6}[A-Fa-f0-9]{1,4}"
    r"|::"  # the all-zero address
    r")"
    r"(?![A-Za-z0-9:])"
)

# Personal-access-token style secrets — GitHub (ghp_/gho_), OpenAI (sk-...),
# AWS access key (AKIA...), generic 32+ hex/base64-ish tokens.
_PAT_RE = re.compile(
    r"\b("
    r"gh[pousr]_[A-Za-z0-9]{20,}"
    r"|sk-[A-Za-z0-9_\-]{20,}"
    r"|AKIA[0-9A-Z]{12,20}"
    r"|xox[bapsr]-[A-Za-z0-9\-]{10,}"
    r"|[A-Fa-f0-9]{32,}"              # long hex (API secrets)
    r"|[A-Za-z0-9+/]{40,}={0,2}"      # base64 / long opaque
    r")\b"
)

# 13-19 consecutive digits, optionally separated by spaces/dashes.
_CARD_RE = re.compile(r"(?<!\d)(?:\d[ \-]?){12,18}\d(?!\d)")

# Chinese mobile: 11 digits starting with 1, second digit 3-9.
_CN_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")

# Max string length we scan for PII.  Anything longer is returned unchanged
# so we don't pay O(n) regex cost on huge JSON blobs.
_MAX_SCAN_BYTES = 10 * 1024  # 10 KB


def _luhn_ok(digits: str) -> bool:
    """Standard Luhn check for 13-19 digit credit-card strings."""
    s = 0
    alt = False
    for ch in reversed(digits):
        if not ch.isdigit():
            return False
        n = int(ch)
        if alt:
            n *= 2
            if n > 9:
                n -= 9
        s += n
        alt = not alt
    return s % 10 == 0 and 13 <= len(digits) <= 19


def _strip_card_sep(raw: str) -> str:
    return "".join(ch for ch in raw if ch.isdigit())


def contains_pii(text: str) -> tuple[bool, list[str]]:
    """Return ``(found, kinds)`` — whether *text* contains any PII.

    ``kinds`` is a deterministic, deduped list of the categories present
    (e.g. ``["email", "ip"]``). Useful for audit logging without leaking
    the matched secret itself.

    Only strings up to :data:`_MAX_SCAN_BYTES` are scanned; longer
    strings return ``(False, [])`` to keep the hot path bounded.
    """
    if not isinstance(text, str) or not text:
        return False, []
    if len(text) > _MAX_SCAN_BYTES:
        return False, []

    found: list[str] = []
    if _EMAIL_RE.search(text):
        found.append("email")
    if _IPV4_RE.search(text):
        found.append("ip")
    elif _IPV6_RE.search(text):
        found.append("ip")
    if _PAT_RE.search(text):
        found.append("token")
    # Credit card: verify Luhn on the stripped digits to cut false positives.
    for m in _CARD_RE.finditer(text):
        digits = _strip_card_sep(m.group(0))
        if _luhn_ok(digits):
            found.append("credit")
            break
    if _CN_PHONE_RE.search(text):
        found.append("phone")

    # Dedupe while preserving order.
    seen: set[str] = set()
    deduped = [k for k in found if not (k in seen or seen.add(k))]
    return bool(deduped), deduped


def scrub_pii(text: str) -> str:
    """Return *text* with every PII match replaced by a category placeholder.

    Short-circuits identical to :func:`contains_pii`:

    * non-string / empty → returned as-is
    * larger than :data:`_MAX_SCAN_BYTES` → returned unchanged

    Replacement order matters — tokens are checked before the generic
    card/phone regexes so a 40-char hex string never gets reinterpreted
    as a "credit card" because Luhn happens to match.
    """
    if not isinstance(text, str) or not text:
        return text
    if len(text) > _MAX_SCAN_BYTES:
        return text

    out = _EMAIL_RE.sub("[EMAIL]", text)
    out = _PAT_RE.sub("[TOKEN]", out)
    out = _IPV4_RE.sub("[IP]", out)
    out = _IPV6_RE.sub("[IP]", out)

    def _card_sub(m: re.Match[str]) -> str:
        digits = _strip_card_sep(m.group(0))
        return "[CREDIT]" if _luhn_ok(digits) else m.group(0)

    out = _CARD_RE.sub(_card_sub, out)
    out = _CN_PHONE_RE.sub("[PHONE]", out)
    return out


# ---------------------------------------------------------------------------
# Combined sanitiser
# ---------------------------------------------------------------------------


def sanitize_pii(payload: Any, *, scrub_values: bool = False) -> Any:
    """Return a new payload with sensitive values replaced.

    ``scrub_values=False`` (default, used by TrajectorySink) — only
    hashes values under sensitive keys. Backwards-compatible with the
    Phase C contract.

    ``scrub_values=True`` — additionally runs :func:`scrub_pii` on any
    string value whose key is NOT sensitive, so email / IP / token
    fragments hiding inside innocuous keys (e.g. ``"content"``) still
    get redacted. Lists / dicts are walked recursively; scalars other
    than strings pass through.
    """
    if isinstance(payload, dict):
        out: dict[str, Any] = {}
        for k, v in payload.items():
            if isinstance(k, str) and _is_sensitive(k):
                if _is_placeholder(v):
                    # Already scrubbed — keep as-is (idempotency).
                    out[k] = v
                elif isinstance(v, (dict, list)):
                    out[k] = _hash_placeholder(v if v else None)
                else:
                    out[k] = _hash_placeholder(v)
            else:
                out[k] = sanitize_pii(v, scrub_values=scrub_values)
        return out
    if isinstance(payload, list):
        return [sanitize_pii(x, scrub_values=scrub_values) for x in payload]
    if scrub_values and isinstance(payload, str):
        return scrub_pii(payload)
    return payload


__all__ = [
    "sanitize_pii",
    "contains_pii",
    "scrub_pii",
]
