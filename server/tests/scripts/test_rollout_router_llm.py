"""Unit tests for ``scripts/rollout_router_llm`` — the RouterLLM rollout CLI.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 24.2.

The script is a one-shot HTTP client hitting the runtime flags admin
API. These tests stub the HTTP layer with :class:`httpx.MockTransport`
and assert:

* The request is a ``PUT`` to
  ``/api/v1/runtime-flags/router_llm_enabled``.
* The JSON body matches the canonical rollout payload
  ``{"enabled": true, "rollout_percent": 100}``.
* The ``Authorization: Bearer ...`` header is included when
  ``AIOPSOS_ADMIN_TOKEN`` is set and omitted otherwise.
* ``--rollout`` / ``--disable`` change the body and the summary line.
* Non-2xx responses bubble up as a non-zero exit code.
* ``--dry-run`` never contacts the transport.

No network, no server, no DB.
"""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
import pytest

from scripts import rollout_router_llm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


class _RequestSpy:
    """Records every request seen by a handler for later assertions."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(
        self,
        status_code: int,
        body: Any,
        *,
        content_type: str = "application/json",
    ) -> Callable[[httpx.Request], httpx.Response]:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            if isinstance(body, (dict, list)):
                return httpx.Response(
                    status_code,
                    content=json.dumps(body).encode("utf-8"),
                    headers={"Content-Type": content_type},
                )
            return httpx.Response(
                status_code,
                content=str(body).encode("utf-8"),
                headers={"Content-Type": content_type},
            )

        return handler


def _run_cli(
    argv: list[str],
    handler: Callable[[httpx.Request], httpx.Response],
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str | None = None,
    base_url: str | None = None,
    api_prefix: str | None = None,
) -> int:
    """Drive :func:`scripts.rollout_router_llm.main` with a mock transport."""
    monkeypatch.delenv("AIOPSOS_CONTROL_URL", raising=False)
    monkeypatch.delenv("AIOPSOS_CONTROL_API_PREFIX", raising=False)
    if token is None:
        monkeypatch.delenv("AIOPSOS_ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AIOPSOS_ADMIN_TOKEN", token)
    if base_url is not None:
        monkeypatch.setenv("AIOPSOS_CONTROL_URL", base_url)
    if api_prefix is not None:
        monkeypatch.setenv("AIOPSOS_CONTROL_API_PREFIX", api_prefix)

    return rollout_router_llm.main(argv, transport=_make_transport(handler))


# ---------------------------------------------------------------------------
# Pure unit: body builder
# ---------------------------------------------------------------------------


def test_build_rollout_body_defaults_to_enabled_100() -> None:
    body = rollout_router_llm.build_rollout_body(100)
    assert body == {"enabled": True, "rollout_percent": 100}


def test_build_rollout_body_rejects_negative() -> None:
    with pytest.raises(ValueError):
        rollout_router_llm.build_rollout_body(-1)


def test_build_rollout_body_rejects_above_100() -> None:
    with pytest.raises(ValueError):
        rollout_router_llm.build_rollout_body(101)


def test_build_rollout_body_honours_disable() -> None:
    body = rollout_router_llm.build_rollout_body(0, enabled=False)
    assert body == {"enabled": False, "rollout_percent": 0}


# ---------------------------------------------------------------------------
# Happy path: bump to 100%
# ---------------------------------------------------------------------------


def test_rollout_puts_expected_path_body_and_auth_header(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    # The server returns a RuntimeFlagOut-shaped body; the CLI echoes it.
    server_body = {
        "key": "router_llm_enabled",
        "enabled": True,
        "rollout_percent": 100,
        "data": {"description": "Enable RouterLLM pre-classification ..."},
        "updated_at": "2026-05-12T12:00:00+00:00",
    }

    exit_code = _run_cli(
        [],  # no args → bump to 100, enabled
        spy(200, server_body),
        monkeypatch,
        token="admin-tkn",
    )
    assert exit_code == 0

    assert len(spy.requests) == 1
    req = spy.requests[0]
    assert req.method == "PUT"

    parts = urlsplit(str(req.url))
    assert parts.path == "/api/v1/runtime-flags/router_llm_enabled"
    # Query string must be empty — rollout is a body-only operation.
    assert parts.query == ""

    # Assert exact body per task 24.2 acceptance criterion.
    body = json.loads(req.content.decode("utf-8"))
    assert body == {"rollout_percent": 100, "enabled": True}

    # Auth header only when token is set.
    assert req.headers["Authorization"] == "Bearer admin-tkn"
    assert req.headers["Content-Type"] == "application/json"

    stdout = capsys.readouterr().out
    assert "router_llm_enabled: FULL rollout (100%)" in stdout
    # The CLI echoes the server response too.
    assert "router_llm_enabled" in stdout


def test_rollout_omits_auth_when_no_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RequestSpy()
    exit_code = _run_cli(
        [],
        spy(200, {"key": "router_llm_enabled"}),
        monkeypatch,
        # No token at all.
    )
    assert exit_code == 0
    assert len(spy.requests) == 1
    assert "Authorization" not in spy.requests[0].headers


# ---------------------------------------------------------------------------
# Partial rollout / disable paths (used for rollback per the runbook)
# ---------------------------------------------------------------------------


def test_partial_rollout_sends_configured_percent(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    exit_code = _run_cli(
        ["--rollout", "10"],
        spy(200, {}),
        monkeypatch,
    )
    assert exit_code == 0
    body = json.loads(spy.requests[0].content.decode("utf-8"))
    assert body == {"rollout_percent": 10, "enabled": True}
    assert "partial rollout 10%" in capsys.readouterr().out


def test_disable_flag_sends_enabled_false(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    exit_code = _run_cli(
        ["--rollout", "0", "--disable"],
        spy(200, {}),
        monkeypatch,
    )
    assert exit_code == 0
    body = json.loads(spy.requests[0].content.decode("utf-8"))
    assert body == {"rollout_percent": 0, "enabled": False}
    assert "DISABLED" in capsys.readouterr().out


def test_invalid_rollout_percent_exits_with_usage_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    # argparse-level error exits via SystemExit(2) before any request.
    with pytest.raises(SystemExit) as excinfo:
        _run_cli(
            ["--rollout", "150"],
            spy(200, {}),
            monkeypatch,
        )
    assert excinfo.value.code == 2
    assert len(spy.requests) == 0


# ---------------------------------------------------------------------------
# Dry-run never hits the transport
# ---------------------------------------------------------------------------


def test_dry_run_prints_plan_without_request(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    exit_code = _run_cli(
        ["--dry-run"],
        spy(200, {}),
        monkeypatch,
        token="admin-tkn",
    )
    assert exit_code == 0
    assert spy.requests == []  # no network traffic

    stdout = capsys.readouterr().out
    assert "[dry-run]" in stdout
    assert "PUT" in stdout
    assert "/api/v1/runtime-flags/router_llm_enabled" in stdout
    # The body printed under dry-run is identical to the live body.
    assert '"rollout_percent": 100' in stdout
    assert '"enabled": true' in stdout


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_non_2xx_response_exits_nonzero_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spy = _RequestSpy()
    exit_code = _run_cli(
        [],
        spy(403, {"detail": "admin token required"}),
        monkeypatch,
    )
    assert exit_code == 1
    assert any(
        "HTTP 403" in rec.message and "router_llm_enabled" in rec.message
        for rec in caplog.records
    )


def test_env_base_url_and_token_are_honoured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must read AIOPSOS_CONTROL_URL + AIOPSOS_ADMIN_TOKEN from env."""
    spy = _RequestSpy()
    exit_code = _run_cli(
        [],
        spy(200, {}),
        monkeypatch,
        base_url="http://ctl.example.com:9001",
        token="env-tkn",
    )
    assert exit_code == 0
    req = spy.requests[0]
    parts = urlsplit(str(req.url))
    assert parts.scheme == "http"
    assert parts.netloc == "ctl.example.com:9001"
    assert parts.path == "/api/v1/runtime-flags/router_llm_enabled"
    assert req.headers["Authorization"] == "Bearer env-tkn"


def test_explicit_flags_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RequestSpy()
    monkeypatch.setenv("AIOPSOS_CONTROL_URL", "http://ignored.example:1")
    monkeypatch.setenv("AIOPSOS_ADMIN_TOKEN", "env-tkn")

    exit_code = rollout_router_llm.main(
        [
            "--base-url", "http://override:8002/",
            "--token", "flag-tkn",
        ],
        transport=_make_transport(_RequestSpy()(200, {})),
    )
    # Second transport — use a fresh spy to capture.
    spy = _RequestSpy()
    exit_code = rollout_router_llm.main(
        [
            "--base-url", "http://override:8002/",
            "--token", "flag-tkn",
        ],
        transport=_make_transport(spy(200, {})),
    )
    assert exit_code == 0
    req = spy.requests[0]
    parts = urlsplit(str(req.url))
    assert parts.netloc == "override:8002"
    assert req.headers["Authorization"] == "Bearer flag-tkn"


def test_help_smoke() -> None:
    """``--help`` exits cleanly without contacting the network."""
    parser = rollout_router_llm._build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--help"])
    assert excinfo.value.code == 0
