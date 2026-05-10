"""Collaboration service — manages emergency collaboration sessions.

This module provides the core service for managing emergency collaboration
sessions, including:
- Session creation with unique identifiers
- Recording creation time, trigger scenario, and trigger reason
- Configuration snapshot at session creation
- State management with proper state transitions
- Manual close functionality
- Summary report generation
- Initialization actions (group chat creation, email sending)
- Status change email notifications

Requirements:
- 6.2: Auto-create collaboration session when scenario with collaboration enabled is triggered
- 6.3: Generate unique identifier for collaboration session
- 6.4: Record creation time, trigger scenario, trigger reason
- 6.5: Support state transitions: created → active → resolved → closed
- 6.6: Execute initialization actions based on scenario configuration
- 6.7: Support manual close of collaboration session
- 6.8: Generate summary report when session is closed
- 8.5: Send status update emails when collaboration session status changes
- 8.6: Record email send status and timestamp
- 8.7: Support retry on send failure
"""

import logging
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
from src.models.collaboration import (
    CollaborationSession,
)
from src.schemas.collaboration import (
    CollaborationSessionCreate,
)
from src.services.channel_manager import channel_manager
from src.services.channels.base import NotificationPayload
from src.services.email_notification import EmailNotificationService, EmailSendResult

logger = logging.getLogger(__name__)


@dataclass
class InitializationActionResult:
    """Result of a single initialization action.

    Attributes:
        action_type: Type of action (e.g., 'group_chat', 'email').
        success: Whether the action completed successfully.
        details: Additional details about the action result.
        error: Error message if the action failed.
        timestamp: When the action was executed.
    """

    action_type: str
    success: bool
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class InitializationResult:
    """Result of all initialization actions for a collaboration session.

    Attributes:
        session_id: The collaboration session ID.
        actions: List of individual action results.
        all_success: Whether all actions completed successfully.
        group_chat_id: The created group chat ID, if any.
        emails_sent: Number of emails successfully sent.
    """

    session_id: uuid.UUID
    actions: list[InitializationActionResult] = field(default_factory=list)
    all_success: bool = True
    group_chat_id: str | None = None
    emails_sent: int = 0

    def add_action(self, result: InitializationActionResult) -> None:
        """Add an action result and update overall success status."""
        self.actions.append(result)
        if not result.success:
            self.all_success = False


class SessionStatus(str, Enum):  # noqa: UP042
    """Collaboration session status values.

    Defines the valid states for a collaboration session and their
    allowed transitions.

    State Machine:
        created → active → resolved → closed
                    ↓
                  closed (manual close)

    Note: Using str + Enum for JSON serialization compatibility.
    Consider migrating to StrEnum when Python 3.11+ is the minimum version.

    Requirements:
        - 6.5: Support state transitions: created → active → resolved → closed
    """

    CREATED = "created"
    ACTIVE = "active"
    RESOLVED = "resolved"
    CLOSED = "closed"


# Valid state transitions mapping
VALID_TRANSITIONS: dict[SessionStatus, set[SessionStatus]] = {
    SessionStatus.CREATED: {SessionStatus.ACTIVE, SessionStatus.CLOSED},
    SessionStatus.ACTIVE: {SessionStatus.RESOLVED, SessionStatus.CLOSED},
    SessionStatus.RESOLVED: {SessionStatus.CLOSED},
    SessionStatus.CLOSED: set(),  # Terminal state, no transitions allowed
}


class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, current_status: str, target_status: str) -> None:
        self.current_status = current_status
        self.target_status = target_status
        super().__init__(
            f"Invalid state transition from '{current_status}' to '{target_status}'"
        )


