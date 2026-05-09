"""evo_ctl — command-line admin for prompt-version evolution.

Spec: ``.kiro/specs/agent-runtime-optimization-evolution`` — task 23.5.

A thin HTTP wrapper over the control-plane evolution API (task 23.4).
Speaks JSON over HTTP. Reads ``AIOPSOS_CONTROL_URL`` (default
``http://localhost:8001``) and ``AIOPSOS_ADMIN_TOKEN`` (sent as an
``Authorization: Bearer ...`` header).

Subcommands
-----------
* ``list-versions <name> [--status <live|all|...>]``  — GET the list of
  prompt versions for a sub-agent.
* ``rollback <name>``                                  — POST to roll
  the active version back to the previously-active one.
* ``diff <name> <version-id>``                         — GET a diff of
  the named version against its parent.
* ``activate <name> <version-id>``                     — POST to
  manually promote the named version to active (admin override).
* ``force-reload``                                     — POST to ask
  every running instance's ``PromptReloader`` to re-read the DB.

The endpoint paths follow design.md § API / CLI. The CLI targets the
FastAPI control plane exposed by ``src.main_control`` whose router is
mounted under ``/api/v1``.

Exit codes
----------
* ``0`` — success.
* ``1`` — HTTP error, network error, or non-2xx response.
* ``2`` — CLI usage error (argparse default).

Examples
--------

.. code:: bash

    AIOPSOS_CONTROL_URL=https://ops.example.com:8001 \\
    AIOPSOS_ADMIN_TOKEN=eyJhbGc... \\
    python -m scripts.evo_ctl list-versions monitor

    python -m scripts.evo_ctl rollback monitor
    python -m scripts.evo_ctl diff monitor 9b3f...
    python -m scripts.evo_ctl activate monitor 9b3f...
    python -m scripts.evo_ctl force-reload
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
logger = logging.getLogger("evo_ctl")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


_DEFAULT_BASE_URL = "http://localhost:8001"
_DEFAULT_API_PREFIX = "/api/v1"
_DEFAULT_TIMEOUT_S = 15.0


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
    headers = {"Accept": "application/json"}
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


class EvoCtlError(RuntimeError):
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


def _request(
    method: str,
    url: str,
    *,
    client: httpx.Client,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> Any:
    """Issue an HTTP call and return the decoded body; raise on non-2xx."""
    try:
        resp = client.request(method, url, params=params, json=json_body)
    except httpx.HTTPError as exc:
        raise EvoCtlError(f"network error: {exc}") from exc

    body = _render_response_body(resp)
    if resp.status_code >= 400:
        detail = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        raise EvoCtlError(
            f"HTTP {resp.status_code} {resp.reason_phrase} {method} {url}: {detail}"
        )
    return body


def _print_json(obj: Any) -> None:
    """Pretty-print JSON to stdout."""
    print(json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False))


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_list_versions(args: argparse.Namespace, client: httpx.Client) -> int:
    url = _build_url(
        args.base_url, args.api_prefix,
        f"/sub-agents/{args.name}/prompt-versions",
    )
    params: dict[str, Any] = {}
    if args.status:
        params["status"] = args.status
    body = _request("GET", url, client=client, params=params or None)
    _print_json(body)
    return 0


def cmd_rollback(args: argparse.Namespace, client: httpx.Client) -> int:
    url = _build_url(
        args.base_url, args.api_prefix,
        f"/sub-agents/{args.name}/rollback",
    )
    body = _request("POST", url, client=client)
    # Write commands: terse summary line first, then body for operators.
    if isinstance(body, dict):
        version_id = body.get("now_active_version_id") or body.get("active_version_id")
        version_no = body.get("version_no") or body.get("active_version_no")
        if version_id is not None:
            print(
                f"rolled back sub-agent {args.name!r} → active version "
                f"{version_id} (v{version_no})"
            )
        else:
            print(f"rolled back sub-agent {args.name!r}")
    else:
        print(f"rolled back sub-agent {args.name!r}")
    _print_json(body)
    return 0


def cmd_diff(args: argparse.Namespace, client: httpx.Client) -> int:
    url = _build_url(
        args.base_url, args.api_prefix,
        f"/sub-agents/{args.name}/prompt-versions/{args.version_id}/diff",
    )
    body = _request("GET", url, client=client)
    _print_json(body)
    return 0


def cmd_activate(args: argparse.Namespace, client: httpx.Client) -> int:
    url = _build_url(
        args.base_url, args.api_prefix,
        f"/sub-agents/{args.name}/prompt-versions/{args.version_id}/activate",
    )
    body = _request("POST", url, client=client)
    if isinstance(body, dict):
        version_id = body.get("now_active_version_id") or args.version_id
        version_no = body.get("version_no") or body.get("active_version_no")
        suffix = f" (v{version_no})" if version_no is not None else ""
        print(
            f"activated sub-agent {args.name!r} prompt version "
            f"{version_id}{suffix}"
        )
    else:
        print(
            f"activated sub-agent {args.name!r} prompt version {args.version_id}"
        )
    _print_json(body)
    return 0


def cmd_force_reload(args: argparse.Namespace, client: httpx.Client) -> int:
    """POST the force-reload kick.

    The endpoint is documented in design.md § API / CLI as
    ``POST /api/control/evolution/force-reload``. If task 23.4 has not
    yet landed the route, the server returns ``404`` and the CLI exits
    non-zero with a clear message — matching the "document as stub"
    instruction in tasks.md.
    """
    url = _build_url(args.base_url, args.api_prefix, "/evolution/force-reload")
    body = _request("POST", url, client=client)
    print("force-reload event dispatched")
    _print_json(body)
    return 0


# ---------------------------------------------------------------------------
# Argparse wiring
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evo_ctl",
        description=(
            "Admin CLI for the agent-runtime evolution control plane "
            "(prompt versions / rollback / activate / force-reload)."
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

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    # list-versions -------------------------------------------------------
    p_list = sub.add_parser(
        "list-versions",
        help="List prompt versions for a sub-agent.",
    )
    p_list.add_argument("name", help="Sub-agent name (e.g. monitor).")
    p_list.add_argument(
        "--status",
        default=None,
        help=(
            "Optional status filter. Typical values: live, all, active, "
            "shadow, ab, retired."
        ),
    )
    p_list.set_defaults(func=cmd_list_versions)

    # rollback -----------------------------------------------------------
    p_roll = sub.add_parser(
        "rollback",
        help="Roll a sub-agent back to the previously-active prompt version.",
    )
    p_roll.add_argument("name", help="Sub-agent name.")
    p_roll.set_defaults(func=cmd_rollback)

    # diff ---------------------------------------------------------------
    p_diff = sub.add_parser(
        "diff",
        help="Show the diff for a prompt version vs its parent.",
    )
    p_diff.add_argument("name", help="Sub-agent name.")
    p_diff.add_argument("version_id", help="Prompt version id (UUID).")
    p_diff.set_defaults(func=cmd_diff)

    # activate -----------------------------------------------------------
    p_act = sub.add_parser(
        "activate",
        help="Manually promote a prompt version to active (admin override).",
    )
    p_act.add_argument("name", help="Sub-agent name.")
    p_act.add_argument("version_id", help="Prompt version id (UUID).")
    p_act.set_defaults(func=cmd_activate)

    # force-reload -------------------------------------------------------
    p_reload = sub.add_parser(
        "force-reload",
        help=(
            "Ask every instance's PromptReloader to re-read the DB. "
            "Requires the POST /evolution/force-reload endpoint."
        ),
    )
    p_reload.set_defaults(func=cmd_force_reload)

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
    """Entry point for ``python -m scripts.evo_ctl``.

    ``transport`` is a test hook: an :class:`httpx.BaseTransport` to
    substitute for the real TCP transport. Using this hook (instead of
    replacing the whole client) preserves the CLI's header and
    timeout configuration so tests can still exercise them.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Normalise derived values so the bound func can look at ``args``
    # without re-reading the environment.
    args.base_url = args.base_url.rstrip("/")
    if not args.api_prefix.startswith("/"):
        args.api_prefix = "/" + args.api_prefix
    args.api_prefix = args.api_prefix.rstrip("/")

    client = _make_client(args, transport=transport)

    try:
        with client:
            return args.func(args, client)
    except EvoCtlError as exc:
        logger.error("%s", exc)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
