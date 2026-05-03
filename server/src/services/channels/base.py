from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class NotificationPayload:
    title: str
    message: str
    severity: str = "info"
    alert_id: str | None = None
    metadata: dict | None = None
    recipients: list[str] | None = None
    cc_recipients: list[str] | None = None

class NotificationChannelBase(ABC):
    channel_type: str = "base"

    @abstractmethod
    async def send(self, config: dict, payload: NotificationPayload) -> bool: ...

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        return True, ""

    async def test(self, config: dict) -> tuple[bool, str]:
        test_payload = NotificationPayload(
            title="AIOpsOS Test",
            message="This is a test notification from AIOpsOS. If you received this, the channel is configured correctly.",
            severity="info",
            metadata={"type": "test"},
        )
        try:
            ok = await self.send(config, test_payload)
            if ok:
                return True, "测试消息发送成功"
            return False, "测试消息发送失败，请检查配置"
        except Exception as exc:
            return False, f"测试异常: {exc}"
