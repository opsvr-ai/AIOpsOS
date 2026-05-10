"""Email notification service for emergency collaboration.

This module provides the EmailNotificationService for sending email notifications
during emergency collaboration sessions. It wraps the existing email channel
with collaboration-specific features including:
- Automatic email sending when collaboration sessions are created
- Template-based email content with variable substitution
- Email send status tracking and retry support
- Integration with scenario and collaboration session data

Requirements:
- 8.1: Support automatic email notification when collaboration session is created
- 8.2: Support configuring email recipients from scenario config and user groups
- 8.3: Support email templates with subject and body
- 8.4: Support template variable substitution (scenario info, alert info, session info)
- 8.5: Send status update emails when collaboration session status changes
- 8.6: Record email send status and timestamp
- 8.7: Support retry on send failure
"""

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.agent import Scenario
from src.models.channel import NotificationChannel
from src.models.collaboration import CollaborationSession
from src.models.user import Role, User
from src.services.channel_manager import channel_manager
from src.services.channels.base import NotificationPayload

logger = logging.getLogger(__name__)


class EmailSendStatus(str, Enum):  # noqa: UP042
    """Email send status values.

    Note: Using str + Enum for JSON serialization compatibility.
    Consider migrating to StrEnum when Python 3.11+ is the minimum version.
    """

    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"
    RETRYING = "retrying"


@dataclass
class EmailSendResult:
    """Result of an email send operation.

    Attributes:
        success: Whether the email was sent successfully.
        status: The send status (pending, sent, failed, retrying).
        recipients: List of email recipients.
        subject: The email subject.
        sent_at: Timestamp when the email was sent (if successful).
        error: Error message if the send failed.
        retry_count: Number of retry attempts made.
        message_id: Optional message ID for tracking.
    """

    success: bool
    status: EmailSendStatus
    recipients: list[str] = field(default_factory=list)
    subject: str = ""
    sent_at: datetime | None = None
    error: str | None = None
    retry_count: int = 0
    message_id: str | None = None


@dataclass
class EmailTemplate:
    """Email template definition.

    Attributes:
        id: Unique template identifier.
        name: Human-readable template name.
        subject_template: Subject line template with variable placeholders.
        body_template: HTML body template with variable placeholders.
        description: Optional description of the template.
    """

    id: str
    name: str
    subject_template: str
    body_template: str
    description: str = ""


@dataclass
class RecipientConfig:
    """Configuration for email recipients.

    Supports multiple sources for recipient email addresses:
    - Direct email addresses
    - User group (role) IDs
    - User IDs

    Attributes:
        email_addresses: List of direct email addresses.
        user_group_ids: List of user group (role) UUIDs to include.
        user_ids: List of user UUIDs to include.
        include_scenario_owner: Whether to include the scenario owner.

    Requirements:
        - 8.2: Support configuring recipients from scenario config and user groups
    """

    email_addresses: list[str] = field(default_factory=list)
    user_group_ids: list[str] = field(default_factory=list)
    user_ids: list[str] = field(default_factory=list)
    include_scenario_owner: bool = False

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RecipientConfig":
        """Create a RecipientConfig from a configuration dictionary.

        Parses the collaboration config to extract recipient settings.

        Args:
            config: Configuration dictionary, typically from scenario.collaboration_config
                or session.config_snapshot.

        Returns:
            RecipientConfig instance with parsed settings.

        Expected config structure:
            {
                "email_recipients": ["user@example.com", ...],
                "email_user_groups": ["group-uuid-1", ...],
                "email_user_ids": ["user-uuid-1", ...],
                "include_scenario_owner": true
            }
        """
        return cls(
            email_addresses=config.get("email_recipients", []),
            user_group_ids=config.get("email_user_groups", []),
            user_ids=config.get("email_user_ids", []),
            include_scenario_owner=config.get("include_scenario_owner", False),
        )

    def has_recipients(self) -> bool:
        """Check if any recipient sources are configured.

        Returns:
            True if at least one recipient source is configured.
        """
        return bool(
            self.email_addresses
            or self.user_group_ids
            or self.user_ids
            or self.include_scenario_owner
        )


