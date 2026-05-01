import hashlib
import hmac
import time
import urllib.parse
import logging
from src.services.channels.base import NotificationChannelBase, NotificationPayload

logger = logging.getLogger(__name__)

class DingTalkChannel(NotificationChannelBase):
    channel_type = "dingtalk"

    async def send(self, config: dict, payload: NotificationPayload) -> bool:
        webhook_url = config.get("webhook_url", "")
        secret = config.get("secret", "")
        if not webhook_url:
            logger.warning("DingTalk: missing webhook_url")
            return False

        timestamp = str(round(time.time() * 1000))
        sign = self._sign(timestamp, secret) if secret else ""
        url = f"{webhook_url}&timestamp={timestamp}&sign={sign}" if sign else webhook_url

        import aiohttp
        severity_emoji = {"critical": "🔥", "warning": "⚠️", "info": "ℹ️", "error": "❌"}

        body = {
            "msgtype": "markdown",
            "markdown": {
                "title": payload.title,
                "text": (
                    f"## {severity_emoji.get(payload.severity, '📢')} {payload.title}\n\n"
                    f"{payload.message}\n\n"
                    f"> 严重程度: {payload.severity}\n"
                    f"> 时间: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                    f"> 来源: AIOpsOS"
                ),
            },
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(10)) as resp:
                    result = await resp.json()
                    if result.get("errcode") == 0:
                        return True
                    logger.error("DingTalk send failed: %s", result)
                    return False
        except Exception as exc:
            logger.exception("DingTalk send error: %s", exc)
            return False

    @staticmethod
    def _sign(timestamp: str, secret: str) -> str:
        string_to_sign = f"{timestamp}\n{secret}"
        mac = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha256)
        return urllib.parse.quote_plus(mac.digest().hex())
