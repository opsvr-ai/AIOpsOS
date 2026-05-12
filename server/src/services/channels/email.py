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
        smtp_port = int(config.get("smtp_port", 25))
        smtp_username = config.get("smtp_username", "")
        smtp_password = config.get("smtp_password", "")
        from_email = config.get("from_email", "")
        
        # SSL/TLS configuration:
        # - use_ssl: true = SSL connection (port 465 typical)
        # - use_tls: true = STARTTLS upgrade (port 587 typical)
        # - both false = plain SMTP (port 25 typical, for internal networks)
        use_ssl = config.get("use_ssl", False)
        use_tls = config.get("use_tls", False)
        
        # Auto-detect based on port if not explicitly configured
        if not use_ssl and not use_tls:
            if smtp_port == 465:
                use_ssl = True
            elif smtp_port == 587:
                use_tls = True
            # Port 25: keep both False for plain SMTP

        # Recipients: use payload recipients, or fall back to default_recipients from config
        to_emails = payload.recipients or []
        if not to_emails:
            default_recipients = config.get("default_recipients", "")
            if default_recipients:
                # Support comma-separated list
                to_emails = [e.strip() for e in default_recipients.split(",") if e.strip()]

        if not smtp_host or not from_email or not to_emails:
            missing = []
            if not smtp_host:
                missing.append("SMTP 主机")
            if not from_email:
                missing.append("发件人邮箱")
            if not to_emails:
                missing.append("收件人 (请在调用时指定 recipients 参数，或在渠道配置中设置默认收件人)")
            raise ValueError(f"缺少配置: {', '.join(missing)}")

        msg, sender_addr, all_recipients = _build_message(config, payload)

        server = None
        try:
            if use_ssl:
                # SSL connection from the start (typically port 465)
                logger.debug(f"Connecting to {smtp_host}:{smtp_port} with SSL")
                server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=15)
            else:
                # Plain SMTP connection
                logger.debug(f"Connecting to {smtp_host}:{smtp_port} (plain)")
                server = smtplib.SMTP(smtp_host, smtp_port, timeout=15)
                if use_tls:
                    # Upgrade to TLS (STARTTLS)
                    logger.debug("Upgrading connection with STARTTLS")
                    server.starttls()

            if smtp_username and smtp_password:
                logger.debug(f"Authenticating as {smtp_username}")
                server.login(smtp_username, smtp_password)
            
            logger.debug(f"Sending email from {sender_addr} to {all_recipients}")
            server.sendmail(sender_addr, all_recipients, msg.as_string())
            logger.info(f"Email sent successfully to {all_recipients}")
            return True
        finally:
            if server:
                try:
                    server.quit()
                except Exception:
                    pass

    async def validate_config(self, config: dict) -> tuple[bool, str]:
        required = ["smtp_host", "from_email"]
        for key in required:
            if not config.get(key):
                return False, f"缺少必填字段: {key}"
        port = config.get("smtp_port", 25)
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
        
        # Use test_recipient if configured, otherwise send to from_email
        test_recipient = config.get("test_recipient", from_email)
        
        test_payload = NotificationPayload(
            title="AIOpsOS 测试邮件",
            message="这是一封来自 AIOpsOS 的测试通知。如果你收到此邮件，说明邮件渠道配置正确。",
            severity="info",
            metadata={"type": "test"},
            recipients=[test_recipient],
        )
        try:
            await self.send(config, test_payload)
            return True, f"测试邮件已发送至 {test_recipient}"

        except smtplib.SMTPAuthenticationError as exc:
            logger.warning(f"SMTP auth failed: {exc}")
            return False, f"SMTP 认证失败，请检查用户名和密码。错误详情: {exc}"
        except smtplib.SMTPServerDisconnected as exc:
            return False, f"SMTP 服务器断开连接: {exc}"
        except smtplib.SMTPConnectError as exc:
            return False, f"无法连接 SMTP 服务器: {exc}"
        except smtplib.SMTPRecipientsRefused as exc:
            return False, f"收件人被拒绝: {exc}"
        except smtplib.SMTPSenderRefused as exc:
            return False, f"发件人被拒绝: {exc}"
        except smtplib.SMTPDataError as exc:
            return False, f"邮件数据错误: {exc}"
        except smtplib.SMTPException as exc:
            return False, f"SMTP 错误: {exc}"
        except OSError as exc:
            return False, f"网络错误: {exc}"
        except Exception as exc:
            logger.exception("EmailChannel test error")
            return False, f"邮件发送异常: {exc}"
