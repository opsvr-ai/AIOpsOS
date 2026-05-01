import logging
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from src.services.channels.base import NotificationChannelBase, NotificationPayload

logger = logging.getLogger(__name__)


def _build_message(config: dict, payload: NotificationPayload):
    from_email = config.get("from_email", "")
    from_name = config.get("from_name", "")
    to_emails = payload.recipients or []
    cc_emails = payload.cc_recipients or []

    severity_labels = {"critical": "严重", "warning": "警告", "info": "信息", "error": "错误"}
    label = severity_labels.get(payload.severity, payload.severity)
    subject = f"[{label}] {payload.title}"
    body_html = (
        f"<h2>{payload.title}</h2>"
        f"<p>{payload.message}</p>"
        f"<hr/>"
        f"<p><strong>Severity:</strong> {payload.severity}</p>"
        f"<p><strong>Time:</strong> {time.strftime('%Y-%m-%d %H:%M:%S')}</p>"
        f"<p><strong>Source:</strong> {(payload.metadata or {}).get('source', 'AIOpsOS')}</p>"
    )

    msg = MIMEMultipart("alternative")
    sender = f"{from_name} <{from_email}>" if from_name else from_email
    msg["From"] = sender
    msg["To"] = ", ".join(to_emails)
    msg["Subject"] = subject
    if cc_emails:
        msg["Cc"] = ", ".join(cc_emails)
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    return msg, from_email, to_emails + cc_emails


class EmailChannel(NotificationChannelBase):
    channel_type = "email"

    async def send(self, config: dict, payload: NotificationPayload) -> bool:
        """Send email. Raises on error so callers can get the detail."""
        smtp_host = config.get("smtp_host", "")
        smtp_port = int(config.get("smtp_port", 587))
        smtp_username = config.get("smtp_username", "")
        smtp_password = config.get("smtp_password", "")
        from_email = config.get("from_email", "")

        to_emails = payload.recipients or []

        if not smtp_host or not from_email or not to_emails:
            raise ValueError("缺少 SMTP 主机、发件人或收件人配置")

        msg, sender_addr, all_recipients = _build_message(config, payload)

        # Port 465 → SSL; everything else → STARTTLS
        if smtp_port == 465:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
        else:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
            server.starttls()

        try:
            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)
            server.sendmail(sender_addr, all_recipients, msg.as_string())
            return True
        finally:
            server.quit()

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        required = ["smtp_host", "smtp_username", "smtp_password", "from_email"]
        for key in required:
            if not config.get(key):
                return False, f"缺少必填字段: {key}"
        port = config.get("smtp_port", 587)
        try:
            port = int(port)
        except (ValueError, TypeError):
            return False, "SMTP 端口必须是数字"
        if port < 1 or port > 65535:
            return False, "SMTP 端口必须在 1-65535 之间"
        return True, ""

    async def test(self, config: dict) -> tuple[bool, str]:
        valid, err = await self.validate_config(config)
        if not valid:
            return False, err
        from_email = config.get("from_email", "")
        test_payload = NotificationPayload(
            title="AIOpsOS 测试邮件",
            message="这是一封来自 AIOpsOS 的测试通知。如果你收到此邮件，说明邮件渠道配置正确。",
            severity="info",
            metadata={"type": "test"},
            recipients=[from_email],
        )
        try:
            await self.send(config, test_payload)
            return True, f"测试邮件已发送至 {from_email}"

        except smtplib.SMTPAuthenticationError:
            return False, "SMTP 认证失败，请检查用户名和密码"
        except smtplib.SMTPServerDisconnected as exc:
            return False, f"SMTP 服务器断开连接: {exc}"
        except smtplib.SMTPConnectError as exc:
            return False, f"无法连接 SMTP 服务器: {exc}"
        except OSError as exc:
            return False, f"网络错误: {exc}"
        except Exception as exc:
            logger.exception("EmailChannel test error")
            return False, f"邮件发送异常: {exc}"