# Built-in email templates for collaboration sessions
BUILTIN_TEMPLATES: dict[str, EmailTemplate] = {
    "session_created": EmailTemplate(
        id="session_created",
        name="协同会话创建通知",
        subject_template="[应急协同] {scenario_name} - 协同会话已创建",
        body_template="""
<h2>应急协同会话已创建</h2>

<table style="border-collapse: collapse; width: 100%;">
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>场景名称</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{scenario_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>场景类型</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{scenario_type}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>触发原因</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{trigger_reason}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>会话ID</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{session_id}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>创建时间</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{created_at}</td>
    </tr>
</table>

<p style="margin-top: 20px;">
    请及时关注协同会话进展，并参与问题处理。
</p>

<hr/>
<p style="color: #666; font-size: 12px;">
    此邮件由 AIOpsOS 应急协同系统自动发送，请勿直接回复。
</p>
""",
        description="协同会话创建时发送的通知邮件",
    ),
    "session_status_changed": EmailTemplate(
        id="session_status_changed",
        name="协同会话状态变更通知",
        subject_template="[应急协同] {scenario_name} - 状态变更: {new_status}",
        body_template="""
<h2>协同会话状态已变更</h2>

<table style="border-collapse: collapse; width: 100%;">
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>场景名称</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{scenario_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>会话ID</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{session_id}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>原状态</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{old_status}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>新状态</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{new_status}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>变更时间</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{changed_at}</td>
    </tr>
</table>

{status_details}

<hr/>
<p style="color: #666; font-size: 12px;">
    此邮件由 AIOpsOS 应急协同系统自动发送，请勿直接回复。
</p>
""",
        description="协同会话状态变更时发送的通知邮件",
    ),
    "session_resolved": EmailTemplate(
        id="session_resolved",
        name="协同会话已解决通知",
        subject_template="[应急协同] {scenario_name} - 问题已解决",
        body_template="""
<h2>协同会话问题已解决</h2>

<table style="border-collapse: collapse; width: 100%;">
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>场景名称</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{scenario_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>会话ID</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{session_id}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>触发原因</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{trigger_reason}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>创建时间</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{created_at}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>解决时间</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{resolved_at}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>处理时长</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{duration_minutes} 分钟</td>
    </tr>
</table>

<h3>处理摘要</h3>
{summary}

<hr/>
<p style="color: #666; font-size: 12px;">
    此邮件由 AIOpsOS 应急协同系统自动发送，请勿直接回复。
</p>
""",
        description="协同会话问题解决时发送的通知邮件",
    ),
    "session_closed": EmailTemplate(
        id="session_closed",
        name="协同会话关闭通知",
        subject_template="[应急协同] {scenario_name} - 会话已关闭",
        body_template="""
<h2>协同会话已关闭</h2>

<table style="border-collapse: collapse; width: 100%;">
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>场景名称</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{scenario_name}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>会话ID</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{session_id}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>创建时间</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{created_at}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>关闭时间</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{closed_at}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>总处理时长</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{duration_minutes} 分钟</td>
    </tr>
</table>

<h3>会话总结报告</h3>
{summary_report}

<hr/>
<p style="color: #666; font-size: 12px;">
    此邮件由 AIOpsOS 应急协同系统自动发送，请勿直接回复。
</p>
""",
        description="协同会话关闭时发送的总结邮件",
    ),
}

# Status display names in Chinese
STATUS_DISPLAY_NAMES: dict[str, str] = {
    "created": "已创建",
    "active": "处理中",
    "resolved": "已解决",
    "closed": "已关闭",
}


