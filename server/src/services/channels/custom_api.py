import json as _json
import logging
import re
import time

from src.services.channels.base import NotificationChannelBase, NotificationPayload

logger = logging.getLogger(__name__)

VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
VALID_AUTH_TYPES = {"none", "basic", "bearer", "api_key"}


def _render_vars(text: str, payload: NotificationPayload) -> str:
    vars_map: dict[str, str] = {
        "title": payload.title,
        "message": payload.message,
        "severity": payload.severity,
        "alert_id": payload.alert_id or "",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": (payload.metadata or {}).get("source", "AIOpsOS"),
    }
    result = text
    for key, val in vars_map.items():
        result = result.replace(f"{{{key}}}", val)
    return result


def _render_dict(data: dict, payload: NotificationPayload) -> dict:
    return {k: _render_vars(v, payload) if isinstance(v, str) else v for k, v in data.items()}


def _check_success(resp_status: int, resp_body: str, condition: dict) -> bool:
    cond_type = condition.get("type", "status_code")
    cond_value = str(condition.get("value", "200-299"))

    if cond_type == "status_code":
        if "-" in cond_value:
            parts = cond_value.split("-", 1)
            try:
                lo, hi = int(parts[0]), int(parts[1])
                return lo <= resp_status <= hi
            except ValueError:
                return resp_status < 400
        try:
            return resp_status == int(cond_value)
        except ValueError:
            return resp_status < 400

    if cond_type == "json_field":
        try:
            data = _json.loads(resp_body)
        except _json.JSONDecodeError:
            return False
        node = data
        for key in cond_value.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                return False
        return bool(node)

    if cond_type == "body_regex":
        try:
            return bool(re.search(cond_value, resp_body))
        except re.error:
            return False

    return resp_status < 400


class CustomApiChannel(NotificationChannelBase):
    channel_type = "custom_api"

    async def send(self, config: dict, payload: NotificationPayload) -> bool:
        url = config.get("url", "")
        method = str(config.get("method", "POST")).upper()
        headers = dict(config.get("headers", {}) or {})
        query_params = dict(config.get("query_params", {}) or {})
        body_template = config.get("body_template", "")
        body_content_type = config.get("body_content_type", "application/json")
        headers["Content-Type"] = body_content_type
        auth_type = config.get("auth_type", "none")
        auth_config = dict(config.get("auth_config", {}) or {})
        success_condition = dict(config.get("success_condition", {}) or {})
        timeout_seconds = int(config.get("timeout_seconds", 30))

        if not success_condition:
            success_condition = {"type": "status_code", "value": "200-299"}
        if method not in VALID_METHODS:
            method = "POST"

        url = _render_vars(url, payload)
        headers = _render_dict(headers, payload)
        query_params = _render_dict(query_params, payload)

        # Auth
        if auth_type == "basic":
            import base64
            username = auth_config.get("username", "")
            password = auth_config.get("password", "")
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"
        elif auth_type == "bearer":
            token = auth_config.get("token", "")
            headers["Authorization"] = f"Bearer {token}"
        elif auth_type == "api_key":
            header_name = auth_config.get("header_name", "X-API-Key")
            api_key = auth_config.get("api_key", "")
            if header_name and api_key:
                headers[header_name] = api_key

        # Build body
        body_str: str | None = None
        if body_template:
            body_str = _render_vars(body_template, payload)
        elif method in ("POST", "PUT", "PATCH"):
            body_str = _json.dumps({
                "title": payload.title,
                "message": payload.message,
                "severity": payload.severity,
                "alert_id": payload.alert_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, ensure_ascii=False)

        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(
                    method=method,
                    url=url,
                    params=query_params if query_params else None,
                    data=body_str,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds),
                ) as resp:
                    resp_text = await resp.text()
                    ok = _check_success(resp.status, resp_text, success_condition)
                    if not ok:
                        logger.warning(
                            "CustomApiChannel: success check failed status=%d body=%s",
                            resp.status,
                            resp_text[:200],
                        )
                    return ok
        except Exception as exc:
            logger.exception("CustomApiChannel send error: %s", exc)
            return False

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        url = config.get("url", "")
        if not url:
            return False, "Missing required field: url"
        if not url.startswith(("http://", "https://")):
            return False, "url must start with http:// or https://"
        method = str(config.get("method", "POST")).upper()
        if method not in VALID_METHODS:
            return False, f"Invalid method: {method}. Valid: {', '.join(sorted(VALID_METHODS))}"
        auth_type = config.get("auth_type", "none")
        if auth_type not in VALID_AUTH_TYPES:
            return False, f"Invalid auth_type: {auth_type}"
        return True, ""
