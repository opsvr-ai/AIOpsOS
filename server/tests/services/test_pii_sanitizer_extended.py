"""Extended PII sanitiser tests.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 10.2 / R-8.1.

Covers the value-level detector+scrubber (email / IPv4 / IPv6 / PAT /
credit / CN phone) added in Phase E. The original key-based tests live
in ``tests/security/test_pii_sanitizer.py`` and stay untouched.
"""
from __future__ import annotations

import pytest

from src.services.pii import contains_pii, sanitize_pii, scrub_pii


# ---------------------------------------------------------------------------
# contains_pii
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("reach me at alice@example.com", ["email"]),
        ("primary is 10.0.1.2, backup 192.168.1.100", ["ip"]),
        ("route ::1 onto 2001:db8::1", ["ip"]),
        ("github token: ghp_abcdef0123456789ABCDEF0123456789AB", ["token"]),
        ("openai key: sk-proj-aBcD1234efGhIjKlMnOpQrStUvWxYz012345", ["token"]),
        ("aws: AKIAIOSFODNN7EXAMPLE", ["token"]),
        # Valid Luhn: 4111-1111-1111-1111 (Visa test card).
        ("card: 4111-1111-1111-1111", ["credit"]),
        ("phone: 13812345678", ["phone"]),
    ],
)
def test_contains_pii_detects_each_kind(text: str, expected: list[str]) -> None:
    found, kinds = contains_pii(text)
    assert found is True
    for k in expected:
        assert k in kinds


def test_contains_pii_composes_multiple_kinds() -> None:
    text = "email alice@example.com IP 10.0.0.1 phone 13812345678"
    found, kinds = contains_pii(text)
    assert found is True
    assert set(kinds) >= {"email", "ip", "phone"}


def test_contains_pii_returns_false_on_clean_text() -> None:
    found, kinds = contains_pii("一个普通的运维操作：查询主机列表，重启服务。")
    assert found is False
    assert kinds == []


def test_contains_pii_returns_false_for_short_digit_runs() -> None:
    # A bare "1234" isn't a card and definitely not a phone.
    found, _ = contains_pii("order #1234 processed in 0.3s")
    assert found is False


def test_contains_pii_short_circuits_large_inputs() -> None:
    # Far larger than the 10 KB scan budget — returns (False, []) to keep
    # the hot path bounded even if the string does contain PII.
    big = "alice@example.com " * 2000
    found, kinds = contains_pii(big)
    assert found is False
    assert kinds == []


def test_contains_pii_luhn_filters_non_card_digit_runs() -> None:
    # 16 digits but Luhn-invalid → NOT flagged as credit.
    found, kinds = contains_pii("invalid card 1234567812345678")
    assert "credit" not in kinds


def test_contains_pii_non_string_is_safe() -> None:
    assert contains_pii(None)[0] is False  # type: ignore[arg-type]
    assert contains_pii(42)[0] is False  # type: ignore[arg-type]
    assert contains_pii("")[0] is False


# ---------------------------------------------------------------------------
# scrub_pii
# ---------------------------------------------------------------------------


def test_scrub_pii_replaces_email() -> None:
    assert scrub_pii("contact alice@example.com now") == "contact [EMAIL] now"


def test_scrub_pii_replaces_ipv4_and_ipv6() -> None:
    assert scrub_pii("nodes 10.0.0.1 and 2001:db8::1") == "nodes [IP] and [IP]"


def test_scrub_pii_replaces_github_token() -> None:
    token = "ghp_" + "A" * 36
    out = scrub_pii(f"push with {token} to origin")
    assert out == "push with [TOKEN] to origin"


def test_scrub_pii_replaces_openai_key() -> None:
    key = "sk-proj-abcd1234efgh5678ijkl9012mnop"
    out = scrub_pii(f"OPENAI_API_KEY={key}")
    assert out == "OPENAI_API_KEY=[TOKEN]"


def test_scrub_pii_replaces_credit_card_only_when_luhn_valid() -> None:
    # Luhn-valid
    valid = "4111-1111-1111-1111"
    assert scrub_pii(f"card {valid} saved") == "card [CREDIT] saved"
    # Luhn-invalid: pass through unchanged
    invalid = "4111 1111 1111 1112"
    assert scrub_pii(f"card {invalid} saved") == f"card {invalid} saved"


def test_scrub_pii_replaces_cn_phone() -> None:
    assert scrub_pii("call 13812345678 for ops") == "call [PHONE] for ops"


def test_scrub_pii_leaves_clean_text_untouched() -> None:
    s = "Restart nginx on the canary host after deploy."
    assert scrub_pii(s) == s


def test_scrub_pii_preserves_surrounding_characters() -> None:
    s = "Host IP is 192.168.1.1."
    # Trailing period preserved.
    assert scrub_pii(s) == "Host IP is [IP]."


def test_scrub_pii_handles_mixed_payload() -> None:
    s = "Error in alice@example.com (10.0.0.1): token sk-abcdefghijklmnopqrstuvwx"
    out = scrub_pii(s)
    assert "alice" not in out
    assert "10.0.0.1" not in out
    assert "sk-abcdefghij" not in out
    assert "[EMAIL]" in out
    assert "[IP]" in out
    assert "[TOKEN]" in out


def test_scrub_pii_noop_on_non_string() -> None:
    assert scrub_pii(None) is None  # type: ignore[arg-type]
    assert scrub_pii(42) == 42  # type: ignore[arg-type]


def test_scrub_pii_short_circuits_large_inputs() -> None:
    big = "alice@example.com " * 2000  # > 10 KB
    # Unchanged — we don't pay regex cost on pathological inputs.
    assert scrub_pii(big) == big


# ---------------------------------------------------------------------------
# sanitize_pii with scrub_values=True
# ---------------------------------------------------------------------------


def test_sanitize_pii_scrub_values_hits_non_sensitive_keys() -> None:
    payload = {
        "content": "ping alice@example.com at 10.0.0.1",
        "meta": {"notes": "token ghp_" + "A" * 36},
        "api_key": "real-secret",  # still hashed via key-based layer
    }
    out = sanitize_pii(payload, scrub_values=True)

    assert "[EMAIL]" in out["content"]
    assert "[IP]" in out["content"]
    assert "[TOKEN]" in out["meta"]["notes"]
    # Sensitive key still hashed — composing, not replacing.
    assert out["api_key"].startswith("<sha256:")


def test_sanitize_pii_default_mode_unchanged() -> None:
    # Backwards-compatible: without scrub_values, only key-based scrubbing.
    payload = {"content": "my email is alice@example.com"}
    out = sanitize_pii(payload)
    assert out == payload  # unchanged


def test_sanitize_pii_walks_into_lists() -> None:
    payload = {"events": [{"msg": "send to bob@example.com"}, {"msg": "ok"}]}
    out = sanitize_pii(payload, scrub_values=True)
    assert out["events"][0]["msg"] == "send to [EMAIL]"
    assert out["events"][1]["msg"] == "ok"