class EmailNotificationService:
    """Service for sending email notifications in collaboration sessions.

    This service handles all email notifications related to emergency
    collaboration sessions, including:
    - Session creation notifications
    - Status change notifications
    - Resolution and closure notifications

    Features:
        - Template-based email content with variable substitution
        - Configurable recipients from scenario config and user groups
        - Send status tracking and retry support
        - Integration with existing email channel infrastructure

    Requirements:
        - 8.1: Support automatic email notification when session is created
        - 8.2: Support configuring recipients from scenario config and user groups
        - 8.3: Support email templates with subject and body
        - 8.4: Support template variable substitution
        - 8.5: Send status update emails when session status changes
        - 8.6: Record email send status and timestamp
        - 8.7: Support retry on send failure
    """

    DEFAULT_MAX_RETRIES = 3
    DEFAULT_RETRY_DELAY_SECONDS = 5

    def __init__(
        self,
        db: AsyncSession,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        """Initialize the email notification service.

        Args:
            db: Async database session for persistence operations.
            max_retries: Maximum number of retry attempts for failed sends.
        """
        self._db = db
        self._max_retries = max_retries
        self._templates = BUILTIN_TEMPLATES.copy()

    def get_template(self, template_id: str) -> EmailTemplate | None:
        """Get an email template by ID.

        Args:
            template_id: The template identifier.

        Returns:
            The EmailTemplate if found, None otherwise.
        """
        return self._templates.get(template_id)

    def list_templates(self) -> list[EmailTemplate]:
        """List all available email templates.

        Returns:
            List of all registered EmailTemplates.
        """
        return list(self._templates.values())

    def register_template(self, template: EmailTemplate) -> None:
        """Register a custom email template.

        Args:
            template: The EmailTemplate to register.
        """
        self._templates[template.id] = template
        logger.info("Registered email template: %s", template.id)

    def render_template(
        self,
        template: EmailTemplate,
        variables: dict[str, Any],
    ) -> tuple[str, str]:
        """Render an email template with variable substitution.

        Substitutes placeholders in the template with actual values.
        Placeholders use the format {variable_name}.

        Args:
            template: The EmailTemplate to render.
            variables: Dictionary of variable names to values.

        Returns:
            Tuple of (rendered_subject, rendered_body).

        Requirements:
            - 8.4: Support template variable substitution
        """
        subject = template.subject_template
        body = template.body_template

        for key, value in variables.items():
            placeholder = "{" + key + "}"
            str_value = str(value) if value is not None else ""
            subject = subject.replace(placeholder, str_value)
            body = body.replace(placeholder, str_value)

        return subject, body

    def build_session_variables(
        self,
        session: CollaborationSession,
        config: dict[str, Any] | None = None,
        extra_vars: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build template variables from a collaboration session.

        Extracts relevant information from the session and config to
        populate email template variables.

        Args:
            session: The collaboration session.
            config: Optional configuration snapshot (uses session.config_snapshot if None).
            extra_vars: Optional additional variables to include.

        Returns:
            Dictionary of variable names to values.

        Requirements:
            - 8.4: Support template variable substitution (scenario info, alert info, session info)
        """
        if config is None:
            config = session.config_snapshot or {}

        # Format timestamps
        created_at = "N/A"
        if session.created_at:
            created_at = session.created_at.strftime("%Y-%m-%d %H:%M:%S")

        resolved_at = "N/A"
        if session.resolved_at:
            resolved_at = session.resolved_at.strftime("%Y-%m-%d %H:%M:%S")

        closed_at = "N/A"
        if session.closed_at:
            closed_at = session.closed_at.strftime("%Y-%m-%d %H:%M:%S")

        # Calculate duration
        duration_minutes = 0
        if session.created_at:
            end_time = session.closed_at or session.resolved_at or datetime.now(UTC)
            duration = end_time - session.created_at
            duration_minutes = int(duration.total_seconds() / 60)

        # Build progress summary
        progress = session.progress_summary or {}
        current_phase = progress.get("current_phase", "unknown")
        completed_steps = progress.get("completed_steps", [])
        pending_items = progress.get("pending_items", [])

        # Build summary report HTML
        summary_report = ""
        if session.summary_report:
            report = session.summary_report
            summary_report = f"""
<p><strong>处理结果:</strong> {report.get('outcome', 'N/A')}</p>
<p><strong>根因分析:</strong> {report.get('root_cause', 'N/A')}</p>
<p><strong>解决方案:</strong> {report.get('resolution', 'N/A')}</p>
"""

        variables = {
            # Session info
            "session_id": str(session.id),
            "session_status": session.status,
            "session_status_display": STATUS_DISPLAY_NAMES.get(session.status, session.status),
            "trigger_reason": session.trigger_reason or "未指定",
            "created_at": created_at,
            "resolved_at": resolved_at,
            "closed_at": closed_at,
            "duration_minutes": duration_minutes,
            # Scenario info from config snapshot
            "scenario_name": config.get("scenario_name", "未知场景"),
            "scenario_type": config.get("scenario_type", "unknown"),
            # Group chat info
            "group_chat_id": session.group_chat_id or "N/A",
            "group_chat_name": session.group_chat_name or "N/A",
            # Progress info
            "current_phase": current_phase,
            "completed_steps": ", ".join(completed_steps) if completed_steps else "无",
            "pending_items": ", ".join(pending_items) if pending_items else "无",
            # Summary
            "summary": self._format_progress_summary(progress),
            "summary_report": summary_report or "<p>暂无总结报告</p>",
        }

        # Add extra variables
        if extra_vars:
            variables.update(extra_vars)

        return variables

    def _format_progress_summary(self, progress: dict[str, Any]) -> str:
        """Format progress summary as HTML.

        Args:
            progress: The progress summary dictionary.

        Returns:
            Formatted HTML string.
        """
        if not progress:
            return "<p>暂无进度信息</p>"

        current_phase = progress.get("current_phase", "unknown")
        completed_steps = progress.get("completed_steps", [])
        pending_items = progress.get("pending_items", [])
        duration = progress.get("duration_minutes", 0)

        html = f"<p><strong>当前阶段:</strong> {current_phase}</p>"
        html += f"<p><strong>已处理时长:</strong> {duration} 分钟</p>"

        if completed_steps:
            html += "<p><strong>已完成步骤:</strong></p><ul>"
            for step in completed_steps:
                html += f"<li>{step}</li>"
            html += "</ul>"

        if pending_items:
            html += "<p><strong>待处理事项:</strong></p><ul>"
            for item in pending_items:
                html += f"<li>{item}</li>"
            html += "</ul>"

        return html

    async def send_session_created_email(
        self,
        session: CollaborationSession,
        recipients: list[str] | None = None,
        template_id: str = "session_created",
    ) -> EmailSendResult:
        """Send email notification when a collaboration session is created.

        Args:
            session: The newly created collaboration session.
            recipients: Optional list of email recipients. If None, uses
                recipients from session config (including user groups).
            template_id: The email template ID to use.

        Returns:
            EmailSendResult with the send status.

        Requirements:
            - 8.1: Support automatic email notification when session is created
            - 8.2: Support configuring recipients from scenario config and user groups
        """
        config = session.config_snapshot or {}

        # Get recipients from config if not provided
        if recipients is None:
            recipients = await self.get_recipients_for_session(session)

        if not recipients:
            logger.info(
                "No email recipients for session %s, skipping email notification",
                session.id,
            )
            return EmailSendResult(
                success=True,
                status=EmailSendStatus.SENT,
                recipients=[],
                subject="",
                error="No recipients configured",
            )

        # Get template
        template = self.get_template(template_id)
        if template is None:
            template = BUILTIN_TEMPLATES["session_created"]

        # Build variables and render template
        variables = self.build_session_variables(session, config)
        subject, body = self.render_template(template, variables)

        # Send email
        return await self._send_email(
            session=session,
            recipients=recipients,
            subject=subject,
            body=body,
            email_type="session_created",
        )

    async def send_status_change_email(
        self,
        session: CollaborationSession,
        old_status: str,
        new_status: str,
        recipients: list[str] | None = None,
    ) -> EmailSendResult:
        """Send email notification when session status changes.

        Args:
            session: The collaboration session.
            old_status: The previous status.
            new_status: The new status.
            recipients: Optional list of email recipients. If None, uses
                recipients from session config (including user groups).

        Returns:
            EmailSendResult with the send status.

        Requirements:
            - 8.5: Send status update emails when session status changes
        """
        config = session.config_snapshot or {}

        # Get recipients from config if not provided
        if recipients is None:
            recipients = await self.get_recipients_for_session(session)

        if not recipients:
            return EmailSendResult(
                success=True,
                status=EmailSendStatus.SENT,
                recipients=[],
                subject="",
                error="No recipients configured",
            )

        # Select appropriate template based on new status
        if new_status == "resolved":
            template_id = "session_resolved"
        elif new_status == "closed":
            template_id = "session_closed"
        else:
            template_id = "session_status_changed"

        template = self.get_template(template_id)
        if template is None:
            template = BUILTIN_TEMPLATES["session_status_changed"]

        # Build variables with status change info
        extra_vars = {
            "old_status": STATUS_DISPLAY_NAMES.get(old_status, old_status),
            "new_status": STATUS_DISPLAY_NAMES.get(new_status, new_status),
            "changed_at": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            "status_details": self._build_status_details(session, new_status),
        }
        variables = self.build_session_variables(session, config, extra_vars)
        subject, body = self.render_template(template, variables)

        # Send email
        return await self._send_email(
            session=session,
            recipients=recipients,
            subject=subject,
            body=body,
            email_type=f"status_change_{new_status}",
        )

    def _build_status_details(
        self,
        session: CollaborationSession,
        new_status: str,
    ) -> str:
        """Build status-specific details HTML.

        Args:
            session: The collaboration session.
            new_status: The new status.

        Returns:
            HTML string with status-specific details.
        """
        if new_status == "active":
            return "<p>协同会话已激活，请关注处理进展。</p>"
        elif new_status == "resolved":
            return "<p>问题已解决，等待确认关闭。</p>"
        elif new_status == "closed":
            return "<p>协同会话已关闭。</p>"
        else:
            return ""

    async def _send_email(
        self,
        session: CollaborationSession,
        recipients: list[str],
        subject: str,
        body: str,
        email_type: str,
    ) -> EmailSendResult:
        """Send an email using the email channel.

        Handles the actual email sending with retry support.

        Args:
            session: The collaboration session (for context).
            recipients: List of email recipients.
            subject: Email subject.
            body: Email body (HTML).
            email_type: Type of email for logging.

        Returns:
            EmailSendResult with the send status.

        Requirements:
            - 8.6: Record email send status and timestamp
            - 8.7: Support retry on send failure
        """
        result = EmailSendResult(
            success=False,
            status=EmailSendStatus.PENDING,
            recipients=recipients,
            subject=subject,
        )

        # Get email channel configuration
        email_channel = await self._get_email_channel_for_scenario(session.scenario_id)
        if email_channel is None:
            logger.warning(
                "No email channel configured for scenario %s",
                session.scenario_id,
            )
            result.status = EmailSendStatus.FAILED
            result.error = "No email channel configured for this scenario"
            return result

        channel_config = email_channel.config or {}

        # Build notification payload
        payload = NotificationPayload(
            title=subject,
            message=body,
            severity="warning",
            recipients=recipients,
            metadata={
                "type": email_type,
                "session_id": str(session.id),
                "scenario_id": str(session.scenario_id),
            },
        )

        # Attempt to send with retries
        email_handler = channel_manager.get("email")
        if email_handler is None:
            result.status = EmailSendStatus.FAILED
            result.error = "Email channel handler not available"
            return result

        last_error: str | None = None
        for attempt in range(self._max_retries):
            result.retry_count = attempt
            result.status = EmailSendStatus.RETRYING if attempt > 0 else EmailSendStatus.PENDING

            try:
                success = await email_handler.send(channel_config, payload)
                if success:
                    result.success = True
                    result.status = EmailSendStatus.SENT
                    result.sent_at = datetime.now(UTC)
                    result.message_id = f"collab_{session.id.hex[:8]}_{email_type}_{attempt}"

                    logger.info(
                        "Email sent for session %s (type=%s, recipients=%d, attempt=%d)",
                        session.id,
                        email_type,
                        len(recipients),
                        attempt + 1,
                    )
                    return result
                else:
                    last_error = "Email send returned False"

            except Exception as exc:
                last_error = str(exc)
                logger.warning(
                    "Email send attempt %d failed for session %s: %s",
                    attempt + 1,
                    session.id,
                    exc,
                )

        # All retries exhausted
        result.status = EmailSendStatus.FAILED
        result.error = last_error or "Unknown error after all retries"

        logger.error(
            "Email send failed after %d attempts for session %s (type=%s): %s",
            self._max_retries,
            session.id,
            email_type,
            result.error,
        )

        return result

    async def _get_email_channel_for_scenario(
        self,
        scenario_id: uuid.UUID,
    ) -> NotificationChannel | None:
        """Get the email notification channel for a scenario.

        Looks up the email channel associated with the scenario, or falls
        back to any active email channel if none is specifically associated.

        Args:
            scenario_id: The scenario ID.

        Returns:
            The NotificationChannel if found, None otherwise.

        Requirements:
            - 8.2: Support configuring recipients from scenario config
        """
        # First, try to get channels associated with the scenario
        scenario_result = await self._db.execute(
            select(Scenario)
            .options(selectinload(Scenario.notification_channels))
            .where(Scenario.id == scenario_id)
        )
        scenario = scenario_result.scalar_one_or_none()

        if scenario and scenario.notification_channels:
            # Look for an email channel in the associated channels
            for channel in scenario.notification_channels:
                if channel.channel_type == "email" and channel.is_active:
                    return channel

        # Fall back to any active email channel
        result = await self._db.execute(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == "email",
                NotificationChannel.is_active == True,  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def get_recipients_for_session(
        self,
        session: CollaborationSession,
        include_user_groups: bool = True,
    ) -> list[str]:
        """Get all email recipients for a collaboration session.

        Collects recipients from:
        - Session config snapshot (email_recipients)
        - Associated user groups/roles (if include_user_groups is True)
        - Individual user IDs

        Args:
            session: The collaboration session.
            include_user_groups: Whether to include recipients from user groups.

        Returns:
            List of unique email addresses.

        Requirements:
            - 8.2: Support configuring recipients from scenario config and user groups
        """
        recipients: set[str] = set()

        # Get recipient configuration from config snapshot
        config = session.config_snapshot or {}
        recipient_config = RecipientConfig.from_config(config)

        # Add direct email addresses
        for email in recipient_config.email_addresses:
            if self._is_valid_email(email):
                recipients.add(email.lower().strip())

        # Add recipients from user groups (roles)
        if include_user_groups and recipient_config.user_group_ids:
            group_emails = await self._get_emails_from_user_groups(
                recipient_config.user_group_ids
            )
            recipients.update(group_emails)

        # Add recipients from individual user IDs
        if recipient_config.user_ids:
            user_emails = await self._get_emails_from_user_ids(
                recipient_config.user_ids
            )
            recipients.update(user_emails)

        return list(recipients)

    async def _get_emails_from_user_groups(
        self,
        group_ids: list[str],
    ) -> set[str]:
        """Get email addresses from user groups (roles).

        Looks up all users in the specified roles and collects their
        email addresses.

        Args:
            group_ids: List of role UUIDs as strings.

        Returns:
            Set of email addresses from users in the specified groups.

        Requirements:
            - 8.2: Support configuring recipients from user groups
        """
        emails: set[str] = set()

        if not group_ids:
            return emails

        # Convert string IDs to UUIDs, skipping invalid ones
        valid_group_ids: list[uuid.UUID] = []
        for gid in group_ids:
            try:
                valid_group_ids.append(uuid.UUID(gid))
            except (ValueError, TypeError):
                logger.warning("Invalid user group ID: %s", gid)
                continue

        if not valid_group_ids:
            return emails

        # Query roles with their users
        result = await self._db.execute(
            select(Role)
            .options(selectinload(Role.users))
            .where(Role.id.in_(valid_group_ids))
        )
        roles = result.scalars().all()

        for role in roles:
            for user in role.users:
                if user.is_active and user.email:
                    emails.add(user.email.lower().strip())

        logger.debug(
            "Retrieved %d email addresses from %d user groups",
            len(emails),
            len(valid_group_ids),
        )

        return emails

    async def _get_emails_from_user_ids(
        self,
        user_ids: list[str],
    ) -> set[str]:
        """Get email addresses from individual user IDs.

        Args:
            user_ids: List of user UUIDs as strings.

        Returns:
            Set of email addresses for the specified users.

        Requirements:
            - 8.2: Support configuring recipients from scenario config
        """
        emails: set[str] = set()

        if not user_ids:
            return emails

        # Convert string IDs to UUIDs, skipping invalid ones
        valid_user_ids: list[uuid.UUID] = []
        for uid in user_ids:
            try:
                valid_user_ids.append(uuid.UUID(uid))
            except (ValueError, TypeError):
                logger.warning("Invalid user ID: %s", uid)
                continue

        if not valid_user_ids:
            return emails

        # Query users
        result = await self._db.execute(
            select(User).where(
                User.id.in_(valid_user_ids),
                User.is_active == True,  # noqa: E712
            )
        )
        users = result.scalars().all()

        for user in users:
            if user.email:
                emails.add(user.email.lower().strip())

        logger.debug(
            "Retrieved %d email addresses from %d user IDs",
            len(emails),
            len(valid_user_ids),
        )

        return emails

    def _is_valid_email(self, email: str) -> bool:
        """Validate an email address format.

        Uses a simple regex pattern for basic validation.

        Args:
            email: The email address to validate.

        Returns:
            True if the email format is valid, False otherwise.
        """
        if not email or not isinstance(email, str):
            return False

        # Simple email validation pattern
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return bool(re.match(pattern, email.strip()))

    async def send_custom_email(
        self,
        session: CollaborationSession,
        subject_template: str,
        body_template: str,
        recipients: list[str] | None = None,
        extra_variables: dict[str, Any] | None = None,
        email_type: str = "custom",
    ) -> EmailSendResult:
        """Send a custom email with user-defined subject and body templates.

        This method allows sending emails with custom templates that are not
        part of the built-in template set. The templates support the same
        variable substitution as built-in templates.

        Args:
            session: The collaboration session for context.
            subject_template: Subject line template with {variable} placeholders.
            body_template: HTML body template with {variable} placeholders.
            recipients: Optional list of email recipients. If None, uses
                recipients from session config (including user groups).
            extra_variables: Optional additional variables for template substitution.
            email_type: Type identifier for logging and tracking.

        Returns:
            EmailSendResult with the send status.

        Example:
            result = await service.send_custom_email(
                session=session,
                subject_template="[Alert] {scenario_name} - Custom Notification",
                body_template="<h1>Alert for {scenario_name}</h1><p>{custom_message}</p>",
                extra_variables={"custom_message": "This is a custom alert"},
            )

        Requirements:
            - 8.3: Support email templates with subject and body
            - 8.4: Support template variable substitution
        """
        # Get recipients from config if not provided
        if recipients is None:
            recipients = await self.get_recipients_for_session(session)

        if not recipients:
            logger.info(
                "No email recipients for session %s, skipping custom email",
                session.id,
            )
            return EmailSendResult(
                success=True,
                status=EmailSendStatus.SENT,
                recipients=[],
                subject="",
                error="No recipients configured",
            )

        # Create a temporary template
        custom_template = EmailTemplate(
            id=f"custom_{email_type}",
            name=f"Custom Template ({email_type})",
            subject_template=subject_template,
            body_template=body_template,
        )

        # Build variables and render template
        config = session.config_snapshot or {}
        variables = self.build_session_variables(session, config, extra_variables)
        subject, body = self.render_template(custom_template, variables)

        # Send email
        return await self._send_email(
            session=session,
            recipients=recipients,
            subject=subject,
            body=body,
            email_type=email_type,
        )

    async def get_recipient_config_for_scenario(
        self,
        scenario_id: uuid.UUID,
    ) -> RecipientConfig:
        """Get the recipient configuration for a scenario.

        Retrieves the email recipient configuration from the scenario's
        collaboration_config.

        Args:
            scenario_id: The scenario ID.

        Returns:
            RecipientConfig with the scenario's email recipient settings.

        Requirements:
            - 8.2: Support configuring recipients from scenario config
        """
        scenario_result = await self._db.execute(
            select(Scenario).where(Scenario.id == scenario_id)
        )
        scenario = scenario_result.scalar_one_or_none()

        if scenario is None:
            return RecipientConfig()

        collab_config = scenario.collaboration_config or {}
        return RecipientConfig.from_config(collab_config)

    async def resolve_recipients(
        self,
        recipient_config: RecipientConfig,
    ) -> list[str]:
        """Resolve a RecipientConfig to a list of email addresses.

        Takes a RecipientConfig and resolves all sources (direct emails,
        user groups, user IDs) to a deduplicated list of email addresses.

        Args:
            recipient_config: The recipient configuration to resolve.

        Returns:
            List of unique email addresses.

        Requirements:
            - 8.2: Support configuring recipients from scenario config and user groups
        """
        recipients: set[str] = set()

        # Add direct email addresses
        for email in recipient_config.email_addresses:
            if self._is_valid_email(email):
                recipients.add(email.lower().strip())

        # Add recipients from user groups (roles)
        if recipient_config.user_group_ids:
            group_emails = await self._get_emails_from_user_groups(
                recipient_config.user_group_ids
            )
            recipients.update(group_emails)

        # Add recipients from individual user IDs
        if recipient_config.user_ids:
            user_emails = await self._get_emails_from_user_ids(
                recipient_config.user_ids
            )
            recipients.update(user_emails)

        return list(recipients)
