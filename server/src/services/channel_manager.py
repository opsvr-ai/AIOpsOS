"""Channel manager — registers, routes, and dispatches notifications.

Usage:
    from src.services.channel_manager import channel_manager
    await channel_manager.notify(alert_title="...", alert_message="...", severity="critical")
"""

import logging
from src.services.channels.base import NotificationChannelBase, NotificationPayload
from src.services.channels.dingtalk import DingTalkChannel
from src.services.channels.wecom import WeComChannel
from src.services.channels.webhook_channel import WebhookChannel
from src.services.channels.email import EmailChannel
from src.services.channels.custom_api import CustomApiChannel

logger = logging.getLogger(__name__)


class ChannelManager:
    def __init__(self):
        self._channels: dict[str, NotificationChannelBase] = {}
        self._register_builtins()

    def _register_builtins(self):
        for ch in [DingTalkChannel(), WeComChannel(), WebhookChannel(), EmailChannel(), CustomApiChannel()]:
            self._channels[ch.channel_type] = ch

    def register(self, channel_type: str, channel: NotificationChannelBase):
        self._channels[channel_type] = channel

    def get(self, channel_type: str) -> NotificationChannelBase | None:
        return self._channels.get(channel_type)

    def list_types(self) -> list[str]:
        return sorted(self._channels.keys())

    async def send(
        self,
        channel_type: str,
        config: dict,
        title: str,
        message: str,
        severity: str = "info",
        alert_id: str | None = None,
        metadata: dict | None = None,
        recipients: list[str] | None = None,
        cc_recipients: list[str] | None = None,
    ) -> bool:
        channel = self._channels.get(channel_type)
        if channel is None:
            logger.warning("Unknown channel type: %s", channel_type)
            return False
        payload = NotificationPayload(
            title=title,
            message=message,
            severity=severity,
            alert_id=alert_id,
            metadata=metadata,
            recipients=recipients,
            cc_recipients=cc_recipients,
        )
        try:
            return await channel.send(config, payload)
        except Exception:
            logger.exception("Channel send failed for type=%s", channel_type)
            return False

    async def test(
        self,
        channel_type: str,
        config: dict,
    ) -> tuple[bool, str]:
        channel = self._channels.get(channel_type)
        if channel is None:
            return False, f"Unknown channel type: {channel_type}"
        valid, err = await channel.validate_config(config)
        if not valid:
            return False, err
        return await channel.test(config)

    async def notify(
        self,
        alert_title: str,
        alert_message: str,
        severity: str = "info",
        alert_id: str | None = None,
    ) -> dict[str, bool]:
        """Send notification to all active channels in the database."""
        from src.models.base import async_session_factory
        from src.models.channel import NotificationChannel
        from sqlalchemy import select

        results: dict[str, bool] = {}
        try:
            async with async_session_factory() as db:
                result = await db.execute(
                    select(NotificationChannel).where(NotificationChannel.is_active == True)
                )
                channels = result.scalars().all()
                for ch in channels:
                    ok = await self.send(
                        ch.channel_type, ch.config,
                        alert_title, alert_message, severity, alert_id,
                    )
                    results[ch.name] = ok
        except Exception:
            logger.exception("Failed to notify channels")
        return results


channel_manager = ChannelManager()
