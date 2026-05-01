import hashlib
import hmac
import time
import logging
from src.services.channels.base import NotificationChannelBase, NotificationPayload

logger = logging.getLogger(__name__)

class WebhookChannel(NotificationChannelBase):
    channel_type = "webhook"

    async def send(self, config: dict, payload: NotificationPayload) -> bool:
        url = config.get("url", "")
        secret = config.get("secret", "")
        method = config.get("method", "POST")
        headers = dict(config.get("headers", {}) or {})

        if not url:
            logger.warning("WebhookChannel: missing url")
            return False

        body = {
            "title": payload.title,
            "message": payload.message,
            "severity": payload.severity,
            "alert_id": payload.alert_id,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "AIOpsOS",
        }
        if payload.metadata:
            body["metadata"] = payload.metadata

        import json as _json
        body_str = _json.dumps(body, ensure_ascii=False)

        # HMAC-SHA256 signature
        if secret:
            timestamp_str = str(int(time.time()))
            sign = hmac.new(
                secret.encode(), f"{timestamp_str}.{body_str}".encode(), hashlib.sha256
            ).hexdigest()
            headers["X-AIOpsOS-Signature-256"] = f"sha256={sign}"
            headers["X-AIOpsOS-Timestamp"] = timestamp_str

        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                if method.upper() == "POST":
                    async with session.post(url, data=body_str, headers=headers,
                        timeout=aiohttp.ClientTimeout(10)) as resp:
                        return resp.status < 400
                else:
                    async with session.put(url, data=body_str, headers=headers,
                        timeout=aiohttp.ClientTimeout(10)) as resp:
                        return resp.status < 400
        except Exception as exc:
            logger.exception("WebhookChannel send error: %s", exc)
            return False
