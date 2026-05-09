"""rollout_router_llm — bump the ``router_llm_enabled`` rollout percentage.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 24.2.

After a 7-day soak at the 10% default (task 24.1) with no regression in
``tests/bench/test_chat_latency.py``, ops bump RouterLLM to a 100%
rollout. This script is the thin, auditable "bump the flag" step:

    python -m scripts.rollout_router_llm                # 100% + enabled
    python -m scripts.rollout_router_llm --rollout 10   # rollback to 10%
    python -m scripts.rollout_router_llm --rollout 0 --disable
    python -m scripts.rollout_router_llm --dry-run      # print plan only

Under the hood the script issues a single ``PUT`` against the runtime
flags admin API documented in design.md § Runtime Flags and
implemented by :mod:`src.api.control.runtime_flags`:

    PUT /api/v1/runtime-flags/router_llm_enabled
    {"enabled": true, "rollout_percent": 100}

Configuration
-------------
``AIOPSOS_CONTROL_URL`` — control-plane base URL
    (default ``http://localhost:8001``).
``AIOPSOS_CONTROL_API_PREFIX`` — API prefix (default ``/api/v1``).
``AIOPSOS_ADMIN_TOKEN`` — Bearer token; sent as
    ``Authorization: Bearer ...`` when set.

Exit codes
----------
* ``0`` — rollout applied (or dry-run printed).
* ``1`` — HTTP error, network error, or non-2xx response.
* ``2`` — CLI usage error (argparse default).

The runbook lives at ``docs/admin-guide/router-llm-rollout.md`` and
covers the 7-day verification procedure, tolerance thresholds, and the
rollback path (re-run with ``--rollout 10`` or ``--rollout 0``).
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("rollout_router_llm")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


FLAG_KEY = "router_llm_enabled"

_DEFAULT_BASE_URL = "http://localhost:8001"
_DEFAULT_API_PREFIX = "/api/v1"
_DEFAULT_TIMEOUT_S = 15.0
_DEFAULT_ROLLOUT = 100


def _env_base_url() -> str:
    return os.getenv("AIOPSOS_CONTROL_URL", _DEFAULT_BASE_URL).rstrip("/")


def _env_admin_token() -> str | None:
    token = os.getenv("AIOPSOS_ADMIN_TOKEN")
    return token.strip() if token else None


def _env_api_prefix() -> str:
    prefix = os.getenv("AIOPSOS_CONTROL_API_PREFIX", _DEFAULT_API_PREFIX)
    if not prefix.startswith("/"):
        prefix = "/" + prefix
    return prefix.rstrip("/")


def _build_headers(token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _build_url(base_url: str, api_prefix: str, path: str) -> str:
    """Join ``base_url`` + ``api_prefix`` + ``path`` without duplicate slashes."""
    if not path.startswith("/"):
        path = "/" + path
    return f"{base_url}{api_prefix}{path}"


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


class RolloutError(RuntimeError):
    """Non-retryable CLI-side failure (bad response, network error)."""


def _render_response_body(resp: httpx.Response) -> Any:
    """Parse a response body as JSON, falling back to raw text."""
    ctype = resp.headers.get("Content-Type", "")
    if "application/json" in ctype or resp.text.startswith(("{", "[")):
        try:
            return resp.json()
        except ValueError:
            pass
    return resp.text


def _put_flag(
    *,
    client: httpx.Client,
    url: str,
    body: dict[str, Any],
) -> Any:
    try:
        resp = client.put(url, json=body)
    except httpx.HTTPError as exc:
        raise RolloutError(f"network error: {exc}") from exc

    parsed = _render_response_body(resp)
    if resp.status_code >= 400:
        detail = (
            parsed if isinstance(parsed, str)
            else json.dumps(parsed, ensure_ascii=False)
        )
        raise RolloutError(
            f"HTTP {resp.status_code} {resp.reason_phrase} PUT {url}: {detail}"
        )
    return parsed


# ---------------------------------------------------------------------------
# Rollout body & summary
# ---------------------------------------------------------------------------


def build_rollout_body(rollout_percent: int, *, enabled: bool = True) -> dict[str, Any]:
    """Build the request body for the PUT call.

    Matches :class:`src.schemas.runtime_flag.RuntimeFlagUpsert`: keys are
    ``enabled`` and ``rollout_percent``. ``data`` is intentionally
    omitted so the existing description JSON on the flag row is
    preserved by the admin handler (see
    :mod:`src.api.control.runtime_flags`).
    """
    if not 0 <= rollout_percent <= 100:
        raise ValueError(
            f"rollout_percent must be between 0 and 100, got {rollout_percent!r}"
        )
    return {
        "enabled": bool(enabled),
        "rollout_percent": int(rollout_percent),
    }


def _summarise(rollout_percent: int, enabled: bool) -> str:
    if not enabled:
        return f"router_llm_enabled: DISABLED (rollout={rollout_percent}%)"
    if rollout_percent >= 100:
        return "router_llm_enabled: FULL rollout (100%)"
    if rollout_percent == 0:
        return "router_llm_enabled: enabled flag set, 0% rollout (effectively off)"
    return f"router_llm_enabled: partial rollout {rollout_percent}%"


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rollout_router_llm",
        description=(
            "Bump the router_llm_enabled feature flag via the runtime "
            "flags admin API. Used in task 24.2 of the agent-runtime "
            "spec to promote RouterLLM from 10% to 100% after a 7-day "
            "no-regression soak."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=_env_base_url(),
        help=(
            "Control-plane base URL (env AIOPSOS_CONTROL_URL; "
            f"default {_DEFAULT_BASE_URL})."
        ),
    )
    parser.add_argument(
        "--api-prefix",
        default=_env_api_prefix(),
        help=(
            "API prefix appended to base-url (env "
            f"AIOPSOS_CONTROL_API_PREFIX; default {_DEFAULT_API_PREFIX})."
        ),
    )
    parser.add_argument(
        "--token",
        default=_env_admin_token(),
        help=(
            "Bearer admin token (env AIOPSOS_ADMIN_TOKEN). "
            "If unset, no Authorization header is sent."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=_DEFAULT_TIMEOUT_S,
        help=f"HTTP timeout in seconds (default {_DEFAULT_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--rollout",
        "--rollout-percent",
        dest="rollout_percent",
        type=int,
        default=_DEFAULT_ROLLOUT,
        help=(
            "Target rollout percentage 0-100 "
            f"(default {_DEFAULT_ROLLOUT} = full rollout). "
            "Use lower values for staged rollback, e.g. --rollout 10."
        ),
    )
    # --enabled / --disable lets operators kill-switch the flag without
    # losing the current rollout_percent. Default is --enabled=true so
    # the happy path of "bump to 100%" Just Works.
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--enable",
        dest="enabled",
        action="store_true",
        default=True,
        help="Set enabled=true (default).",
    )
    group.add_argument(
        "--disable",
        dest="enabled",
        action="store_false",
        help=(
            "Set enabled=false (kill switch). When disabled, the flag "
            "is off regardless of rollout_percent."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the request plan without sending it.",
    )
    return parser


def _make_client(
    args: argparse.Namespace,
    *,
    transport: httpx.BaseTransport | None = None,
) -> httpx.Client:
    kwargs: dict[str, Any] = {
        "headers": _build_headers(args.token),
        "timeout": args.timeout,
    }
    if transport is not None:
        kwargs["transport"] = transport
    return httpx.Client(**kwargs)


def main(
    argv: list[str] | None = None,
    *,
    transport: httpx.BaseTransport | None = None,
) -> int:
    """Entry point for ``python -m scripts.rollout_router_llm``.

    ``transport`` is a test hook: an :class:`httpx.BaseTransport` to
    substitute for the real TCP transport so tests can assert the
    exact request body without spinning up a server.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Normalise derived values once.
    args.base_url = args.base_url.rstrip("/")
    if not args.api_prefix.startswith("/"):
        args.api_prefix = "/" + args.api_prefix
    args.api_prefix = args.api_prefix.rstrip("/")

    try:
        body = build_rollout_body(args.rollout_percent, enabled=args.enabled)
    except ValueError as exc:
        parser.error(str(exc))  # exits with code 2

    url = _build_url(args.base_url, args.api_prefix, f"/runtime-flags/{FLAG_KEY}")

    if args.dry_run:
        print(f"[dry-run] PUT {url}")
        print(f"[dry-run] body: {json.dumps(body, ensure_ascii=False)}")
        print(f"[dry-run] {_summarise(args.rollout_percent, args.enabled)}")
        return 0

    client = _make_client(args, transport=transport)
    try:
        with client:
            result = _put_flag(client=client, url=url, body=body)
    except RolloutError as exc:
        logger.error("%s", exc)
        return 1

    print(_summarise(args.rollout_percent, args.enabled))
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=False))
    else:
        print(str(result))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
