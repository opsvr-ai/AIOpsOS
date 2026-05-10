"""Message synchronization service for emergency collaboration.

This module provides the MessageSyncService for bidirectional message
synchronization between collaboration sessions and external channels
(primarily WeChat Work group chats). It supports:
- Syncing messages from collaboration sessions to group chats
- Syncing messages from group chats to collaboration sessions
- Tracking sync status to avoid duplicate syncing
- Message format conversion for different channels
- Configurable sync direction (unidirectional or bidirectional)
- Source channel and original message ID tracking
- Failure tracking with error reasons and manual retry support
- Message deduplication using message IDs to prevent duplicate syncs

Requirements:
- 9.1: Support syncing collaboration session messages to associated group chats
- 9.2: Support syncing group chat messages to collaboration session records
- 9.3: Support configuring sync direction (unidirectional or bidirectional)
- 9.4: Record source channel and original message ID for each synced message
- 9.5: Support message format conversion for different channel requirements
- 9.6: Record sync failure reason and support manual retry
- 9.7: Avoid duplicate sync through message ID deduplication
"""

from __future__ import annotations

import html
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
from src.models.collaboration import (
    CollaborationMessage,
    CollaborationSession,
    MessageSyncFailure,
)
from src.services.group_chat_manager import (
    GroupChatManager,
    ReceivedMessage,
    WeComConfig,
)

logger = logging.getLogger(__name__)


# ── Enums and Constants ──────────────────────────────────────────────────────


class SyncDirection(str, Enum):  # noqa: UP042
    """Message sync direction.

    Defines the direction of message synchronization between
    collaboration sessions and external channels.

    Supports three modes:
    - SESSION_TO_GROUP: Only sync from collaboration session to group chat (unidirectional)
    - GROUP_TO_SESSION: Only sync from group chat to collaboration session (unidirectional)
    - BIDIRECTIONAL: Sync in both directions (default)

    Requirements:
        - 9.3: Support configuring sync direction (unidirectional or bidirectional)

    Note: Using str + Enum for JSON serialization compatibility.
    """

    SESSION_TO_GROUP = "session_to_group"  # From collaboration session to group chat (unidirectional)
    GROUP_TO_SESSION = "group_to_session"  # From group chat to collaboration session (unidirectional)
    BIDIRECTIONAL = "bidirectional"  # Both directions (default)

    @classmethod
    def from_string(cls, value: str) -> SyncDirection:
        """Create SyncDirection from a string value.

        Args:
            value: String representation of the direction.

        Returns:
            SyncDirection enum value.

        Raises:
            ValueError: If the value is not a valid direction.
        """
        try:
            return cls(value.lower())
        except ValueError:
            # Try common aliases
            aliases = {
                "unidirectional_to_group": cls.SESSION_TO_GROUP,
                "unidirectional_to_session": cls.GROUP_TO_SESSION,
                "one_way_to_group": cls.SESSION_TO_GROUP,
                "one_way_to_session": cls.GROUP_TO_SESSION,
                "two_way": cls.BIDIRECTIONAL,
                "both": cls.BIDIRECTIONAL,
            }
            if value.lower() in aliases:
                return aliases[value.lower()]
            raise ValueError(f"Invalid sync direction: {value}")


class SyncStatus(str, Enum):  # noqa: UP042
    """Message sync status.

    Tracks the synchronization status of individual messages.

    Note: Using str + Enum for JSON serialization compatibility.
    """

    PENDING = "pending"
    SYNCED = "synced"
    FAILED = "failed"
    SKIPPED = "skipped"  # Skipped due to deduplication or filtering


class ChannelFormat(str, Enum):  # noqa: UP042
    """Target channel message format.

    Defines the message format requirements for different channels.
    Used for message format conversion during synchronization.

    Requirements:
        - 9.5: Support message format conversion for different channel requirements

    Note: Using str + Enum for JSON serialization compatibility.
    """

    PLAIN_TEXT = "plain_text"  # Plain text format (no formatting)
    WECOM_MARKDOWN = "wecom_markdown"  # WeChat Work markdown format
    EMAIL_HTML = "email_html"  # HTML format for email
    STANDARD_MARKDOWN = "standard_markdown"  # Standard markdown format


# Channel identifiers
CHANNEL_WECOM = "wecom"
CHANNEL_EMAIL = "email"
CHANNEL_SYSTEM = "system"
CHANNEL_API = "api"