class CollaborationService:
    """Service for managing emergency collaboration sessions.

    This service handles the creation and management of collaboration sessions
    that are triggered when scenarios with collaboration enabled are executed.

    Features:
        - Session creation with unique identifiers
        - State management with proper transitions (created → active → resolved → closed)
        - Manual close functionality
        - Summary report generation
        - Status change email notifications with retry support

    Requirements:
        - 6.2: Auto-create collaboration session when scenario triggered
        - 6.3: Generate unique identifier for collaboration session
        - 6.4: Record creation time, trigger scenario, trigger reason
        - 6.5: Support state transitions: created → active → resolved → closed
        - 6.7: Support manual close of collaboration session
        - 6.8: Generate summary report when session is closed
        - 8.5: Send status update emails when session status changes
        - 8.6: Record email send status and timestamp
        - 8.7: Support retry on send failure
    """

    def __init__(
        self,
        db: AsyncSession,
        send_status_emails: bool = True,
    ) -> None:
        """Initialize the collaboration service.

        Args:
            db: Async database session for persistence operations.
            send_status_emails: Whether to send email notifications on status changes.
                Defaults to True. Set to False for testing or when email is not needed.
        """
        self._db = db
        self._send_status_emails = send_status_emails
        self._email_service: EmailNotificationService | None = None

    def _get_email_service(self) -> EmailNotificationService:
        """Get or create the email notification service.

        Lazily initializes the email service to avoid circular dependencies
        and allow for testing without email functionality.

        Returns:
            The EmailNotificationService instance.
        """
        if self._email_service is None:
            self._email_service = EmailNotificationService(self._db)
        return self._email_service

    async def _send_status_change_notification(
        self,
        session: CollaborationSession,
        old_status: str,
        new_status: str,
    ) -> EmailSendResult | None:
        """Send email notification for status change.

        Sends an email notification when the collaboration session status
        changes. The email includes details about the status change and
        is sent to configured recipients.

        Args:
            session: The collaboration session.
            old_status: The previous status.
            new_status: The new status.

        Returns:
            EmailSendResult if email was sent, None if email sending is disabled
            or no recipients are configured.

        Requirements:
            - 8.5: Send status update emails when session status changes
            - 8.6: Record email send status and timestamp
            - 8.7: Support retry on send failure
        """
        if not self._send_status_emails:
            logger.debug(
                "Status email notifications disabled, skipping for session %s",
                session.id,
            )
            return None

        # Check if email notifications are configured for this session
        config = session.config_snapshot or {}
        if not config.get("send_email", False):
            logger.debug(
                "Email notifications not configured for session %s, skipping",
                session.id,
            )
            return None

        try:
            email_service = self._get_email_service()
            result = await email_service.send_status_change_email(
                session=session,
                old_status=old_status,
                new_status=new_status,
            )

            # Log the result for tracking (Requirement 8.6)
            if result.success:
                logger.info(
                    "Status change email sent for session %s: %s -> %s "
                    "(recipients=%d, sent_at=%s, message_id=%s)",
                    session.id,
                    old_status,
                    new_status,
                    len(result.recipients),
                    result.sent_at.isoformat() if result.sent_at else "N/A",
                    result.message_id,
                )
            else:
                logger.warning(
                    "Status change email failed for session %s: %s -> %s "
                    "(error=%s, retry_count=%d)",
                    session.id,
                    old_status,
                    new_status,
                    result.error,
                    result.retry_count,
                )

            return result

        except Exception as exc:
            logger.exception(
                "Error sending status change email for session %s: %s",
                session.id,
                exc,
            )
            return EmailSendResult(
                success=False,
                status="failed",
                error=str(exc),
            )

    async def create_session(
        self,
        scenario_id: uuid.UUID,
        trigger_reason: str | None = None,
        space_id: uuid.UUID | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> CollaborationSession:
        """Create a new collaboration session.

        Creates a collaboration session with a unique identifier, recording
        the creation time, trigger scenario, and trigger reason. The session
        is initialized with 'created' status.

        Args:
            scenario_id: ID of the scenario that triggered this session.
            trigger_reason: Description of why the session was triggered.
            space_id: Optional workspace scope for the session.
            config_snapshot: Optional snapshot of collaboration config at creation.

        Returns:
            The newly created CollaborationSession.

        Raises:
            ValueError: If the scenario_id is invalid or scenario not found.

        Requirements:
            - 6.2: Auto-create collaboration session when scenario triggered
            - 6.3: Generate unique identifier (UUID)
            - 6.4: Record creation time, trigger scenario, trigger reason
        """
        # Validate scenario exists
        scenario = await self._get_scenario(scenario_id)
        if scenario is None:
            raise ValueError(f"Scenario with ID '{scenario_id}' not found")

        # Generate unique identifier (Requirement 6.3)
        session_id = uuid.uuid4()

        # Build config snapshot from scenario if not provided
        if config_snapshot is None:
            config_snapshot = self._build_config_snapshot(scenario)

        # Create the collaboration session (Requirements 6.2, 6.4)
        session = CollaborationSession(
            id=session_id,
            scenario_id=scenario_id,
            status="created",
            trigger_reason=trigger_reason,
            config_snapshot=config_snapshot,
            space_id=space_id or scenario.space_id,
            progress_summary={
                "current_phase": "created",
                "completed_steps": [],
                "pending_items": ["初始化协同会话", "创建群聊", "发送通知"],
                "duration_minutes": 0,
                "last_analysis_at": None,
            },
        )

        self._db.add(session)
        await self._db.flush()
        await self._db.refresh(session)

        logger.info(
            "Created collaboration session %s for scenario %s (trigger_reason: %s)",
            session_id,
            scenario_id,
            trigger_reason[:100] if trigger_reason else "N/A",
        )

        return session

    async def create_session_from_request(
        self,
        request: CollaborationSessionCreate,
    ) -> CollaborationSession:
        """Create a collaboration session from a request schema.

        This is a convenience method that accepts a Pydantic schema and
        delegates to the main create_session method.

        Args:
            request: The session creation request.

        Returns:
            The newly created CollaborationSession.

        Requirements:
            - 6.2: Auto-create collaboration session when scenario triggered
            - 6.3: Generate unique identifier
            - 6.4: Record creation time, trigger scenario, trigger reason
        """
        scenario_id = uuid.UUID(request.scenario_id)
        space_id = uuid.UUID(request.space_id) if request.space_id else None

        return await self.create_session(
            scenario_id=scenario_id,
            trigger_reason=request.trigger_reason,
            space_id=space_id,
        )

    async def get_session(
        self,
        session_id: uuid.UUID,
        include_messages: bool = False,
        include_recommendations: bool = False,
    ) -> CollaborationSession | None:
        """Get a collaboration session by ID.

        Args:
            session_id: The unique identifier of the session.
            include_messages: Whether to eagerly load messages.
            include_recommendations: Whether to eagerly load recommendations.

        Returns:
            The CollaborationSession if found, None otherwise.
        """
        query = select(CollaborationSession).where(CollaborationSession.id == session_id)

        # Build eager loading options
        options = []
        if include_messages:
            options.append(selectinload(CollaborationSession.messages))
        if include_recommendations:
            options.append(selectinload(CollaborationSession.recommendations))

        if options:
            query = query.options(*options)

        result = await self._db.execute(query)
        return result.scalar_one_or_none()

    async def get_session_by_scenario(
        self,
        scenario_id: uuid.UUID,
        status: str | None = None,
    ) -> list[CollaborationSession]:
        """Get collaboration sessions for a scenario.

        Args:
            scenario_id: The scenario ID to filter by.
            status: Optional status filter.

        Returns:
            List of matching CollaborationSessions.
        """
        query = select(CollaborationSession).where(
            CollaborationSession.scenario_id == scenario_id
        )

        if status:
            query = query.where(CollaborationSession.status == status)

        query = query.order_by(CollaborationSession.created_at.desc())

        result = await self._db.execute(query)
        return list(result.scalars().all())

    async def _get_scenario(self, scenario_id: uuid.UUID) -> Scenario | None:
        """Get a scenario by ID.

        Args:
            scenario_id: The scenario ID to look up.

        Returns:
            The Scenario if found, None otherwise.
        """
        result = await self._db.execute(
            select(Scenario).where(Scenario.id == scenario_id)
        )
        return result.scalar_one_or_none()

    def _build_config_snapshot(self, scenario: Scenario) -> dict[str, Any]:
        """Build a configuration snapshot from the scenario.

        Creates a snapshot of the collaboration configuration at the time
        of session creation. This preserves the configuration even if the
        scenario is later modified.

        Args:
            scenario: The scenario to snapshot configuration from.

        Returns:
            Dictionary containing the configuration snapshot.
        """
        # Get collaboration config from scenario
        collab_config = scenario.collaboration_config or {}

        return {
            "scenario_name": scenario.name,
            "scenario_type": scenario.scenario_type,
            "auto_create_group": collab_config.get("auto_create_group", False),
            "group_name_template": collab_config.get(
                "group_name_template",
                "[应急] {scenario_name} - {timestamp}",
            ),
            "group_members": collab_config.get("group_members", []),
            "group_owner": collab_config.get("group_owner"),
            "send_email": collab_config.get("send_email", False),
            "email_recipients": collab_config.get("email_recipients", []),
            "email_template_id": collab_config.get("email_template_id"),
            "snapshot_at": datetime.now(UTC).isoformat(),
        }

    def generate_group_name(
        self,
        template: str,
        scenario_name: str,
        trigger_reason: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a group chat name from a template.

        Substitutes variables in the template with actual values.
        Supported variables:
        - {scenario_name}: Name of the scenario
        - {timestamp}: Current timestamp in YYYY-MM-DD HH:MM format
        - {trigger_reason}: The trigger reason (truncated if too long)
        - Any additional kwargs

        Args:
            template: The name template string.
            scenario_name: Name of the triggering scenario.
            trigger_reason: Optional trigger reason.
            **kwargs: Additional variables for substitution.

        Returns:
            The generated group name.
        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")

        # Truncate trigger reason if too long
        reason_display = ""
        if trigger_reason:
            if len(trigger_reason) > 50:
                reason_display = trigger_reason[:50] + "..."
            else:
                reason_display = trigger_reason

        # Build substitution dict
        substitutions = {
            "scenario_name": scenario_name,
            "timestamp": timestamp,
            "trigger_reason": reason_display,
            **kwargs,
        }

        # Perform substitution
        result = template
        for key, value in substitutions.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))

        return result

    async def execute_initialization_actions(
        self,
        session: CollaborationSession,
    ) -> InitializationResult:
        """Execute initialization actions based on scenario configuration.

        This method executes all configured initialization actions when a
        collaboration session is created, including:
        - Creating a group chat (if auto_create_group is enabled)
        - Sending email notifications (if send_email is enabled)

        Args:
            session: The collaboration session to initialize.

        Returns:
            InitializationResult containing the results of all actions.

        Requirements:
            - 6.6: Execute initialization actions based on scenario configuration
        """
        result = InitializationResult(session_id=session.id)
        config = session.config_snapshot or {}

        logger.info(
            "Executing initialization actions for session %s "
            "(config: auto_create_group=%s, send_email=%s)",
            session.id,
            config.get("auto_create_group", False),
            config.get("send_email", False),
        )

        # Execute group chat creation if configured
        if config.get("auto_create_group", False):
            group_result = await self._create_group_chat(session, config)
            result.add_action(group_result)
            if group_result.success and group_result.details.get("group_chat_id"):
                result.group_chat_id = group_result.details["group_chat_id"]

        # Execute email notification if configured
        if config.get("send_email", False):
            email_result = await self._send_initialization_emails(session, config)
            result.add_action(email_result)
            if email_result.success:
                result.emails_sent = email_result.details.get("emails_sent", 0)

        # Update session progress summary
        await self._update_initialization_progress(session, result)

        logger.info(
            "Initialization actions completed for session %s: "
            "all_success=%s, group_chat_id=%s, emails_sent=%d",
            session.id,
            result.all_success,
            result.group_chat_id,
            result.emails_sent,
        )

        return result

    async def _create_group_chat(
        self,
        session: CollaborationSession,
        config: dict[str, Any],
    ) -> InitializationActionResult:
        """Create a group chat for the collaboration session.

        Integrates with the WeCom channel to create a group chat based on
        the scenario configuration.

        Args:
            session: The collaboration session.
            config: The configuration snapshot.

        Returns:
            InitializationActionResult for the group chat creation.

        Requirements:
            - 6.6: Execute initialization actions (group chat creation)
        """
        try:
            # Generate group name from template
            group_name_template = config.get(
                "group_name_template",
                "[应急] {scenario_name} - {timestamp}",
            )
            scenario_name = config.get("scenario_name", "未知场景")
            group_name = self.generate_group_name(
                template=group_name_template,
                scenario_name=scenario_name,
                trigger_reason=session.trigger_reason,
            )

            # Get WeCom channel configuration
            wecom_channel = await self._get_wecom_channel_for_scenario(session.scenario_id)
            if wecom_channel is None:
                logger.warning(
                    "No WeCom channel configured for scenario %s, skipping group chat creation",
                    session.scenario_id,
                )
                return InitializationActionResult(
                    action_type="group_chat",
                    success=False,
                    error="No WeCom channel configured for this scenario",
                    details={"scenario_id": str(session.scenario_id)},
                )

            # Get group members from config
            group_members = config.get("group_members", [])
            group_owner = config.get("group_owner")

            # Create group chat via WeCom API
            # Note: The actual group creation depends on the WeCom channel implementation
            # For now, we'll use the channel's send capability to notify about the session
            # and record the intent to create a group
            channel_config = wecom_channel.config or {}

            # Build notification payload for group creation
            payload = NotificationPayload(
                title=f"应急协同会话已创建: {group_name}",
                message=(
                    f"场景: {scenario_name}\n"
                    f"触发原因: {session.trigger_reason or '未指定'}\n"
                    f"会话ID: {session.id}\n"
                    f"创建时间: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S')}"
                ),
                severity="warning",
                metadata={
                    "type": "collaboration_session_created",
                    "session_id": str(session.id),
                    "scenario_id": str(session.scenario_id),
                    "group_name": group_name,
                    "group_members": group_members,
                    "group_owner": group_owner,
                },
            )

            # Send notification via WeCom channel
            wecom_handler = channel_manager.get("wecom")
            if wecom_handler:
                success = await wecom_handler.send(channel_config, payload)
            else:
                success = False

            if success:
                # For now, we use a placeholder group_chat_id
                # In a full implementation, this would be returned by the WeCom API
                # when creating an actual group chat
                group_chat_id = f"collab_{session.id.hex[:12]}"

                # Update session with group chat info
                session.group_chat_id = group_chat_id
                session.group_chat_name = group_name
                await self._db.flush()

                return InitializationActionResult(
                    action_type="group_chat",
                    success=True,
                    details={
                        "group_chat_id": group_chat_id,
                        "group_name": group_name,
                        "members_count": len(group_members),
                        "channel_name": wecom_channel.name,
                    },
                )
            else:
                return InitializationActionResult(
                    action_type="group_chat",
                    success=False,
                    error="Failed to send group creation notification via WeCom",
                    details={
                        "channel_name": wecom_channel.name,
                        "group_name": group_name,
                    },
                )

        except Exception as exc:
            logger.exception(
                "Error creating group chat for session %s: %s",
                session.id,
                exc,
            )
            return InitializationActionResult(
                action_type="group_chat",
                success=False,
                error=str(exc),
            )

    async def _send_initialization_emails(
        self,
        session: CollaborationSession,
        config: dict[str, Any],
    ) -> InitializationActionResult:
        """Send initialization email notifications.

        Sends email notifications to configured recipients when a
        collaboration session is created.

        Args:
            session: The collaboration session.
            config: The configuration snapshot.

        Returns:
            InitializationActionResult for the email sending.

        Requirements:
            - 6.6: Execute initialization actions (email sending)
        """
        try:
            # Get email recipients from config
            email_recipients = config.get("email_recipients", [])
            if not email_recipients:
                logger.info(
                    "No email recipients configured for session %s, skipping email notification",
                    session.id,
                )
                return InitializationActionResult(
                    action_type="email",
                    success=True,
                    details={"emails_sent": 0, "reason": "No recipients configured"},
                )

            # Get email channel configuration
            email_channel = await self._get_email_channel_for_scenario(session.scenario_id)
            if email_channel is None:
                logger.warning(
                    "No email channel configured for scenario %s, skipping email notification",
                    session.scenario_id,
                )
                return InitializationActionResult(
                    action_type="email",
                    success=False,
                    error="No email channel configured for this scenario",
                    details={"scenario_id": str(session.scenario_id)},
                )

            # Build email content
            scenario_name = config.get("scenario_name", "未知场景")
            email_template_id = config.get("email_template_id")

            # Build email payload
            email_title = f"[应急协同] {scenario_name} - 协同会话已创建"
            email_message = self._build_email_content(session, config)

            payload = NotificationPayload(
                title=email_title,
                message=email_message,
                severity="warning",
                recipients=email_recipients,
                metadata={
                    "type": "collaboration_session_created",
                    "session_id": str(session.id),
                    "scenario_id": str(session.scenario_id),
                    "template_id": email_template_id,
                },
            )

            # Send email via email channel
            channel_config = email_channel.config or {}
            email_handler = channel_manager.get("email")
            if email_handler:
                try:
                    success = await email_handler.send(channel_config, payload)
                except Exception as send_exc:
                    logger.warning(
                        "Email send raised exception for session %s: %s",
                        session.id,
                        send_exc,
                    )
                    success = False
            else:
                success = False

            if success:
                return InitializationActionResult(
                    action_type="email",
                    success=True,
                    details={
                        "emails_sent": len(email_recipients),
                        "recipients": email_recipients,
                        "channel_name": email_channel.name,
                    },
                )
            else:
                return InitializationActionResult(
                    action_type="email",
                    success=False,
                    error="Failed to send email notification",
                    details={
                        "channel_name": email_channel.name,
                        "recipients": email_recipients,
                    },
                )

        except Exception as exc:
            logger.exception(
                "Error sending initialization emails for session %s: %s",
                session.id,
                exc,
            )
            return InitializationActionResult(
                action_type="email",
                success=False,
                error=str(exc),
            )

    def _build_email_content(
        self,
        session: CollaborationSession,
        config: dict[str, Any],
    ) -> str:
        """Build the email content for initialization notification.

        Args:
            session: The collaboration session.
            config: The configuration snapshot.

        Returns:
            The formatted email content as HTML.
        """
        scenario_name = config.get("scenario_name", "未知场景")
        scenario_type = config.get("scenario_type", "unknown")
        trigger_reason = session.trigger_reason or "未指定"
        if session.created_at:
            created_at = session.created_at.strftime("%Y-%m-%d %H:%M:%S")
        else:
            created_at = "N/A"

        return f"""
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
        <td style="padding: 8px; border: 1px solid #ddd;">{session.id}</td>
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
"""

    async def _get_wecom_channel_for_scenario(
        self,
        scenario_id: uuid.UUID,
    ) -> NotificationChannel | None:
        """Get the WeCom notification channel for a scenario.

        Looks up the WeCom channel associated with the scenario, or falls
        back to any active WeCom channel if none is specifically associated.

        Args:
            scenario_id: The scenario ID.

        Returns:
            The NotificationChannel if found, None otherwise.
        """
        # First, try to get channels associated with the scenario
        scenario = await self._db.execute(
            select(Scenario)
            .options(selectinload(Scenario.notification_channels))
            .where(Scenario.id == scenario_id)
        )
        scenario_obj = scenario.scalar_one_or_none()

        if scenario_obj and scenario_obj.notification_channels:
            # Look for a WeCom channel in the associated channels
            for channel in scenario_obj.notification_channels:
                if channel.channel_type == "wecom" and channel.is_active:
                    return channel

        # Fall back to any active WeCom channel
        result = await self._db.execute(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == "wecom",
                NotificationChannel.is_active == True,  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none()

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
        """
        # First, try to get channels associated with the scenario
        scenario = await self._db.execute(
            select(Scenario)
            .options(selectinload(Scenario.notification_channels))
            .where(Scenario.id == scenario_id)
        )
        scenario_obj = scenario.scalar_one_or_none()

        if scenario_obj and scenario_obj.notification_channels:
            # Look for an email channel in the associated channels
            for channel in scenario_obj.notification_channels:
                if channel.channel_type == "email" and channel.is_active:
                    return channel

        # Fall back to any active email channel
        result = await self._db.execute(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == "email",
                NotificationChannel.is_active == True,  # noqa: E712
            ).limit(1)
        )
        return result.scalar_one_or_none()

    async def _update_initialization_progress(
        self,
        session: CollaborationSession,
        result: InitializationResult,
    ) -> None:
        """Update the session's progress summary after initialization.

        Args:
            session: The collaboration session.
            result: The initialization result.
        """
        progress = session.progress_summary or {}
        completed_steps = progress.get("completed_steps", [])
        pending_items = progress.get("pending_items", [])

        # Update completed steps based on results
        if "初始化协同会话" in pending_items:
            pending_items.remove("初始化协同会话")
            completed_steps.append("初始化协同会话")

        for action in result.actions:
            if action.action_type == "group_chat":
                if "创建群聊" in pending_items:
                    pending_items.remove("创建群聊")
                if action.success:
                    completed_steps.append("创建群聊")
                else:
                    completed_steps.append(f"创建群聊 (失败: {action.error})")

            elif action.action_type == "email":
                if "发送通知" in pending_items:
                    pending_items.remove("发送通知")
                if action.success:
                    emails_sent = action.details.get("emails_sent", 0)
                    completed_steps.append(f"发送通知 ({emails_sent}封邮件)")
                else:
                    completed_steps.append(f"发送通知 (失败: {action.error})")

        # Update progress summary
        session.progress_summary = {
            **progress,
            "completed_steps": completed_steps,
            "pending_items": pending_items,
            "initialization_completed_at": datetime.now(UTC).isoformat(),
            "initialization_success": result.all_success,
        }

        await self._db.flush()

    async def create_and_initialize_session(
        self,
        scenario_id: uuid.UUID,
        trigger_reason: str | None = None,
        space_id: uuid.UUID | None = None,
        config_snapshot: dict[str, Any] | None = None,
    ) -> tuple[CollaborationSession, InitializationResult]:
        """Create a collaboration session and execute initialization actions.

        This is a convenience method that combines session creation with
        initialization action execution.

        Args:
            scenario_id: ID of the scenario that triggered this session.
            trigger_reason: Description of why the session was triggered.
            space_id: Optional workspace scope for the session.
            config_snapshot: Optional snapshot of collaboration config at creation.

        Returns:
            A tuple of (CollaborationSession, InitializationResult).

        Requirements:
            - 6.2: Auto-create collaboration session when scenario triggered
            - 6.6: Execute initialization actions based on scenario configuration
        """
        # Create the session
        session = await self.create_session(
            scenario_id=scenario_id,
            trigger_reason=trigger_reason,
            space_id=space_id,
            config_snapshot=config_snapshot,
        )

        # Execute initialization actions
        init_result = await self.execute_initialization_actions(session)

        return session, init_result

    # =========================================================================
    # State Management Methods (Requirements 6.5, 6.7, 6.8)
    # =========================================================================

    def _validate_state_transition(
        self,
        current_status: str,
        target_status: str,
    ) -> None:
        """Validate that a state transition is allowed.

        Args:
            current_status: The current session status.
            target_status: The desired target status.

        Raises:
            InvalidStateTransitionError: If the transition is not allowed.

        Requirements:
            - 6.5: Support state transitions: created → active → resolved → closed
        """
        try:
            current = SessionStatus(current_status)
            target = SessionStatus(target_status)
        except ValueError as e:
            raise InvalidStateTransitionError(current_status, target_status) from e

        if target not in VALID_TRANSITIONS.get(current, set()):
            raise InvalidStateTransitionError(current_status, target_status)

    async def transition_to_active(
        self,
        session_id: uuid.UUID,
    ) -> CollaborationSession:
        """Transition a session from 'created' to 'active' status.

        This transition occurs when the collaboration session has been
        initialized and is ready for active collaboration (e.g., group
        chat created, notifications sent).

        Args:
            session_id: The unique identifier of the session.

        Returns:
            The updated CollaborationSession.

        Raises:
            ValueError: If the session is not found.
            InvalidStateTransitionError: If the transition is not allowed.

        Requirements:
            - 6.5: Support state transitions: created → active
            - 8.5: Send status update emails when session status changes
        """
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError(f"Collaboration session with ID '{session_id}' not found")

        old_status = session.status
        self._validate_state_transition(old_status, SessionStatus.ACTIVE.value)

        session.status = SessionStatus.ACTIVE.value

        # Update progress summary
        progress = dict(session.progress_summary)
        progress["current_phase"] = "active"
        if "初始化协同会话" in progress.get("pending_items", []):
            progress["pending_items"].remove("初始化协同会话")
            progress["completed_steps"] = progress.get("completed_steps", []) + ["初始化协同会话"]
        session.progress_summary = progress

        await self._db.flush()
        await self._db.refresh(session)

        logger.info(
            "Collaboration session %s transitioned to 'active' status",
            session_id,
        )

        # Send status change email notification (Requirements 8.5, 8.6, 8.7)
        await self._send_status_change_notification(
            session=session,
            old_status=old_status,
            new_status=SessionStatus.ACTIVE.value,
        )

        return session

    async def transition_to_resolved(
        self,
        session_id: uuid.UUID,
        resolution_summary: str | None = None,
    ) -> CollaborationSession:
        """Transition a session from 'active' to 'resolved' status.

        This transition occurs when the underlying issue has been resolved
        but the session is not yet formally closed.

        Args:
            session_id: The unique identifier of the session.
            resolution_summary: Optional summary of how the issue was resolved.

        Returns:
            The updated CollaborationSession.

        Raises:
            ValueError: If the session is not found.
            InvalidStateTransitionError: If the transition is not allowed.

        Requirements:
            - 6.5: Support state transitions: active → resolved
            - 8.5: Send status update emails when session status changes
        """
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError(f"Collaboration session with ID '{session_id}' not found")

        old_status = session.status
        self._validate_state_transition(old_status, SessionStatus.RESOLVED.value)

        session.status = SessionStatus.RESOLVED.value
        session.resolved_at = datetime.now(UTC)

        # Update progress summary
        progress = dict(session.progress_summary)
        progress["current_phase"] = "resolved"
        if resolution_summary:
            progress["resolution_summary"] = resolution_summary
        # Calculate duration
        if session.created_at:
            duration = datetime.now(UTC) - session.created_at
            progress["duration_minutes"] = int(duration.total_seconds() / 60)
        session.progress_summary = progress

        await self._db.flush()
        await self._db.refresh(session)

        logger.info(
            "Collaboration session %s transitioned to 'resolved' status",
            session_id,
        )

        # Send status change email notification (Requirements 8.5, 8.6, 8.7)
        await self._send_status_change_notification(
            session=session,
            old_status=old_status,
            new_status=SessionStatus.RESOLVED.value,
        )

        return session

    async def close_session(
        self,
        session_id: uuid.UUID,
        close_reason: str | None = None,
        generate_report: bool = True,
    ) -> CollaborationSession:
        """Close a collaboration session (manual close).

        This method supports closing a session from any non-closed state.
        When closed, a summary report is generated if requested.

        Args:
            session_id: The unique identifier of the session.
            close_reason: Optional reason for closing the session.
            generate_report: Whether to generate a summary report (default: True).

        Returns:
            The updated CollaborationSession with summary report.

        Raises:
            ValueError: If the session is not found.
            InvalidStateTransitionError: If the session is already closed.

        Requirements:
            - 6.5: Support state transitions: * → closed
            - 6.7: Support manual close of collaboration session
            - 6.8: Generate summary report when session is closed
            - 8.5: Send status update emails when session status changes
        """
        session = await self.get_session(
            session_id,
            include_messages=True,
            include_recommendations=True,
        )
        if session is None:
            raise ValueError(f"Collaboration session with ID '{session_id}' not found")

        old_status = session.status
        self._validate_state_transition(old_status, SessionStatus.CLOSED.value)

        # Generate summary report before closing (Requirement 6.8)
        if generate_report:
            summary_report = await self._generate_summary_report(
                session,
                close_reason=close_reason,
            )
            session.summary_report = summary_report

        session.status = SessionStatus.CLOSED.value
        session.closed_at = datetime.now(UTC)

        # Update progress summary
        progress = dict(session.progress_summary)
        progress["current_phase"] = "closed"
        if close_reason:
            progress["close_reason"] = close_reason
        # Calculate final duration
        if session.created_at:
            duration = datetime.now(UTC) - session.created_at
            progress["duration_minutes"] = int(duration.total_seconds() / 60)
        session.progress_summary = progress

        await self._db.flush()
        await self._db.refresh(session)

        logger.info(
            "Collaboration session %s closed (reason: %s)",
            session_id,
            close_reason[:100] if close_reason else "N/A",
        )

        # Send status change email notification (Requirements 8.5, 8.6, 8.7)
        await self._send_status_change_notification(
            session=session,
            old_status=old_status,
            new_status=SessionStatus.CLOSED.value,
        )

        return session

    async def update_status(
        self,
        session_id: uuid.UUID,
        target_status: str,
        reason: str | None = None,
    ) -> CollaborationSession:
        """Update the status of a collaboration session.

        This is a generic method that validates and performs any allowed
        state transition. For specific transitions, prefer using the
        dedicated methods (transition_to_active, transition_to_resolved,
        close_session).

        Args:
            session_id: The unique identifier of the session.
            target_status: The desired target status.
            reason: Optional reason for the status change.

        Returns:
            The updated CollaborationSession.

        Raises:
            ValueError: If the session is not found or target_status is invalid.
            InvalidStateTransitionError: If the transition is not allowed.

        Requirements:
            - 6.5: Support state transitions: created → active → resolved → closed
        """
        # Validate target status is a valid SessionStatus
        try:
            target = SessionStatus(target_status)
        except ValueError as e:
            raise ValueError(
                f"Invalid target status '{target_status}'. "
                f"Valid values: {[s.value for s in SessionStatus]}"
            ) from e

        # Delegate to specific methods for proper handling
        if target == SessionStatus.ACTIVE:
            return await self.transition_to_active(session_id)
        elif target == SessionStatus.RESOLVED:
            return await self.transition_to_resolved(session_id, resolution_summary=reason)
        elif target == SessionStatus.CLOSED:
            return await self.close_session(session_id, close_reason=reason)
        else:
            # For 'created' status, this should not be reachable in normal flow
            raise ValueError(
                f"Cannot transition to '{target_status}' status. "
                "Sessions are created with 'created' status."
            )

    async def _generate_summary_report(
        self,
        session: CollaborationSession,
        close_reason: str | None = None,
    ) -> dict[str, Any]:
        """Generate a summary report for a collaboration session.

        Creates a comprehensive summary of the collaboration session including:
        - Session metadata (ID, scenario, duration)
        - Timeline of key events
        - Message statistics
        - Recommendations summary
        - Resolution details

        Args:
            session: The collaboration session to generate report for.
            close_reason: Optional reason for closing the session.

        Returns:
            Dictionary containing the summary report.

        Requirements:
            - 6.8: Generate summary report when session is closed
        """
        now = datetime.now(UTC)

        # Calculate duration
        duration_minutes = 0
        if session.created_at:
            duration = now - session.created_at
            duration_minutes = int(duration.total_seconds() / 60)

        # Get message statistics
        messages = session.messages or []
        message_count = len(messages)
        message_by_channel: dict[str, int] = {}
        message_by_sender: dict[str, int] = {}

        for msg in messages:
            # Count by channel
            channel = msg.source_channel
            message_by_channel[channel] = message_by_channel.get(channel, 0) + 1

            # Count by sender
            sender = msg.sender_name or msg.sender_id or "unknown"
            message_by_sender[sender] = message_by_sender.get(sender, 0) + 1

        # Get recommendations statistics
        recommendations = session.recommendations or []
        recommendation_count = len(recommendations)
        adopted_count = sum(1 for r in recommendations if r.status == "adopted")
        ignored_count = sum(1 for r in recommendations if r.status == "ignored")

        # Build timeline of key events
        timeline: list[dict[str, Any]] = []

        # Session created
        if session.created_at:
            timeline.append({
                "event": "session_created",
                "timestamp": session.created_at.isoformat(),
                "description": "协同会话创建",
            })

        # Session resolved
        if session.resolved_at:
            timeline.append({
                "event": "session_resolved",
                "timestamp": session.resolved_at.isoformat(),
                "description": "问题已解决",
            })

        # Session closed
        timeline.append({
            "event": "session_closed",
            "timestamp": now.isoformat(),
            "description": close_reason or "协同会话关闭",
        })

        # Sort timeline by timestamp
        timeline.sort(key=lambda x: x["timestamp"])

        # Get scenario info from config snapshot
        config = session.config_snapshot or {}
        scenario_name = config.get("scenario_name", "Unknown")
        scenario_type = config.get("scenario_type", "Unknown")

        # Build the summary report
        report: dict[str, Any] = {
            "report_version": "1.0",
            "generated_at": now.isoformat(),
            "session": {
                "id": str(session.id),
                "scenario_id": str(session.scenario_id),
                "scenario_name": scenario_name,
                "scenario_type": scenario_type,
                "trigger_reason": session.trigger_reason,
                "created_at": session.created_at.isoformat() if session.created_at else None,
                "resolved_at": session.resolved_at.isoformat() if session.resolved_at else None,
                "closed_at": now.isoformat(),
                "duration_minutes": duration_minutes,
                "final_status": SessionStatus.CLOSED.value,
            },
            "group_chat": {
                "id": session.group_chat_id,
                "name": session.group_chat_name,
            },
            "messages": {
                "total_count": message_count,
                "by_channel": message_by_channel,
                "by_sender": message_by_sender,
            },
            "recommendations": {
                "total_count": recommendation_count,
                "adopted_count": adopted_count,
                "ignored_count": ignored_count,
                "pending_count": recommendation_count - adopted_count - ignored_count,
            },
            "progress": session.progress_summary,
            "timeline": timeline,
            "close_reason": close_reason,
        }

        logger.info(
            "Generated summary report for collaboration session %s "
            "(duration: %d minutes, messages: %d, recommendations: %d)",
            session.id,
            duration_minutes,
            message_count,
            recommendation_count,
        )

        return report

    async def get_summary_report(
        self,
        session_id: uuid.UUID,
    ) -> dict[str, Any] | None:
        """Get the summary report for a closed collaboration session.

        Args:
            session_id: The unique identifier of the session.

        Returns:
            The summary report if available, None otherwise.

        Raises:
            ValueError: If the session is not found.
        """
        session = await self.get_session(session_id)
        if session is None:
            raise ValueError(f"Collaboration session with ID '{session_id}' not found")

        return session.summary_report

    async def regenerate_summary_report(
        self,
        session_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Regenerate the summary report for a closed collaboration session.

        This can be used to update the report with any changes made after
        the initial closure.

        Args:
            session_id: The unique identifier of the session.

        Returns:
            The newly generated summary report.

        Raises:
            ValueError: If the session is not found or not closed.

        Requirements:
            - 6.8: Generate summary report when session is closed
        """
        session = await self.get_session(
            session_id,
            include_messages=True,
            include_recommendations=True,
        )
        if session is None:
            raise ValueError(f"Collaboration session with ID '{session_id}' not found")

        if session.status != SessionStatus.CLOSED.value:
            raise ValueError(
                f"Cannot regenerate summary report for session in '{session.status}' status. "
                "Session must be closed first."
            )

        # Get close reason from existing progress summary
        progress = session.progress_summary or {}
        close_reason = progress.get("close_reason")

        # Generate new report
        summary_report = await self._generate_summary_report(
            session,
            close_reason=close_reason,
        )
        session.summary_report = summary_report

        await self._db.flush()
        await self._db.refresh(session)

        logger.info(
            "Regenerated summary report for collaboration session %s",
            session_id,
        )

        return summary_report
