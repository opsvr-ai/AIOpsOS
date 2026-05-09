"""Unit tests for ``scripts/evo_ctl`` — the evolution control-plane CLI.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 23.5.

The CLI is a thin wrapper over the control-plane evolution API, so
these tests stub the HTTP layer with :class:`httpx.MockTransport` and
assert:

* Correct HTTP method + URL for each subcommand.
* Headers include the ``Authorization: Bearer ...`` header when
  ``AIOPSOS_ADMIN_TOKEN`` is set, and omit it when unset.
* Response bodies are pretty-printed as JSON for read subcommands and
  summary lines for write subcommands.
* Non-2xx responses bubble up as a non-zero exit code with a readable
  stderr / log line.

No network, no server, no DB.
"""
from __future__ import annotations

import json
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from scripts import evo_ctl


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_transport(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def _run_cli(
    argv: list[str],
    handler: Callable[[httpx.Request], httpx.Response],
    monkeypatch: pytest.MonkeyPatch,
    *,
    token: str | None = None,
) -> int:
    """Drive :func:`scripts.evo_ctl.main` with a mock transport."""
    # Make the environment deterministic for each test.
    monkeypatch.delenv("AIOPSOS_CONTROL_URL", raising=False)
    monkeypatch.delenv("AIOPSOS_CONTROL_API_PREFIX", raising=False)
    if token is None:
        monkeypatch.delenv("AIOPSOS_ADMIN_TOKEN", raising=False)
    else:
        monkeypatch.setenv("AIOPSOS_ADMIN_TOKEN", token)

    return evo_ctl.main(argv, transport=_make_transport(handler))


class _RequestSpy:
    """Records every request seen by a handler for later assertions."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(
        self, status_code: int, body: Any, *, content_type: str = "application/json"
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


# ---------------------------------------------------------------------------
# Build-URL unit
# ---------------------------------------------------------------------------


def test_build_url_joins_prefix_and_path_without_duplicate_slashes() -> None:
    url = evo_ctl._build_url(
        "http://localhost:8001", "/api/v1", "/sub-agents/monitor/prompt-versions"
    )
    assert url == "http://localhost:8001/api/v1/sub-agents/monitor/prompt-versions"


def test_build_url_adds_leading_slash_to_path() -> None:
    url = evo_ctl._build_url(
        "http://h:1", "/api/v1", "sub-agents/x/rollback",
    )
    assert url == "http://h:1/api/v1/sub-agents/x/rollback"


def test_build_headers_omits_auth_when_token_is_missing() -> None:
    headers = evo_ctl._build_headers(None)
    assert "Authorization" not in headers
    assert headers["Accept"] == "application/json"


def test_build_headers_includes_bearer_when_token_is_set() -> None:
    headers = evo_ctl._build_headers("abc.def.ghi")
    assert headers["Authorization"] == "Bearer abc.def.ghi"


# ---------------------------------------------------------------------------
# list-versions
# ---------------------------------------------------------------------------


def test_list_versions_get_expected_path_and_prints_json(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    body = [
        {"id": "11111111-1111-1111-1111-111111111111", "version_no": 1, "status": "active"},
        {"id": "22222222-2222-2222-2222-222222222222", "version_no": 2, "status": "shadow"},
    ]

    exit_code = _run_cli(
        ["list-versions", "monitor"],
        spy(200, body),
        monkeypatch,
        token="tkn",
    )
    assert exit_code == 0

    assert len(spy.requests) == 1
    req = spy.requests[0]
    assert req.method == "GET"
    parts = urlsplit(str(req.url))
    assert parts.path == "/api/v1/sub-agents/monitor/prompt-versions"
    assert req.headers["Authorization"] == "Bearer tkn"

    stdout = capsys.readouterr().out
    # JSON pretty-print contains both ids.
    assert "11111111-1111-1111-1111-111111111111" in stdout
    assert "shadow" in stdout
    # And it's real JSON (round-trippable).
    assert json.loads(stdout) == body


def test_list_versions_forwards_status_filter_as_query_param(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    _run_cli(
        ["list-versions", "monitor", "--status", "live"],
        spy(200, []),
        monkeypatch,
    )
    req = spy.requests[0]
    qs = parse_qs(urlsplit(str(req.url)).query)
    assert qs == {"status": ["live"]}
    # And empty list still prints valid JSON.
    assert capsys.readouterr().out.strip() == "[]"


# ---------------------------------------------------------------------------
# rollback
# ---------------------------------------------------------------------------


def test_rollback_posts_and_prints_summary_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    body = {
        "now_active_version_id": "aaaa-bbbb",
        "version_no": 4,
    }
    exit_code = _run_cli(
        ["rollback", "monitor"],
        spy(200, body),
        monkeypatch,
        token="tkn",
    )
    assert exit_code == 0
    req = spy.requests[0]
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/api/v1/sub-agents/monitor/rollback"

    stdout = capsys.readouterr().out
    assert "rolled back sub-agent 'monitor'" in stdout
    assert "aaaa-bbbb" in stdout
    assert "v4" in stdout


# ---------------------------------------------------------------------------
# diff
# ---------------------------------------------------------------------------


def test_diff_get_hits_versioned_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    vid = "33333333-3333-3333-3333-333333333333"
    body = {"parent_version_id": None, "diff": "- a\n+ b\n"}
    exit_code = _run_cli(
        ["diff", "monitor", vid],
        spy(200, body),
        monkeypatch,
    )
    assert exit_code == 0
    req = spy.requests[0]
    assert req.method == "GET"
    assert urlsplit(str(req.url)).path == (
        f"/api/v1/sub-agents/monitor/prompt-versions/{vid}/diff"
    )
    assert "- a" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# activate
# ---------------------------------------------------------------------------


def test_activate_posts_to_activate_path_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    vid = "44444444-4444-4444-4444-444444444444"
    body = {"now_active_version_id": vid, "version_no": 7}
    exit_code = _run_cli(
        ["activate", "monitor", vid],
        spy(200, body),
        monkeypatch,
        token="admin-tkn",
    )
    assert exit_code == 0
    req = spy.requests[0]
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == (
        f"/api/v1/sub-agents/monitor/prompt-versions/{vid}/activate"
    )
    assert req.headers["Authorization"] == "Bearer admin-tkn"

    out = capsys.readouterr().out
    assert "activated sub-agent 'monitor'" in out
    assert vid in out
    assert "v7" in out


# ---------------------------------------------------------------------------
# force-reload
# ---------------------------------------------------------------------------


def test_force_reload_posts_to_expected_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spy = _RequestSpy()
    _run_cli(
        ["force-reload"],
        spy(200, {"reloaded": True}),
        monkeypatch,
    )
    req = spy.requests[0]
    assert req.method == "POST"
    assert urlsplit(str(req.url)).path == "/api/v1/evolution/force-reload"
    out = capsys.readouterr().out
    assert "force-reload event dispatched" in out
    assert '"reloaded": true' in out


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_non_2xx_response_exits_nonzero_and_logs(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    spy = _RequestSpy()
    exit_code = _run_cli(
        ["list-versions", "does-not-exist"],
        spy(404, {"detail": "not found"}),
        monkeypatch,
    )
    assert exit_code == 1
    assert any(
        "HTTP 404" in rec.message and "does-not-exist" in rec.message
        for rec in caplog.records
    )


def test_env_base_url_and_token_are_honoured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI must read AIOPSOS_CONTROL_URL + AIOPSOS_ADMIN_TOKEN from env."""
    spy = _RequestSpy()
    monkeypatch.setenv("AIOPSOS_CONTROL_URL", "http://ctl.example.com:9001")
    monkeypatch.setenv("AIOPSOS_ADMIN_TOKEN", "env-tkn")

    exit_code = evo_ctl.main(
        ["list-versions", "monitor"],
        transport=_make_transport(spy(200, [])),
    )
    assert exit_code == 0
    req = spy.requests[0]
    parts = urlsplit(str(req.url))
    assert parts.scheme == "http"
    assert parts.netloc == "ctl.example.com:9001"
    assert parts.path == "/api/v1/sub-agents/monitor/prompt-versions"
    assert req.headers["Authorization"] == "Bearer env-tkn"


def test_explicit_flags_override_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RequestSpy()
    monkeypatch.setenv("AIOPSOS_CONTROL_URL", "http://ignored.example:1")
    monkeypatch.setenv("AIOPSOS_ADMIN_TOKEN", "env-tkn")

    exit_code = evo_ctl.main(
        [
            "--base-url", "http://override:8002/",
            "--token", "flag-tkn",
            "list-versions", "monitor",
        ],
        transport=_make_transport(spy(200, [])),
    )
    assert exit_code == 0
    req = spy.requests[0]
    parts = urlsplit(str(req.url))
    assert parts.netloc == "override:8002"
    assert req.headers["Authorization"] == "Bearer flag-tkn"


def test_help_smoke() -> None:
    """``--help`` exits cleanly without contacting the network."""
    parser = evo_ctl._build_parser()
    with pytest.raises(SystemExit) as excinfo:
        parser.parse_args(["--help"])
    assert excinfo.value.code == 0