# Channel to format mapping
CHANNEL_FORMAT_MAP: dict[str, ChannelFormat] = {
    CHANNEL_WECOM: ChannelFormat.WECOM_MARKDOWN,
    CHANNEL_EMAIL: ChannelFormat.EMAIL_HTML,
    CHANNEL_SYSTEM: ChannelFormat.PLAIN_TEXT,
    CHANNEL_API: ChannelFormat.PLAIN_TEXT,
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class SyncConfig:
    """Configuration for message synchronization.

    Provides comprehensive configuration for controlling message sync behavior
    including direction, filtering, and format conversion options.

    Attributes:
        direction: The sync direction (session_to_group, group_to_session, bidirectional).
        enabled: Whether sync is enabled.
        sync_system_messages: Whether to sync system-generated messages.
        sync_event_messages: Whether to sync event-type messages.
        format_for_channel: Whether to apply format conversion for target channel.
        preserve_source_info: Whether to include source channel info in synced messages.
        target_format: Override target format (None uses channel default).

    Requirements:
        - 9.3: Support configuring sync direction (unidirectional or bidirectional)
        - 9.4: Record source channel and original message ID for each synced message
        - 9.5: Support message format conversion for different channel requirements
    """

    direction: SyncDirection = SyncDirection.BIDIRECTIONAL
    enabled: bool = True
    sync_system_messages: bool = True
    sync_event_messages: bool = False
    format_for_channel: bool = True
    preserve_source_info: bool = True  # Include source channel info in synced messages
    target_format: ChannelFormat | None = None  # Override target format

    @classmethod
    def from_dict(cls, config: dict[str, Any]) -> SyncConfig:
        """Create SyncConfig from a configuration dictionary.

        Parses configuration from scenario collaboration_config or session config.

        Args:
            config: Configuration dictionary with sync settings.

        Returns:
            SyncConfig instance with parsed settings.

        Requirements:
            - 9.3: Support configuring sync direction (unidirectional or bidirectional)
        """
        direction_str = config.get("sync_direction", "bidirectional")
        try:
            direction = SyncDirection.from_string(direction_str)
        except ValueError:
            logger.warning("Invalid sync direction '%s', using bidirectional", direction_str)
            direction = SyncDirection.BIDIRECTIONAL

        # Parse target format if specified
        target_format = None
        target_format_str = config.get("target_format")
        if target_format_str:
            try:
                target_format = ChannelFormat(target_format_str)
            except ValueError:
                logger.warning("Invalid target format '%s', using channel default", target_format_str)

        return cls(
            direction=direction,
            enabled=config.get("sync_enabled", True),
            sync_system_messages=config.get("sync_system_messages", True),
            sync_event_messages=config.get("sync_event_messages", False),
            format_for_channel=config.get("format_for_channel", True),
            preserve_source_info=config.get("preserve_source_info", True),
            target_format=target_format,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert SyncConfig to a dictionary.

        Returns:
            Dictionary representation of the configuration.
        """
        return {
            "sync_direction": self.direction.value,
            "sync_enabled": self.enabled,
            "sync_system_messages": self.sync_system_messages,
            "sync_event_messages": self.sync_event_messages,
            "format_for_channel": self.format_for_channel,
            "preserve_source_info": self.preserve_source_info,
            "target_format": self.target_format.value if self.target_format else None,
        }

    def allows_session_to_group(self) -> bool:
        """Check if configuration allows session-to-group sync.

        Returns:
            True if session-to-group sync is allowed.
        """
        return self.direction in (SyncDirection.SESSION_TO_GROUP, SyncDirection.BIDIRECTIONAL)

    def allows_group_to_session(self) -> bool:
        """Check if configuration allows group-to-session sync.

        Returns:
            True if group-to-session sync is allowed.
        """
        return self.direction in (SyncDirection.GROUP_TO_SESSION, SyncDirection.BIDIRECTIONAL)


@dataclass
class MessageSyncResult:
    """Result of a message sync operation.

    Tracks the outcome of syncing a single message, including source
    channel and message ID information for traceability.

    Attributes:
        success: Whether the sync was successful.
        status: The sync status.
        source_message_id: ID of the source message.
        source_channel: The source channel the message came from.
        target_channel: The target channel the message was synced to.
        target_message_id: ID of the message in the target channel (if available).
        error: Error message if sync failed.
        synced_at: Timestamp when the sync completed.
        format_applied: The format conversion applied (if any).
        details: Additional details about the sync operation.

    Requirements:
        - 9.4: Record source channel and original message ID for each synced message
    """

    success: bool
    status: SyncStatus
    source_message_id: str | None = None
    source_channel: str | None = None
    target_channel: str | None = None
    target_message_id: str | None = None
    error: str | None = None
    synced_at: datetime | None = None
    format_applied: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchSyncResult:
    """Result of a batch message sync operation.

    Attributes:
        total_messages: Total number of messages to sync.
        synced_count: Number of messages successfully synced.
        failed_count: Number of messages that failed to sync.
        skipped_count: Number of messages skipped (duplicates, filtered).
        results: Individual sync results for each message.
        errors: List of error messages encountered.
    """

    total_messages: int = 0
    synced_count: int = 0
    failed_count: int = 0
    skipped_count: int = 0
    results: list[MessageSyncResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def add_result(self, result: MessageSyncResult) -> None:
        """Add a sync result and update counters."""
        self.results.append(result)
        self.total_messages += 1

        if result.status == SyncStatus.SYNCED:
            self.synced_count += 1
        elif result.status == SyncStatus.FAILED:
            self.failed_count += 1
            if result.error:
                self.errors.append(result.error)
        elif result.status == SyncStatus.SKIPPED:
            self.skipped_count += 1

    @property
    def all_success(self) -> bool:
        """Check if all messages were synced successfully."""
        return self.failed_count == 0


# ── Message Format Converter ─────────────────────────────────────────────────


class MessageFormatConverter:
    """Converts message content between different channel formats.

    Provides format conversion for messages being synced between different
    channels, each with their own formatting requirements:
    - WeChat Work: Supports a subset of markdown
    - Email: HTML format
    - Plain text: No formatting

    Requirements:
        - 9.5: Support message format conversion for different channel requirements
    """

    # WeChat Work markdown supported tags
    WECOM_SUPPORTED_TAGS = {"bold", "link", "quote", "code"}

    # Channel display names for source info
    CHANNEL_DISPLAY_NAMES = {
        CHANNEL_WECOM: "企业微信",
        CHANNEL_EMAIL: "邮件",
        CHANNEL_SYSTEM: "系统",
        CHANNEL_API: "API",
    }

    # Channel emoji indicators
    CHANNEL_EMOJI = {
        CHANNEL_WECOM: "💬",
        CHANNEL_EMAIL: "📧",
        CHANNEL_SYSTEM: "🤖",
        CHANNEL_API: "🔗",
    }

    @classmethod
    def convert(
        cls,
        content: str,
        source_format: ChannelFormat,
        target_format: ChannelFormat,
        source_channel: str | None = None,
        sender_name: str | None = None,
        include_source_info: bool = True,
    ) -> str:
        """Convert message content from source format to target format.

        Args:
            content: The message content to convert.
            source_format: The format of the source content.
            target_format: The desired target format.
            source_channel: Optional source channel for attribution.
            sender_name: Optional sender name for attribution.
            include_source_info: Whether to include source channel info.

        Returns:
            Converted message content in the target format.

        Requirements:
            - 9.5: Support message format conversion for different channel requirements
        """
        # If formats are the same, just add source info if needed
        if source_format == target_format:
            if include_source_info and source_channel:
                return cls._add_source_info(
                    content, target_format, source_channel, sender_name
                )
            return content

        # Handle direct conversions that preserve formatting
        if source_format == ChannelFormat.STANDARD_MARKDOWN:
            if target_format == ChannelFormat.EMAIL_HTML:
                # Direct markdown to HTML conversion preserving formatting
                result = cls._markdown_to_html(content)
                if include_source_info and source_channel:
                    result = cls._add_source_info(result, target_format, source_channel, sender_name)
                return result
            elif target_format == ChannelFormat.WECOM_MARKDOWN:
                # Convert standard markdown to WeChat Work markdown
                result = cls.to_wecom_markdown(content, include_source_info=False)
                if include_source_info and source_channel:
                    result = cls._add_source_info(result, target_format, source_channel, sender_name)
                return result

        if source_format == ChannelFormat.WECOM_MARKDOWN:
            if target_format == ChannelFormat.EMAIL_HTML:
                # WeChat Work markdown to HTML
                result = cls._markdown_to_html(content)
                if include_source_info and source_channel:
                    result = cls._add_source_info(result, target_format, source_channel, sender_name)
                return result

        # For other conversions, go through plain text intermediate
        if source_format == ChannelFormat.EMAIL_HTML:
            intermediate = cls._html_to_plain_text(content)
        elif source_format == ChannelFormat.WECOM_MARKDOWN:
            intermediate = cls._wecom_markdown_to_plain_text(content)
        elif source_format == ChannelFormat.STANDARD_MARKDOWN:
            intermediate = cls._standard_markdown_to_plain_text(content)
        else:
            intermediate = content

        # Convert from plain text to target format
        if target_format == ChannelFormat.WECOM_MARKDOWN:
            result = cls._plain_text_to_wecom_markdown(intermediate)
        elif target_format == ChannelFormat.EMAIL_HTML:
            result = cls._plain_text_to_email_html(intermediate)
        elif target_format == ChannelFormat.STANDARD_MARKDOWN:
            result = cls._plain_text_to_standard_markdown(intermediate)
        else:
            result = intermediate

        # Add source info if requested
        if include_source_info and source_channel:
            result = cls._add_source_info(result, target_format, source_channel, sender_name)

        return result

    @classmethod
    def _markdown_to_html(cls, content: str) -> str:
        """Convert markdown content to HTML.

        Preserves markdown formatting by converting to HTML equivalents.

        Args:
            content: Markdown content.

        Returns:
            HTML content.
        """
        result = content

        # Escape HTML special characters first (but not markdown syntax)
        # We need to be careful not to escape characters that are part of markdown
        result = result.replace("&", "&amp;")
        result = result.replace("<", "&lt;")
        result = result.replace(">", "&gt;")

        # Convert markdown to HTML
        # Bold: **text** -> <strong>text</strong>
        result = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", result)

        # Italic: *text* or _text_ -> <em>text</em>
        result = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", result)
        result = re.sub(r"_([^_]+)_", r"<em>\1</em>", result)

        # Code: `code` -> <code>code</code>
        result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)

        # Links: [text](url) -> <a href="url">text</a>
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', result)

        # Quotes: > text -> <blockquote>text</blockquote>
        result = re.sub(
            r"^&gt;\s*(.+)$",  # Note: > was escaped to &gt;
            r"<blockquote>\1</blockquote>",
            result,
            flags=re.MULTILINE,
        )

        # Convert newlines to <br> for HTML display
        result = result.replace("\n", "<br>\n")

        return result

    @classmethod
    def to_wecom_markdown(
        cls,
        content: str,
        source_channel: str | None = None,
        sender_name: str | None = None,
        include_source_info: bool = True,
    ) -> str:
        """Convert content to WeChat Work markdown format.

        WeChat Work supports a limited subset of markdown:
        - Bold: **text** or <font color="warning">text</font>
        - Links: [text](url)
        - Quotes: > text
        - Code: `code`

        Args:
            content: The message content to convert.
            source_channel: Optional source channel for attribution.
            sender_name: Optional sender name for attribution.
            include_source_info: Whether to include source channel info.

        Returns:
            Content formatted for WeChat Work markdown.

        Requirements:
            - 9.5: Support message format conversion for different channel requirements
        """
        # Escape special characters that might interfere with markdown
        result = content

        # Convert common HTML entities if present
        result = html.unescape(result)

        # Preserve existing markdown formatting that WeChat Work supports
        # Bold: **text** is supported
        # Links: [text](url) is supported
        # Quotes: > text is supported
        # Code: `code` is supported

        # Convert unsupported markdown to plain text
        # Headers -> Bold
        result = re.sub(r"^#{1,6}\s+(.+)$", r"**\1**", result, flags=re.MULTILINE)

        # Italic -> plain text (WeChat Work doesn't support italic)
        result = re.sub(r"\*([^*]+)\*", r"\1", result)
        result = re.sub(r"_([^_]+)_", r"\1", result)

        # Strikethrough -> plain text
        result = re.sub(r"~~([^~]+)~~", r"\1", result)

        # Add source info if requested
        if include_source_info and source_channel:
            result = cls._add_source_info(
                result, ChannelFormat.WECOM_MARKDOWN, source_channel, sender_name
            )

        return result

    @classmethod
    def to_email_html(
        cls,
        content: str,
        source_channel: str | None = None,
        sender_name: str | None = None,
        include_source_info: bool = True,
    ) -> str:
        """Convert content to HTML format for email.

        Converts plain text or markdown content to HTML suitable for
        email rendering.

        Args:
            content: The message content to convert.
            source_channel: Optional source channel for attribution.
            sender_name: Optional sender name for attribution.
            include_source_info: Whether to include source channel info.

        Returns:
            Content formatted as HTML for email.

        Requirements:
            - 9.5: Support message format conversion for different channel requirements
        """
        # Escape HTML special characters first
        result = html.escape(content)

        # Convert markdown-style formatting to HTML
        # Bold: **text** -> <strong>text</strong>
        result = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", result)

        # Italic: *text* or _text_ -> <em>text</em>
        result = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", result)
        result = re.sub(r"_([^_]+)_", r"<em>\1</em>", result)

        # Code: `code` -> <code>code</code>
        result = re.sub(r"`([^`]+)`", r"<code>\1</code>", result)

        # Links: [text](url) -> <a href="url">text</a>
        result = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', result)

        # Quotes: > text -> <blockquote>text</blockquote>
        result = re.sub(
            r"^>\s*(.+)$",
            r"<blockquote>\1</blockquote>",
            result,
            flags=re.MULTILINE,
        )

        # Convert newlines to <br> for HTML display
        result = result.replace("\n", "<br>\n")

        # Add source info if requested
        if include_source_info and source_channel:
            result = cls._add_source_info(
                result, ChannelFormat.EMAIL_HTML, source_channel, sender_name
            )

        return result

    @classmethod
    def to_plain_text(
        cls,
        content: str,
        source_channel: str | None = None,
        sender_name: str | None = None,
        include_source_info: bool = True,
    ) -> str:
        """Convert content to plain text format.

        Strips all formatting from the content, leaving only plain text.

        Args:
            content: The message content to convert.
            source_channel: Optional source channel for attribution.
            sender_name: Optional sender name for attribution.
            include_source_info: Whether to include source channel info.

        Returns:
            Plain text content with no formatting.

        Requirements:
            - 9.5: Support message format conversion for different channel requirements
        """
        result = content

        # Remove HTML tags if present
        result = re.sub(r"<[^>]+>", "", result)

        # Convert HTML entities
        result = html.unescape(result)

        # Remove markdown formatting
        # Bold
        result = re.sub(r"\*\*([^*]+)\*\*", r"\1", result)
        # Italic
        result = re.sub(r"\*([^*]+)\*", r"\1", result)
        result = re.sub(r"_([^_]+)_", r"\1", result)
        # Code
        result = re.sub(r"`([^`]+)`", r"\1", result)
        # Links - keep text, remove URL
        result = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", result)
        # Headers
        result = re.sub(r"^#{1,6}\s+", "", result, flags=re.MULTILINE)
        # Quotes
        result = re.sub(r"^>\s*", "", result, flags=re.MULTILINE)

        # Add source info if requested
        if include_source_info and source_channel:
            result = cls._add_source_info(
                result, ChannelFormat.PLAIN_TEXT, source_channel, sender_name
            )

        return result

    @classmethod
    def _add_source_info(
        cls,
        content: str,
        target_format: ChannelFormat,
        source_channel: str,
        sender_name: str | None = None,
    ) -> str:
        """Add source channel information to the message.

        Prepends source channel and sender information to the message
        in a format appropriate for the target channel.

        Args:
            content: The message content.
            target_format: The target format.
            source_channel: The source channel identifier.
            sender_name: Optional sender name.

        Returns:
            Content with source info prepended.

        Requirements:
            - 9.4: Record source channel and original message ID for each synced message
        """
        emoji = cls.CHANNEL_EMOJI.get(source_channel, "📨")
        channel_name = cls.CHANNEL_DISPLAY_NAMES.get(source_channel, source_channel)

        if target_format == ChannelFormat.EMAIL_HTML:
            # HTML format with styled source info
            source_info = f'<span style="color: #666; font-size: 12px;">{emoji} '
            if sender_name:
                source_info += f"[{sender_name}] "
            source_info += f"来自{channel_name}</span><br>\n"
            return source_info + content

        elif target_format == ChannelFormat.WECOM_MARKDOWN:
            # WeChat Work markdown format
            source_info = f"{emoji} "
            if sender_name:
                source_info += f"[{sender_name}] "
            source_info += f"来自{channel_name}\n"
            return source_info + content

        else:
            # Plain text format
            source_info = f"{emoji} "
            if sender_name:
                source_info += f"[{sender_name}] "
            source_info += f"来自{channel_name}: "
            return source_info + content

    @classmethod
    def _html_to_plain_text(cls, content: str) -> str:
        """Convert HTML content to plain text.

        Args:
            content: HTML content.

        Returns:
            Plain text content.
        """
        # Remove HTML tags
        result = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
        result = re.sub(r"<p[^>]*>", "\n", result, flags=re.IGNORECASE)
        result = re.sub(r"</p>", "\n", result, flags=re.IGNORECASE)
        result = re.sub(r"<[^>]+>", "", result)

        # Convert HTML entities
        result = html.unescape(result)

        # Clean up extra whitespace
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()

    @classmethod
    def _wecom_markdown_to_plain_text(cls, content: str) -> str:
        """Convert WeChat Work markdown to plain text.

        Args:
            content: WeChat Work markdown content.

        Returns:
            Plain text content.
        """
        result = content

        # Remove bold
        result = re.sub(r"\*\*([^*]+)\*\*", r"\1", result)

        # Remove links - keep text
        result = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", result)

        # Remove quotes marker
        result = re.sub(r"^>\s*", "", result, flags=re.MULTILINE)

        # Remove code markers
        result = re.sub(r"`([^`]+)`", r"\1", result)

        # Remove WeChat Work color tags
        result = re.sub(r'<font color="[^"]*">([^<]*)</font>', r"\1", result)

        return result.strip()

    @classmethod
    def _standard_markdown_to_plain_text(cls, content: str) -> str:
        """Convert standard markdown to plain text.

        Args:
            content: Standard markdown content.

        Returns:
            Plain text content.
        """
        result = content

        # Remove headers
        result = re.sub(r"^#{1,6}\s+", "", result, flags=re.MULTILINE)

        # Remove bold/italic
        result = re.sub(r"\*\*([^*]+)\*\*", r"\1", result)
        result = re.sub(r"\*([^*]+)\*", r"\1", result)
        result = re.sub(r"__([^_]+)__", r"\1", result)
        result = re.sub(r"_([^_]+)_", r"\1", result)

        # Remove strikethrough
        result = re.sub(r"~~([^~]+)~~", r"\1", result)

        # Remove links - keep text
        result = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", result)

        # Remove images - keep alt text
        result = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", result)

        # Remove code markers
        result = re.sub(r"`([^`]+)`", r"\1", result)
        result = re.sub(r"```[^`]*```", "", result, flags=re.DOTALL)

        # Remove quotes marker
        result = re.sub(r"^>\s*", "", result, flags=re.MULTILINE)

        # Remove horizontal rules
        result = re.sub(r"^[-*_]{3,}$", "", result, flags=re.MULTILINE)

        return result.strip()

    @classmethod
    def _plain_text_to_wecom_markdown(cls, content: str) -> str:
        """Convert plain text to WeChat Work markdown.

        Minimal conversion - WeChat Work markdown is close to plain text.

        Args:
            content: Plain text content.

        Returns:
            WeChat Work markdown content.
        """
        # Plain text is already valid WeChat Work markdown
        # Just ensure proper line breaks
        return content

    @classmethod
    def _plain_text_to_email_html(cls, content: str) -> str:
        """Convert plain text to HTML for email.

        Args:
            content: Plain text content.

        Returns:
            HTML content.
        """
        # Escape HTML special characters
        result = html.escape(content)

        # Convert newlines to <br>
        result = result.replace("\n", "<br>\n")

        return result

    @classmethod
    def _plain_text_to_standard_markdown(cls, content: str) -> str:
        """Convert plain text to standard markdown.

        Args:
            content: Plain text content.

        Returns:
            Standard markdown content.
        """
        # Plain text is already valid markdown
        return content

    @classmethod
    def get_format_for_channel(cls, channel: str) -> ChannelFormat:
        """Get the appropriate format for a channel.

        Args:
            channel: The channel identifier.

        Returns:
            The ChannelFormat for the channel.
        """
        return CHANNEL_FORMAT_MAP.get(channel, ChannelFormat.PLAIN_TEXT)


# ── Message Sync Service ─────────────────────────────────────────────────────


class MessageSyncService:
    """Service for bidirectional message synchronization.

    This service handles synchronization of messages between collaboration
    sessions and external channels (primarily WeChat Work group chats).

    Features:
        - Sync messages from collaboration sessions to group chats
        - Sync messages from group chats to collaboration sessions
        - Track sync status to avoid duplicate syncing
        - Configurable sync direction (unidirectional or bidirectional)
        - Message format conversion for different channels
        - Source channel and message ID tracking

    Requirements:
        - 9.1: Support syncing collaboration session messages to group chats
        - 9.2: Support syncing group chat messages to collaboration sessions
        - 9.3: Support configuring sync direction (unidirectional or bidirectional)
        - 9.4: Record source channel and original message ID for each synced message
        - 9.5: Support message format conversion for different channel requirements
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialize the MessageSyncService.

        Args:
            db: Async database session for persistence operations.
        """
        self._db = db
        self._group_chat_manager: GroupChatManager | None = None
        self._format_converter = MessageFormatConverter()

    def _get_group_chat_manager(self) -> GroupChatManager:
        """Get or create the GroupChatManager instance.

        Lazily initializes the GroupChatManager to avoid circular dependencies.

        Returns:
            The GroupChatManager instance.
        """
        if self._group_chat_manager is None:
            self._group_chat_manager = GroupChatManager(self._db)
        return self._group_chat_manager

    # ── Session to Group Chat Sync ───────────────────────────────────────────

    async def sync_session_message_to_group(
        self,
        message: CollaborationMessage,
        session: CollaborationSession,
        wecom_config: WeComConfig | None = None,
        sync_config: SyncConfig | None = None,
    ) -> MessageSyncResult:
        """Sync a collaboration session message to the associated group chat.

        Takes a message from the collaboration session and sends it to the
        associated WeChat Work group chat.

        Args:
            message: The collaboration message to sync.
            session: The collaboration session containing the message.
            wecom_config: Optional WeChat Work configuration. If not provided,
                will attempt to get config from the session's scenario.
            sync_config: Optional sync configuration.

        Returns:
            MessageSyncResult indicating success or failure.

        Requirements:
            - 9.1: Support syncing collaboration session messages to group chats
        """
        if sync_config is None:
            sync_config = SyncConfig()

        # Check if sync is enabled and direction allows session-to-group
        if not sync_config.enabled:
            return MessageSyncResult(
                success=True,
                status=SyncStatus.SKIPPED,
                source_message_id=str(message.id),
                target_channel=CHANNEL_WECOM,
                details={"reason": "Sync disabled"},
            )

        if sync_config.direction == SyncDirection.GROUP_TO_SESSION:
            return MessageSyncResult(
                success=True,
                status=SyncStatus.SKIPPED,
                source_message_id=str(message.id),
                source_channel=message.source_channel,
                target_channel=CHANNEL_WECOM,
                details={"reason": "Sync direction does not allow session-to-group"},
            )

        # Check if message should be synced based on type
        if not self._should_sync_message(message, sync_config):
            return MessageSyncResult(
                success=True,
                status=SyncStatus.SKIPPED,
                source_message_id=str(message.id),
                source_channel=message.source_channel,
                target_channel=CHANNEL_WECOM,
                details={"reason": f"Message type '{message.message_type}' filtered"},
            )

        # Check if already synced to wecom
        if CHANNEL_WECOM in (message.synced_to or []):
            return MessageSyncResult(
                success=True,
                status=SyncStatus.SKIPPED,
                source_message_id=str(message.id),
                source_channel=message.source_channel,
                target_channel=CHANNEL_WECOM,
                details={"reason": "Already synced to wecom"},
            )

        # Check if session has a group chat
        if not session.group_chat_id:
            return MessageSyncResult(
                success=False,
                status=SyncStatus.FAILED,
                source_message_id=str(message.id),
                source_channel=message.source_channel,
                target_channel=CHANNEL_WECOM,
                error="Session has no associated group chat",
            )

        # Get WeChat Work config if not provided
        if wecom_config is None:
            wecom_config = await self._get_wecom_config_for_scenario(session.scenario_id)
            if wecom_config is None:
                return MessageSyncResult(
                    success=False,
                    status=SyncStatus.FAILED,
                    source_message_id=str(message.id),
                    source_channel=message.source_channel,
                    target_channel=CHANNEL_WECOM,
                    error="No WeChat Work configuration available",
                )

        # Format message content for group chat (Requirement 9.5)
        formatted_content, format_applied = self._format_message_for_group_chat(
            message, sync_config.format_for_channel, sync_config
        )

        # Send message to group chat
        group_chat_manager = self._get_group_chat_manager()

        if message.message_type == "markdown":
            send_result = await group_chat_manager.send_markdown_message(
                wecom_config=wecom_config,
                chatid=session.group_chat_id,
                content=formatted_content,
            )
        else:
            send_result = await group_chat_manager.send_text_message(
                wecom_config=wecom_config,
                chatid=session.group_chat_id,
                content=formatted_content,
            )

        if send_result.success:
            # Update message synced_to list
            synced_to = list(message.synced_to or [])
            if CHANNEL_WECOM not in synced_to:
                synced_to.append(CHANNEL_WECOM)
                message.synced_to = synced_to
                await self._db.flush()

            logger.info(
                "Synced message %s to group chat %s for session %s (format: %s)",
                message.id,
                session.group_chat_id,
                session.id,
                format_applied,
            )

            return MessageSyncResult(
                success=True,
                status=SyncStatus.SYNCED,
                source_message_id=str(message.id),
                source_channel=message.source_channel,
                target_channel=CHANNEL_WECOM,
                synced_at=datetime.now(UTC),
                format_applied=format_applied,
                details={
                    "group_chat_id": session.group_chat_id,
                    "message_type": message.message_type,
                    "original_source_message_id": message.source_message_id,
                },
            )
        else:
            error_message = send_result.error_message or "Unknown error"
            error_code = str(send_result.error_code) if send_result.error_code else None

            logger.warning(
                "Failed to sync message %s to group chat %s: %s",
                message.id,
                session.group_chat_id,
                error_message,
            )

            # Record the sync failure for later retry (Requirement 9.6)
            await self.record_sync_failure(
                message=message,
                session=session,
                target_channel=CHANNEL_WECOM,
                error_reason=error_message,
                error_code=error_code,
                error_details={
                    "group_chat_id": session.group_chat_id,
                    "message_type": message.message_type,
                },
            )

            return MessageSyncResult(
                success=False,
                status=SyncStatus.FAILED,
                source_message_id=str(message.id),
                target_channel=CHANNEL_WECOM,
                error=error_message,
                details={
                    "error_code": error_code,
                    "group_chat_id": session.group_chat_id,
                },
            )

    async def sync_session_messages_to_group(
        self,
        session: CollaborationSession,
        messages: list[CollaborationMessage] | None = None,
        wecom_config: WeComConfig | None = None,
        sync_config: SyncConfig | None = None,
    ) -> BatchSyncResult:
        """Sync multiple collaboration session messages to the group chat.

        Batch syncs messages from the collaboration session to the associated
        WeChat Work group chat.

        Args:
            session: The collaboration session.
            messages: Optional list of messages to sync. If None, syncs all
                unsynced messages from the session.
            wecom_config: Optional WeChat Work configuration.
            sync_config: Optional sync configuration.

        Returns:
            BatchSyncResult with results for all messages.

        Requirements:
            - 9.1: Support syncing collaboration session messages to group chats
        """
        result = BatchSyncResult()

        # Get messages to sync if not provided
        if messages is None:
            messages = await self._get_unsynced_messages(session.id, CHANNEL_WECOM)

        if not messages:
            logger.debug("No messages to sync for session %s", session.id)
            return result

        logger.info(
            "Syncing %d messages from session %s to group chat",
            len(messages),
            session.id,
        )

        # Get WeChat Work config once for all messages
        if wecom_config is None:
            wecom_config = await self._get_wecom_config_for_scenario(session.scenario_id)

        # Sync each message
        for message in messages:
            sync_result = await self.sync_session_message_to_group(
                message=message,
                session=session,
                wecom_config=wecom_config,
                sync_config=sync_config,
            )
            result.add_result(sync_result)

        logger.info(
            "Batch sync completed for session %s: synced=%d, failed=%d, skipped=%d",
            session.id,
            result.synced_count,
            result.failed_count,
            result.skipped_count,
        )

        return result

    # ── Group Chat to Session Sync ───────────────────────────────────────────

    async def sync_group_message_to_session(
        self,
        session: CollaborationSession,
        message_id: str,
        chatid: str,
        sender_id: str,
        sender_name: str | None,
        content: str,
        message_type: str = "text",
        timestamp: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        sync_config: SyncConfig | None = None,
    ) -> MessageSyncResult:
        """Sync a group chat message to the collaboration session.

        Takes a message from the WeChat Work group chat and stores it in
        the collaboration session's message records. Records the source
        channel and original message ID for traceability.

        Args:
            session: The collaboration session to sync to.
            message_id: Original message ID from WeChat Work.
            chatid: Group chat ID.
            sender_id: Sender user ID.
            sender_name: Sender display name.
            content: Message content.
            message_type: Message type (text, markdown, etc.).
            timestamp: Message timestamp (defaults to now).
            metadata: Additional metadata.
            sync_config: Optional sync configuration.

        Returns:
            MessageSyncResult indicating success or failure.

        Requirements:
            - 9.2: Support syncing group chat messages to collaboration sessions
            - 9.3: Support configuring sync direction (unidirectional or bidirectional)
            - 9.4: Record source channel and original message ID for each synced message
        """
        if sync_config is None:
            sync_config = SyncConfig()

        # Check if sync is enabled and direction allows group-to-session (Requirement 9.3)
        if not sync_config.enabled:
            return MessageSyncResult(
                success=True,
                status=SyncStatus.SKIPPED,
                source_message_id=message_id,
                source_channel=CHANNEL_WECOM,
                target_channel=CHANNEL_SYSTEM,
                details={"reason": "Sync disabled"},
            )

        if not sync_config.allows_group_to_session():
            return MessageSyncResult(
                success=True,
                status=SyncStatus.SKIPPED,
                source_message_id=message_id,
                source_channel=CHANNEL_WECOM,
                target_channel=CHANNEL_SYSTEM,
                details={"reason": "Sync direction does not allow group-to-session"},
            )

        # Check for duplicate message (by source_message_id) - Requirement 9.7
        existing = await self._find_message_by_source_id(session.id, message_id)
        if existing is not None:
            return MessageSyncResult(
                success=True,
                status=SyncStatus.SKIPPED,
                source_message_id=message_id,
                source_channel=CHANNEL_WECOM,
                target_channel=CHANNEL_SYSTEM,
                details={
                    "reason": "Message already exists in session",
                    "existing_message_id": str(existing.id),
                },
            )

        try:
            # Create collaboration message record (Requirement 9.4 - records source channel and message ID)
            group_chat_manager = self._get_group_chat_manager()
            collab_message = await group_chat_manager.sync_message_to_session(
                session=session,
                message_id=message_id,
                chatid=chatid,
                sender_id=sender_id,
                sender_name=sender_name,
                content=content,
                message_type=message_type,
                timestamp=timestamp,
                metadata=metadata,
            )

            logger.info(
                "Synced group message %s to session %s as message %s (source: %s)",
                message_id,
                session.id,
                collab_message.id,
                CHANNEL_WECOM,
            )

            return MessageSyncResult(
                success=True,
                status=SyncStatus.SYNCED,
                source_message_id=message_id,
                source_channel=CHANNEL_WECOM,
                target_channel=CHANNEL_SYSTEM,
                target_message_id=str(collab_message.id),
                synced_at=datetime.now(UTC),
                details={
                    "collaboration_message_id": str(collab_message.id),
                    "chatid": chatid,
                    "sender_id": sender_id,
                    "sender_name": sender_name,
                },
            )

        except Exception as exc:
            logger.exception(
                "Failed to sync group message %s to session %s: %s",
                message_id,
                session.id,
                exc,
            )

            return MessageSyncResult(
                success=False,
                status=SyncStatus.FAILED,
                source_message_id=message_id,
                source_channel=CHANNEL_WECOM,
                target_channel=CHANNEL_SYSTEM,
                error=str(exc),
            )

    async def sync_group_messages_to_session(
        self,
        session: CollaborationSession,
        messages: list[ReceivedMessage],
        sync_config: SyncConfig | None = None,
    ) -> BatchSyncResult:
        """Sync multiple group chat messages to the collaboration session.

        Batch syncs messages from the WeChat Work group chat to the
        collaboration session's message records.

        Args:
            session: The collaboration session.
            messages: List of received messages to sync.
            sync_config: Optional sync configuration.

        Returns:
            BatchSyncResult with results for all messages.

        Requirements:
            - 9.2: Support syncing group chat messages to collaboration sessions
        """
        result = BatchSyncResult()

        if not messages:
            logger.debug("No messages to sync to session %s", session.id)
            return result

        logger.info(
            "Syncing %d group messages to session %s",
            len(messages),
            session.id,
        )

        # Sync each message
        for message in messages:
            sync_result = await self.sync_group_message_to_session(
                session=session,
                message_id=message.message_id,
                chatid=message.chatid,
                sender_id=message.sender_id,
                sender_name=message.sender_name,
                content=message.content,
                message_type=message.message_type,
                timestamp=message.timestamp,
                metadata=message.metadata,
                sync_config=sync_config,
            )
            result.add_result(sync_result)

        logger.info(
            "Batch sync completed for session %s: synced=%d, failed=%d, skipped=%d",
            session.id,
            result.synced_count,
            result.failed_count,
            result.skipped_count,
        )

        return result

    # ── Bidirectional Sync ───────────────────────────────────────────────────

    async def sync_all_pending(
        self,
        session: CollaborationSession,
        wecom_config: WeComConfig | None = None,
        sync_config: SyncConfig | None = None,
    ) -> BatchSyncResult:
        """Sync all pending messages for a session in both directions.

        Syncs unsynced session messages to the group chat. Group chat messages
        are typically synced via webhook callbacks, but this method can be used
        for manual sync or recovery.

        Args:
            session: The collaboration session.
            wecom_config: Optional WeChat Work configuration.
            sync_config: Optional sync configuration.

        Returns:
            BatchSyncResult with combined results.

        Requirements:
            - 9.1: Support syncing collaboration session messages to group chats
            - 9.2: Support syncing group chat messages to collaboration sessions
        """
        if sync_config is None:
            sync_config = SyncConfig()

        result = BatchSyncResult()

        # Sync session messages to group chat if direction allows
        if sync_config.direction in (
            SyncDirection.SESSION_TO_GROUP,
            SyncDirection.BIDIRECTIONAL,
        ):
            session_to_group_result = await self.sync_session_messages_to_group(
                session=session,
                wecom_config=wecom_config,
                sync_config=sync_config,
            )
            # Merge results
            for r in session_to_group_result.results:
                result.add_result(r)

        logger.info(
            "Full sync completed for session %s: total=%d, synced=%d, failed=%d",
            session.id,
            result.total_messages,
            result.synced_count,
            result.failed_count,
        )

        return result

    # ── Helper Methods ───────────────────────────────────────────────────────

    def _should_sync_message(
        self,
        message: CollaborationMessage,
        sync_config: SyncConfig,
    ) -> bool:
        """Check if a message should be synced based on configuration.

        Args:
            message: The message to check.
            sync_config: The sync configuration.

        Returns:
            True if the message should be synced, False otherwise.
        """
        # Check message type filters
        if message.message_type == "event" and not sync_config.sync_event_messages:
            return False

        # Check if it's a system message
        if message.source_channel == CHANNEL_SYSTEM:
            if not sync_config.sync_system_messages:
                return False

        return True

    def _format_message_for_group_chat(
        self,
        message: CollaborationMessage,
        apply_formatting: bool = True,
        sync_config: SyncConfig | None = None,
    ) -> tuple[str, str | None]:
        """Format a collaboration message for sending to group chat.

        Applies format conversion to make the message suitable for WeChat Work
        group chat display, using the MessageFormatConverter.

        Args:
            message: The collaboration message.
            apply_formatting: Whether to apply formatting.
            sync_config: Optional sync configuration for format settings.

        Returns:
            Tuple of (formatted_content, format_applied).
            format_applied is the name of the format conversion applied, or None.

        Requirements:
            - 9.5: Support message format conversion for different channel requirements
        """
        if not apply_formatting:
            return message.content, None

        # Determine source format based on message type and source channel
        source_format = self._get_source_format(message)

        # Determine target format
        if sync_config and sync_config.target_format:
            target_format = sync_config.target_format
        else:
            target_format = ChannelFormat.WECOM_MARKDOWN

        # Determine if we should include source info
        include_source_info = True
        if sync_config:
            include_source_info = sync_config.preserve_source_info

        # Don't add source info for messages already from wecom
        if message.source_channel == CHANNEL_WECOM:
            include_source_info = False

        # Convert the message content
        formatted_content = MessageFormatConverter.convert(
            content=message.content,
            source_format=source_format,
            target_format=target_format,
            source_channel=message.source_channel,
            sender_name=message.sender_name,
            include_source_info=include_source_info,
        )

        format_applied = f"{source_format.value} -> {target_format.value}"
        return formatted_content, format_applied

    def _get_source_format(self, message: CollaborationMessage) -> ChannelFormat:
        """Determine the source format of a message.

        Args:
            message: The collaboration message.

        Returns:
            The ChannelFormat of the message content.
        """
        # Check message type first
        if message.message_type == "markdown":
            # Check if it's from WeChat Work (uses WeChat Work markdown)
            if message.source_channel == CHANNEL_WECOM:
                return ChannelFormat.WECOM_MARKDOWN
            return ChannelFormat.STANDARD_MARKDOWN

        # Check source channel
        if message.source_channel == CHANNEL_EMAIL:
            # Email messages might be HTML
            if "<" in message.content and ">" in message.content:
                return ChannelFormat.EMAIL_HTML
            return ChannelFormat.PLAIN_TEXT

        # Default to plain text
        return ChannelFormat.PLAIN_TEXT

    def _format_message_for_email(
        self,
        message: CollaborationMessage,
        sync_config: SyncConfig | None = None,
    ) -> tuple[str, str | None]:
        """Format a collaboration message for sending via email.

        Converts the message content to HTML format suitable for email.

        Args:
            message: The collaboration message.
            sync_config: Optional sync configuration for format settings.

        Returns:
            Tuple of (formatted_content, format_applied).

        Requirements:
            - 9.5: Support message format conversion for different channel requirements
        """
        source_format = self._get_source_format(message)

        # Determine if we should include source info
        include_source_info = True
        if sync_config:
            include_source_info = sync_config.preserve_source_info

        # Don't add source info for messages already from email
        if message.source_channel == CHANNEL_EMAIL:
            include_source_info = False

        formatted_content = MessageFormatConverter.convert(
            content=message.content,
            source_format=source_format,
            target_format=ChannelFormat.EMAIL_HTML,
            source_channel=message.source_channel,
            sender_name=message.sender_name,
            include_source_info=include_source_info,
        )

        format_applied = f"{source_format.value} -> {ChannelFormat.EMAIL_HTML.value}"
        return formatted_content, format_applied

    def _format_message_for_plain_text(
        self,
        message: CollaborationMessage,
        sync_config: SyncConfig | None = None,
    ) -> tuple[str, str | None]:
        """Format a collaboration message as plain text.

        Strips all formatting from the message content.

        Args:
            message: The collaboration message.
            sync_config: Optional sync configuration for format settings.

        Returns:
            Tuple of (formatted_content, format_applied).

        Requirements:
            - 9.5: Support message format conversion for different channel requirements
        """
        source_format = self._get_source_format(message)

        # Determine if we should include source info
        include_source_info = True
        if sync_config:
            include_source_info = sync_config.preserve_source_info

        formatted_content = MessageFormatConverter.convert(
            content=message.content,
            source_format=source_format,
            target_format=ChannelFormat.PLAIN_TEXT,
            source_channel=message.source_channel,
            sender_name=message.sender_name,
            include_source_info=include_source_info,
        )

        format_applied = f"{source_format.value} -> {ChannelFormat.PLAIN_TEXT.value}"
        return formatted_content, format_applied

    async def _get_unsynced_messages(
        self,
        session_id: uuid.UUID,
        target_channel: str,
    ) -> list[CollaborationMessage]:
        """Get messages that haven't been synced to a target channel.

        Args:
            session_id: The collaboration session ID.
            target_channel: The target channel to check sync status for.

        Returns:
            List of unsynced messages.
        """
        # Query messages where target_channel is not in synced_to array
        # and source_channel is not the target (avoid syncing back)
        query = (
            select(CollaborationMessage)
            .where(CollaborationMessage.session_id == session_id)
            .where(CollaborationMessage.source_channel != target_channel)
            .order_by(CollaborationMessage.created_at)
        )

        result = await self._db.execute(query)
        messages = list(result.scalars().all())

        # Filter out already synced messages (check synced_to array)
        unsynced = [
            msg for msg in messages
            if target_channel not in (msg.synced_to or [])
        ]

        return unsynced

    async def _find_message_by_source_id(
        self,
        session_id: uuid.UUID,
        source_message_id: str,
    ) -> CollaborationMessage | None:
        """Find a message by its source message ID.

        Used for deduplication when syncing messages.

        Args:
            session_id: The collaboration session ID.
            source_message_id: The original message ID from the source channel.

        Returns:
            The CollaborationMessage if found, None otherwise.
        """
        query = (
            select(CollaborationMessage)
            .where(CollaborationMessage.session_id == session_id)
            .where(CollaborationMessage.source_message_id == source_message_id)
        )

        result = await self._db.execute(query)
        return result.scalar_one_or_none()

    async def _get_wecom_config_for_scenario(
        self,
        scenario_id: uuid.UUID,
    ) -> WeComConfig | None:
        """Get WeChat Work configuration for a scenario.

        Looks up the WeChat Work notification channel associated with the
        scenario and extracts the configuration.

        Args:
            scenario_id: The scenario ID.

        Returns:
            WeComConfig if found and valid, None otherwise.
        """
        # Get scenario with notification channels
        query = (
            select(Scenario)
            .options(selectinload(Scenario.notification_channels))
            .where(Scenario.id == scenario_id)
        )

        result = await self._db.execute(query)
        scenario = result.scalar_one_or_none()

        if scenario is None:
            logger.warning("Scenario %s not found", scenario_id)
            return None

        # Find WeChat Work channel
        for channel in scenario.notification_channels:
            if channel.channel_type == "wecom" and channel.is_active:
                try:
                    return WeComConfig.from_channel_config(channel.config or {})
                except ValueError as exc:
                    logger.warning(
                        "Invalid WeChat Work config for channel %s: %s",
                        channel.id,
                        exc,
                    )
                    continue

        # Fallback: try to find any active WeChat Work channel
        query = (
            select(NotificationChannel)
            .where(NotificationChannel.channel_type == "wecom")
            .where(NotificationChannel.is_active.is_(True))
            .limit(1)
        )

        result = await self._db.execute(query)
        channel = result.scalar_one_or_none()

        if channel is not None:
            try:
                return WeComConfig.from_channel_config(channel.config or {})
            except ValueError as exc:
                logger.warning(
                    "Invalid WeChat Work config for fallback channel %s: %s",
                    channel.id,
                    exc,
                )

        logger.warning(
            "No valid WeChat Work configuration found for scenario %s",
            scenario_id,
        )
        return None

    async def get_session_with_messages(
        self,
        session_id: uuid.UUID,
    ) -> CollaborationSession | None:
        """Get a collaboration session with its messages loaded.

        Convenience method for loading a session with messages for sync.

        Args:
            session_id: The session ID.

        Returns:
            The CollaborationSession with messages loaded, or None if not found.
        """
        query = (
            select(CollaborationSession)
            .options(selectinload(CollaborationSession.messages))
            .where(CollaborationSession.id == session_id)
        )

        result = await self._db.execute(query)
        return result.scalar_one_or_none()

    async def get_sync_status(
        self,
        session_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Get the sync status for a collaboration session.

        Returns statistics about message sync status for the session.

        Args:
            session_id: The session ID.

        Returns:
            Dictionary with sync status information.
        """
        # Get all messages for the session
        query = (
            select(CollaborationMessage)
            .where(CollaborationMessage.session_id == session_id)
        )

        result = await self._db.execute(query)
        messages = list(result.scalars().all())

        total = len(messages)
        synced_to_wecom = sum(
            1 for msg in messages
            if CHANNEL_WECOM in (msg.synced_to or [])
        )
        from_wecom = sum(
            1 for msg in messages
            if msg.source_channel == CHANNEL_WECOM
        )
        from_other = total - from_wecom

        # Get failure statistics
        failure_query = (
            select(MessageSyncFailure)
            .where(MessageSyncFailure.session_id == session_id)
            .where(MessageSyncFailure.status.in_(["pending", "retrying"]))
        )
        failure_result = await self._db.execute(failure_query)
        pending_failures = len(list(failure_result.scalars().all()))

        return {
            "session_id": str(session_id),
            "total_messages": total,
            "from_wecom": from_wecom,
            "from_other_channels": from_other,
            "synced_to_wecom": synced_to_wecom,
            "pending_sync_to_wecom": from_other - synced_to_wecom,
            "pending_failures": pending_failures,
        }

    # ── Failure Tracking and Retry Methods ───────────────────────────────────

    async def record_sync_failure(
        self,
        message: CollaborationMessage,
        session: CollaborationSession,
        target_channel: str,
        error_reason: str,
        error_code: str | None = None,
        error_details: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> MessageSyncFailure:
        """Record a message sync failure for later retry.

        Creates a failure record that tracks the sync failure and allows
        for manual retry operations.

        Args:
            message: The collaboration message that failed to sync.
            session: The collaboration session.
            target_channel: The channel the message failed to sync to.
            error_reason: Description of why the sync failed.
            error_code: Optional error code from the target channel.
            error_details: Optional additional error details.
            max_retries: Maximum number of retries allowed.

        Returns:
            The created MessageSyncFailure record.

        Requirements:
            - 9.6: Record sync failure reason and support manual retry
        """
        # Check if a failure record already exists for this message and channel
        existing = await self._find_existing_failure(
            message_id=message.id,
            target_channel=target_channel,
        )

        if existing is not None:
            # Update existing failure record
            existing.error_reason = error_reason
            existing.error_code = error_code
            existing.error_details = error_details or {}
            existing.retry_count += 1
            existing.last_retry_at = datetime.now(UTC)

            # Check if max retries exceeded
            if existing.retry_count >= existing.max_retries:
                existing.status = "abandoned"
                logger.warning(
                    "Sync failure for message %s to %s abandoned after %d retries",
                    message.id,
                    target_channel,
                    existing.retry_count,
                )
            else:
                existing.status = "pending"

            await self._db.flush()
            return existing

        # Create new failure record
        failure = MessageSyncFailure(
            message_id=message.id,
            session_id=session.id,
            target_channel=target_channel,
            error_reason=error_reason,
            error_code=error_code,
            error_details=error_details or {},
            retry_count=0,
            max_retries=max_retries,
            status="pending",
        )

        self._db.add(failure)
        await self._db.flush()

        logger.info(
            "Recorded sync failure for message %s to %s: %s",
            message.id,
            target_channel,
            error_reason,
        )

        return failure

    async def retry_failed_sync(
        self,
        failure_id: uuid.UUID,
        wecom_config: WeComConfig | None = None,
        sync_config: SyncConfig | None = None,
    ) -> MessageSyncResult:
        """Manually retry a failed message sync.

        Attempts to resync a message that previously failed. Updates the
        failure record with the retry result.

        Args:
            failure_id: The ID of the failure record to retry.
            wecom_config: Optional WeChat Work configuration.
            sync_config: Optional sync configuration.

        Returns:
            MessageSyncResult indicating success or failure.

        Requirements:
            - 9.6: Support manual retry for failed syncs
        """
        # Get the failure record with message and session
        query = (
            select(MessageSyncFailure)
            .options(
                selectinload(MessageSyncFailure.message),
                selectinload(MessageSyncFailure.session),
            )
            .where(MessageSyncFailure.id == failure_id)
        )

        result = await self._db.execute(query)
        failure = result.scalar_one_or_none()

        if failure is None:
            return MessageSyncResult(
                success=False,
                status=SyncStatus.FAILED,
                error="Failure record not found",
            )

        if failure.status in ("resolved", "abandoned"):
            return MessageSyncResult(
                success=False,
                status=SyncStatus.SKIPPED,
                source_message_id=str(failure.message_id),
                target_channel=failure.target_channel,
                details={"reason": f"Failure already {failure.status}"},
            )

        # Update status to retrying
        failure.status = "retrying"
        failure.last_retry_at = datetime.now(UTC)
        await self._db.flush()

        # Attempt to sync based on target channel
        if failure.target_channel == CHANNEL_WECOM:
            sync_result = await self.sync_session_message_to_group(
                message=failure.message,
                session=failure.session,
                wecom_config=wecom_config,
                sync_config=sync_config,
            )
        else:
            # For other channels, return not supported
            sync_result = MessageSyncResult(
                success=False,
                status=SyncStatus.FAILED,
                source_message_id=str(failure.message_id),
                target_channel=failure.target_channel,
                error=f"Retry not supported for channel: {failure.target_channel}",
            )

        # Update failure record based on result
        failure.retry_count += 1

        if sync_result.success:
            failure.status = "resolved"
            failure.resolved_at = datetime.now(UTC)
            logger.info(
                "Successfully retried sync for message %s to %s",
                failure.message_id,
                failure.target_channel,
            )
        else:
            if failure.retry_count >= failure.max_retries:
                failure.status = "abandoned"
                logger.warning(
                    "Sync retry for message %s to %s abandoned after %d retries",
                    failure.message_id,
                    failure.target_channel,
                    failure.retry_count,
                )
            else:
                failure.status = "pending"
                failure.error_reason = sync_result.error or "Unknown error"

        await self._db.flush()
        return sync_result

    async def retry_all_failed_syncs(
        self,
        session_id: uuid.UUID,
        wecom_config: WeComConfig | None = None,
        sync_config: SyncConfig | None = None,
    ) -> BatchSyncResult:
        """Retry all failed syncs for a collaboration session.

        Attempts to resync all messages that have pending failure records.

        Args:
            session_id: The collaboration session ID.
            wecom_config: Optional WeChat Work configuration.
            sync_config: Optional sync configuration.

        Returns:
            BatchSyncResult with results for all retry attempts.

        Requirements:
            - 9.6: Support manual retry for failed syncs
        """
        result = BatchSyncResult()

        # Get all pending failures for the session
        query = (
            select(MessageSyncFailure)
            .where(MessageSyncFailure.session_id == session_id)
            .where(MessageSyncFailure.status.in_(["pending", "retrying"]))
            .order_by(MessageSyncFailure.created_at)
        )

        query_result = await self._db.execute(query)
        failures = list(query_result.scalars().all())

        if not failures:
            logger.debug("No pending failures to retry for session %s", session_id)
            return result

        logger.info(
            "Retrying %d failed syncs for session %s",
            len(failures),
            session_id,
        )

        for failure in failures:
            sync_result = await self.retry_failed_sync(
                failure_id=failure.id,
                wecom_config=wecom_config,
                sync_config=sync_config,
            )
            result.add_result(sync_result)

        logger.info(
            "Batch retry completed for session %s: synced=%d, failed=%d",
            session_id,
            result.synced_count,
            result.failed_count,
        )

        return result

    async def get_failed_syncs(
        self,
        session_id: uuid.UUID,
        status: str | None = None,
    ) -> list[MessageSyncFailure]:
        """Get failed sync records for a collaboration session.

        Args:
            session_id: The collaboration session ID.
            status: Optional status filter (pending, retrying, resolved, abandoned).

        Returns:
            List of MessageSyncFailure records.

        Requirements:
            - 9.6: Record sync failure reason and support manual retry
        """
        query = (
            select(MessageSyncFailure)
            .options(selectinload(MessageSyncFailure.message))
            .where(MessageSyncFailure.session_id == session_id)
            .order_by(MessageSyncFailure.created_at.desc())
        )

        if status is not None:
            query = query.where(MessageSyncFailure.status == status)

        result = await self._db.execute(query)
        return list(result.scalars().all())

    async def _find_existing_failure(
        self,
        message_id: uuid.UUID,
        target_channel: str,
    ) -> MessageSyncFailure | None:
        """Find an existing failure record for a message and channel.

        Args:
            message_id: The collaboration message ID.
            target_channel: The target channel.

        Returns:
            The MessageSyncFailure if found, None otherwise.
        """
        query = (
            select(MessageSyncFailure)
            .where(MessageSyncFailure.message_id == message_id)
            .where(MessageSyncFailure.target_channel == target_channel)
            .where(MessageSyncFailure.status.in_(["pending", "retrying"]))
        )

        result = await self._db.execute(query)
        return result.scalar_one_or_none()

    # ── Enhanced Deduplication Methods ───────────────────────────────────────

    async def is_message_duplicate(
        self,
        session_id: uuid.UUID,
        source_message_id: str,
        source_channel: str,
    ) -> bool:
        """Check if a message is a duplicate based on source message ID.

        Used to prevent duplicate syncs by checking if a message with the
        same source_message_id already exists in the session.

        Args:
            session_id: The collaboration session ID.
            source_message_id: The original message ID from the source channel.
            source_channel: The source channel of the message.

        Returns:
            True if the message is a duplicate, False otherwise.

        Requirements:
            - 9.7: Avoid duplicate sync through message ID deduplication
        """
        query = (
            select(CollaborationMessage)
            .where(CollaborationMessage.session_id == session_id)
            .where(CollaborationMessage.source_message_id == source_message_id)
            .where(CollaborationMessage.source_channel == source_channel)
        )

        result = await self._db.execute(query)
        return result.scalar_one_or_none() is not None

    async def get_synced_message_ids(
        self,
        session_id: uuid.UUID,
        source_channel: str,
    ) -> set[str]:
        """Get all synced message IDs for a session and source channel.

        Returns a set of source_message_ids that have already been synced
        to the session from the specified channel.

        Args:
            session_id: The collaboration session ID.
            source_channel: The source channel to check.

        Returns:
            Set of source message IDs that have been synced.

        Requirements:
            - 9.7: Avoid duplicate sync through message ID deduplication
        """
        query = (
            select(CollaborationMessage.source_message_id)
            .where(CollaborationMessage.session_id == session_id)
            .where(CollaborationMessage.source_channel == source_channel)
            .where(CollaborationMessage.source_message_id.isnot(None))
        )

        result = await self._db.execute(query)
        return {row[0] for row in result.all() if row[0] is not None}

    async def filter_duplicate_messages(
        self,
        session_id: uuid.UUID,
        messages: list[ReceivedMessage],
    ) -> list[ReceivedMessage]:
        """Filter out duplicate messages from a list of received messages.

        Removes messages that have already been synced to the session
        based on their message_id.

        Args:
            session_id: The collaboration session ID.
            messages: List of received messages to filter.

        Returns:
            List of messages that are not duplicates.

        Requirements:
            - 9.7: Avoid duplicate sync through message ID deduplication
        """
        if not messages:
            return []

        # Get all synced message IDs for wecom channel
        synced_ids = await self.get_synced_message_ids(session_id, CHANNEL_WECOM)

        # Filter out duplicates
        unique_messages = [
            msg for msg in messages
            if msg.message_id not in synced_ids
        ]

        filtered_count = len(messages) - len(unique_messages)
        if filtered_count > 0:
            logger.debug(
                "Filtered %d duplicate messages for session %s",
                filtered_count,
                session_id,
            )

        return unique_messages
