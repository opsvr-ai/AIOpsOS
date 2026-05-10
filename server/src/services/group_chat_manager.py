"""Group Chat Manager — manages WeChat Work (企业微信) group chats for emergency collaboration.

This module provides the GroupChatManager service for creating and managing
group chats through the WeChat Work application API. It supports:
- Creating group chats with configurable members and owners
- Sending text and markdown messages to group chats
- Receiving and parsing messages from group chats
- Querying group chat information
- Error handling and notification for failures

Requirements:
- 7.1: Support automatic group chat creation via WeChat Work application API
- 7.2: Add specified group members based on scenario configuration
- 7.3: Support group chat name templates with variable substitution
- 7.4: Record group chat ID and collaboration session association
- 7.5: Support sending text and markdown messages to group chats
- 7.6: Support receiving messages from group chats and syncing to collaboration session
- 7.7: Parse message content and store to message records
- 7.8: Support querying group chat basic information
- 7.9: Record error information and notify relevant personnel on failure
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.agent import Scenario
from src.models.channel import NotificationChannel
from src.models.collaboration import CollaborationMessage, CollaborationSession
from src.services.channels.wecom.app_client import (
    create_app_chat,
    get_app_chat,
    send_app_chat_message,
    update_app_chat,
)

logger = logging.getLogger(__name__)


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class WeComConfig:
    """WeChat Work API configuration.

    Attributes:
        corp_id: Enterprise ID (企业ID)
        corp_secret: Application secret (应用Secret)
        agent_id: Application agent ID (应用AgentId)
        api_base: API base URL (defaults to cloud API)
    """

    corp_id: str
    corp_secret: str
    agent_id: int
    api_base: str = "https://qyapi.weixin.qq.com"

    @classmethod
    def from_channel_config(cls, config: dict[str, Any]) -> WeComConfig:
        """Create WeComConfig from notification channel configuration.

        Args:
            config: Channel configuration dictionary.

        Returns:
            WeComConfig instance.

        Raises:
            ValueError: If required configuration fields are missing.
        """
        corp_id = config.get("corp_id") or config.get("corpid")
        corp_secret = config.get("corp_secret") or config.get("corpsecret")
        agent_id = config.get("agent_id") or config.get("agentid")

        if not corp_id:
            raise ValueError("Missing required field: corp_id")
        if not corp_secret:
            raise ValueError("Missing required field: corp_secret")
        if not agent_id:
            raise ValueError("Missing required field: agent_id")

        return cls(
            corp_id=str(corp_id),
            corp_secret=str(corp_secret),
            agent_id=int(agent_id),
            api_base=config.get("api_base", "https://qyapi.weixin.qq.com"),
        )


@dataclass
class GroupChatCreateRequest:
    """Request to create a group chat.

    Attributes:
        name: Group chat name (max 50 UTF-8 characters)
        owner: Group owner user ID
        userlist: List of member user IDs (at least 2, including owner)
        chatid: Optional custom chat ID (max 32 characters)
    """

    name: str
    owner: str
    userlist: list[str]
    chatid: str = ""


@dataclass
class GroupChatCreateResult:
    """Result of group chat creation.

    Attributes:
        success: Whether the creation was successful
        chatid: The created group chat ID (if successful)
        error_code: Error code from WeChat Work API (if failed)
        error_message: Error message (if failed)
        details: Additional details about the operation
    """

    success: bool
    chatid: str | None = None
    error_code: int | None = None
    error_message: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupChatInfo:
    """Group chat information.

    Attributes:
        chatid: Group chat ID
        name: Group chat name
        owner: Group owner user ID
        userlist: List of member user IDs
        create_time: Creation timestamp (if available)
    """

    chatid: str
    name: str
    owner: str
    userlist: list[str]
    create_time: int | None = None


@dataclass
class MessageSendResult:
    """Result of sending a message to group chat.

    Attributes:
        success: Whether the message was sent successfully
        error_code: Error code from WeChat Work API (if failed)
        error_message: Error message (if failed)
    """

    success: bool
    error_code: int | None = None
    error_message: str | None = None


@dataclass
class ReceivedMessage:
    """A message received from group chat.

    Attributes:
        message_id: Original message ID from WeChat Work
        chatid: Group chat ID
        sender_id: Sender user ID
        sender_name: Sender display name (if available)
        content: Message content
        message_type: Message type (text, markdown, etc.)
        timestamp: Message timestamp
        metadata: Additional message metadata
    """

    message_id: str
    chatid: str
    sender_id: str
    sender_name: str | None
    content: str
    message_type: str = "text"
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class GroupMemberExtractionResult:
    """Result of extracting group members from collaboration configuration.

    Attributes:
        success: Whether the extraction was successful
        owner: The group owner user ID
        members: List of member user IDs (including owner)
        error_message: Error message if extraction failed
    """

    success: bool
    owner: str
    members: list[str]
    error_message: str | None = None


# ── Group Chat Manager Service ───────────────────────────────────────────────


class GroupChatManager:
    """Service for managing WeChat Work group chats for emergency collaboration.

    This service provides functionality to create and manage group chats
    through the WeChat Work application API. It integrates with the
    collaboration session system to support emergency response workflows.

    Features:
        - Create group chats with configurable members and owners
        - Send text and markdown messages to group chats
        - Receive and sync messages from group chats
        - Query group chat information
        - Error handling with notification support

    Requirements:
        - 7.1: Support automatic group chat creation via WeChat Work API
        - 7.2: Add specified group members based on scenario configuration
        - 7.3: Support group chat name templates with variable substitution
        - 7.4: Record group chat ID and collaboration session association
        - 7.5: Support sending text and markdown messages
        - 7.6: Support receiving messages and syncing to collaboration session
        - 7.7: Parse message content and store to message records
        - 7.8: Support querying group chat basic information
        - 7.9: Record error information and notify on failure
    """

    def __init__(self, db: AsyncSession) -> None:
        """Initialize the GroupChatManager.

        Args:
            db: Async database session for persistence operations.
        """
        self._db = db

    # ── Group Chat Creation ──────────────────────────────────────────────────

    async def create_group_chat(
        self,
        wecom_config: WeComConfig,
        request: GroupChatCreateRequest,
    ) -> GroupChatCreateResult:
        """Create a group chat via WeChat Work application API.

        Creates a new group chat with the specified name, owner, and members.
        The group chat is created through the WeChat Work application API.

        Args:
            wecom_config: WeChat Work API configuration.
            request: Group chat creation request with name, owner, and members.

        Returns:
            GroupChatCreateResult indicating success or failure with details.

        Requirements:
            - 7.1: Support automatic group chat creation via WeChat Work API
        """
        logger.info(
            "Creating group chat: name=%s, owner=%s, members=%d",
            request.name,
            request.owner,
            len(request.userlist),
        )

        # Validate request
        if len(request.userlist) < 2:
            return GroupChatCreateResult(
                success=False,
                error_code=-1,
                error_message="Group chat requires at least 2 members (including owner)",
                details={"members_provided": len(request.userlist)},
            )

        if request.owner not in request.userlist:
            # Auto-add owner to userlist if not present
            request.userlist = [request.owner] + request.userlist

        try:
            # Call WeChat Work API to create group chat
            result = await create_app_chat(
                corp_id=wecom_config.corp_id,
                corp_secret=wecom_config.corp_secret,
                agent_id=wecom_config.agent_id,
                name=request.name,
                owner=request.owner,
                userlist=request.userlist,
                chatid=request.chatid,
                api_base=wecom_config.api_base,
            )

            errcode = result.get("errcode", -1)
            if errcode == 0:
                chatid = result.get("chatid", "")
                logger.info(
                    "Group chat created successfully: chatid=%s, name=%s",
                    chatid,
                    request.name,
                )
                return GroupChatCreateResult(
                    success=True,
                    chatid=chatid,
                    details={
                        "name": request.name,
                        "owner": request.owner,
                        "members_count": len(request.userlist),
                    },
                )
            else:
                errmsg = result.get("errmsg", "Unknown error")
                logger.error(
                    "Failed to create group chat: errcode=%d, errmsg=%s",
                    errcode,
                    errmsg,
                )
                return GroupChatCreateResult(
                    success=False,
                    error_code=errcode,
                    error_message=errmsg,
                    details={"api_response": result},
                )

        except Exception as exc:
            logger.exception("Exception while creating group chat: %s", exc)
            return GroupChatCreateResult(
                success=False,
                error_code=-1,
                error_message=str(exc),
                details={"exception_type": type(exc).__name__},
            )

    async def create_group_chat_for_session(
        self,
        session: CollaborationSession,
        wecom_config: WeComConfig,
        group_name: str,
        owner: str,
        members: list[str],
    ) -> GroupChatCreateResult:
        """Create a group chat for a collaboration session and update the session.

        Creates a group chat and associates it with the collaboration session
        by updating the session's group_chat_id and group_chat_name fields.

        Args:
            session: The collaboration session to associate with the group chat.
            wecom_config: WeChat Work API configuration.
            group_name: Name for the group chat.
            owner: Group owner user ID.
            members: List of member user IDs.

        Returns:
            GroupChatCreateResult indicating success or failure.

        Requirements:
            - 7.1: Support automatic group chat creation via WeChat Work API
            - 7.2: Add specified group members based on scenario configuration
            - 7.4: Record group chat ID and collaboration session association
        """
        request = GroupChatCreateRequest(
            name=group_name,
            owner=owner,
            userlist=members,
        )

        result = await self.create_group_chat(wecom_config, request)

        if result.success and result.chatid:
            # Update session with group chat information (Requirement 7.4)
            session.group_chat_id = result.chatid
            session.group_chat_name = group_name
            await self._db.flush()

            logger.info(
                "Associated group chat %s with session %s",
                result.chatid,
                session.id,
            )

        return result

    # ── Message Sending ──────────────────────────────────────────────────────

    async def send_text_message(
        self,
        wecom_config: WeComConfig,
        chatid: str,
        content: str,
    ) -> MessageSendResult:
        """Send a text message to a group chat.

        Args:
            wecom_config: WeChat Work API configuration.
            chatid: Target group chat ID.
            content: Text message content.

        Returns:
            MessageSendResult indicating success or failure.

        Requirements:
            - 7.5: Support sending text messages to group chats
        """
        return await self._send_message(
            wecom_config=wecom_config,
            chatid=chatid,
            msgtype="text",
            content=content,
        )

    async def send_markdown_message(
        self,
        wecom_config: WeComConfig,
        chatid: str,
        content: str,
    ) -> MessageSendResult:
        """Send a markdown message to a group chat.

        Args:
            wecom_config: WeChat Work API configuration.
            chatid: Target group chat ID.
            content: Markdown message content.

        Returns:
            MessageSendResult indicating success or failure.

        Requirements:
            - 7.5: Support sending markdown messages to group chats
        """
        return await self._send_message(
            wecom_config=wecom_config,
            chatid=chatid,
            msgtype="markdown",
            content=content,
        )

    async def _send_message(
        self,
        wecom_config: WeComConfig,
        chatid: str,
        msgtype: str,
        content: str,
    ) -> MessageSendResult:
        """Internal method to send a message to a group chat.

        Args:
            wecom_config: WeChat Work API configuration.
            chatid: Target group chat ID.
            msgtype: Message type (text or markdown).
            content: Message content.

        Returns:
            MessageSendResult indicating success or failure.
        """
        logger.debug(
            "Sending %s message to group chat %s (length=%d)",
            msgtype,
            chatid,
            len(content),
        )

        try:
            result = await send_app_chat_message(
                corp_id=wecom_config.corp_id,
                corp_secret=wecom_config.corp_secret,
                chatid=chatid,
                msgtype=msgtype,
                content=content,
                api_base=wecom_config.api_base,
            )

            errcode = result.get("errcode", -1)
            if errcode == 0:
                logger.debug("Message sent successfully to group chat %s", chatid)
                return MessageSendResult(success=True)
            else:
                errmsg = result.get("errmsg", "Unknown error")
                logger.error(
                    "Failed to send message to group chat %s: errcode=%d, errmsg=%s",
                    chatid,
                    errcode,
                    errmsg,
                )
                return MessageSendResult(
                    success=False,
                    error_code=errcode,
                    error_message=errmsg,
                )

        except Exception as exc:
            logger.exception(
                "Exception while sending message to group chat %s: %s",
                chatid,
                exc,
            )
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message=str(exc),
            )

    # ── Message Receiving ────────────────────────────────────────────────────

    async def process_received_message(
        self,
        session_id: uuid.UUID,
        message: ReceivedMessage,
    ) -> CollaborationMessage:
        """Process a received message and store it in the collaboration session.

        Parses the received message content and stores it as a
        CollaborationMessage record associated with the session.

        Args:
            session_id: The collaboration session ID.
            message: The received message to process.

        Returns:
            The created CollaborationMessage record.

        Requirements:
            - 7.6: Support receiving messages from group chats and syncing
            - 7.7: Parse message content and store to message records
        """
        logger.info(
            "Processing received message for session %s: sender=%s, type=%s",
            session_id,
            message.sender_id,
            message.message_type,
        )

        # Create collaboration message record (Requirement 7.7)
        collab_message = CollaborationMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            source_channel="wecom",
            source_message_id=message.message_id,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
            content=message.content,
            message_type=message.message_type,
            msg_metadata={
                "chatid": message.chatid,
                "original_timestamp": message.timestamp.isoformat(),
                **message.metadata,
            },
            synced_to=[],  # Will be updated when synced to other channels
            created_at=message.timestamp,
        )

        self._db.add(collab_message)
        await self._db.flush()
        await self._db.refresh(collab_message)

        logger.info(
            "Stored message %s for session %s",
            collab_message.id,
            session_id,
        )

        return collab_message

    async def sync_message_to_session(
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
    ) -> CollaborationMessage:
        """Sync a group chat message to a collaboration session.

        Convenience method that creates a ReceivedMessage and processes it.

        Args:
            session: The collaboration session.
            message_id: Original message ID from WeChat Work.
            chatid: Group chat ID.
            sender_id: Sender user ID.
            sender_name: Sender display name.
            content: Message content.
            message_type: Message type (text, markdown, etc.).
            timestamp: Message timestamp (defaults to now).
            metadata: Additional metadata.

        Returns:
            The created CollaborationMessage record.

        Requirements:
            - 7.6: Support receiving messages from group chats and syncing
        """
        received_message = ReceivedMessage(
            message_id=message_id,
            chatid=chatid,
            sender_id=sender_id,
            sender_name=sender_name,
            content=content,
            message_type=message_type,
            timestamp=timestamp or datetime.now(UTC),
            metadata=metadata or {},
        )

        return await self.process_received_message(session.id, received_message)

    async def send_message_to_session(
        self,
        session: CollaborationSession,
        content: str,
        message_type: str = "text",
        wecom_config: WeComConfig | None = None,
    ) -> MessageSendResult:
        """Send a message to a collaboration session's group chat.

        Convenience method that sends a message to the group chat associated
        with a collaboration session and records it in the session.

        Args:
            session: The collaboration session with an associated group chat.
            content: Message content to send.
            message_type: Message type ('text' or 'markdown').
            wecom_config: Optional WeChat Work configuration. If not provided,
                will attempt to get config from the session's scenario.

        Returns:
            MessageSendResult indicating success or failure.

        Requirements:
            - 7.5: Support sending text and markdown messages to group chats
            - 7.6: Support syncing messages to collaboration session
        """
        if not session.group_chat_id:
            logger.warning(
                "Cannot send message to session %s: no group chat associated",
                session.id,
            )
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="No group chat associated with this session",
            )

        # Get WeChat Work config if not provided
        if wecom_config is None:
            wecom_config = await self.get_wecom_config_for_scenario(session.scenario_id)
            if wecom_config is None:
                return MessageSendResult(
                    success=False,
                    error_code=-1,
                    error_message="No WeChat Work configuration available",
                )

        # Send the message
        if message_type == "markdown":
            result = await self.send_markdown_message(
                wecom_config=wecom_config,
                chatid=session.group_chat_id,
                content=content,
            )
        else:
            result = await self.send_text_message(
                wecom_config=wecom_config,
                chatid=session.group_chat_id,
                content=content,
            )

        # Record the sent message in the session if successful
        if result.success:
            sent_message = CollaborationMessage(
                id=uuid.uuid4(),
                session_id=session.id,
                source_channel="wecom",
                source_message_id=None,  # Outgoing messages don't have a source ID
                sender_id="system",
                sender_name="系统",
                content=content,
                message_type=message_type,
                msg_metadata={
                    "chatid": session.group_chat_id,
                    "direction": "outgoing",
                    "sent_at": datetime.now(UTC).isoformat(),
                },
                synced_to=["wecom"],
                created_at=datetime.now(UTC),
            )
            self._db.add(sent_message)
            await self._db.flush()

            logger.info(
                "Sent %s message to session %s group chat %s",
                message_type,
                session.id,
                session.group_chat_id,
            )

        return result

    async def process_batch_messages(
        self,
        session_id: uuid.UUID,
        messages: list[ReceivedMessage],
    ) -> list[CollaborationMessage]:
        """Process multiple received messages and store them in the collaboration session.

        Batch processes multiple messages for efficiency, useful when syncing
        historical messages or processing webhook batches.

        Args:
            session_id: The collaboration session ID.
            messages: List of received messages to process.

        Returns:
            List of created CollaborationMessage records.

        Requirements:
            - 7.6: Support receiving messages from group chats and syncing
            - 7.7: Parse message content and store to message records
        """
        if not messages:
            return []

        logger.info(
            "Processing batch of %d messages for session %s",
            len(messages),
            session_id,
        )

        created_messages: list[CollaborationMessage] = []

        for message in messages:
            # Parse and create collaboration message record
            collab_message = CollaborationMessage(
                id=uuid.uuid4(),
                session_id=session_id,
                source_channel="wecom",
                source_message_id=message.message_id,
                sender_id=message.sender_id,
                sender_name=message.sender_name,
                content=message.content,
                message_type=message.message_type,
                msg_metadata={
                    "chatid": message.chatid,
                    "original_timestamp": message.timestamp.isoformat(),
                    **message.metadata,
                },
                synced_to=[],
                created_at=message.timestamp,
            )
            self._db.add(collab_message)
            created_messages.append(collab_message)

        await self._db.flush()

        # Refresh all messages to get generated values
        for msg in created_messages:
            await self._db.refresh(msg)

        logger.info(
            "Stored %d messages for session %s",
            len(created_messages),
            session_id,
        )

        return created_messages

    def parse_message_content(
        self,
        content: str,
        message_type: str = "text",
    ) -> dict[str, Any]:
        """Parse message content and extract structured information.

        Analyzes the message content to extract mentions, links, commands,
        and other structured data that can be used for further processing.

        Args:
            content: The raw message content.
            message_type: The message type (text, markdown, etc.).

        Returns:
            Dictionary containing parsed information:
            - mentions: List of mentioned user IDs
            - links: List of URLs found in the content
            - commands: List of command-like patterns (e.g., /command)
            - hashtags: List of hashtags found
            - plain_text: Content with mentions/links removed

        Requirements:
            - 7.7: Parse message content and store to message records
        """
        import re

        result: dict[str, Any] = {
            "mentions": [],
            "links": [],
            "commands": [],
            "hashtags": [],
            "plain_text": content,
        }

        # Extract URLs first (before command extraction to avoid false positives)
        url_pattern = r"https?://[^\s<>\"{}|\\^`\[\]]+"
        links = re.findall(url_pattern, content)
        result["links"] = links

        # Remove URLs from content for command extraction
        content_without_urls = re.sub(url_pattern, "", content)

        # Extract mentions (@user patterns)
        mention_pattern = r"@(\w+)"
        mentions = re.findall(mention_pattern, content)
        result["mentions"] = mentions

        # Extract commands (patterns starting with / at word boundary, not in URLs)
        # Commands must be at the start of a word (preceded by whitespace or start of string)
        command_pattern = r"(?:^|\s)/(\w+)(?:\s+([^/\n]+?))?(?=\s+/|\s*$)"
        commands = re.findall(command_pattern, content_without_urls)
        result["commands"] = [
            {"command": cmd[0], "args": cmd[1].strip() if cmd[1] else ""}
            for cmd in commands
        ]

        # Extract hashtags
        hashtag_pattern = r"#(\w+)"
        hashtags = re.findall(hashtag_pattern, content)
        result["hashtags"] = hashtags

        # Generate plain text (remove mentions and links for cleaner text)
        plain_text = re.sub(mention_pattern, "", content)
        plain_text = re.sub(url_pattern, "", plain_text)
        plain_text = re.sub(r"\s+", " ", plain_text).strip()
        result["plain_text"] = plain_text

        return result

    async def process_received_message_with_parsing(
        self,
        session_id: uuid.UUID,
        message: ReceivedMessage,
    ) -> CollaborationMessage:
        """Process a received message with enhanced content parsing.

        Similar to process_received_message but includes content parsing
        to extract mentions, links, commands, and other structured data.

        Args:
            session_id: The collaboration session ID.
            message: The received message to process.

        Returns:
            The created CollaborationMessage record with parsed metadata.

        Requirements:
            - 7.6: Support receiving messages from group chats and syncing
            - 7.7: Parse message content and store to message records
        """
        logger.info(
            "Processing received message with parsing for session %s: sender=%s, type=%s",
            session_id,
            message.sender_id,
            message.message_type,
        )

        # Parse message content
        parsed_content = self.parse_message_content(
            content=message.content,
            message_type=message.message_type,
        )

        # Create collaboration message record with parsed metadata
        collab_message = CollaborationMessage(
            id=uuid.uuid4(),
            session_id=session_id,
            source_channel="wecom",
            source_message_id=message.message_id,
            sender_id=message.sender_id,
            sender_name=message.sender_name,
            content=message.content,
            message_type=message.message_type,
            msg_metadata={
                "chatid": message.chatid,
                "original_timestamp": message.timestamp.isoformat(),
                "parsed": parsed_content,
                **message.metadata,
            },
            synced_to=[],
            created_at=message.timestamp,
        )

        self._db.add(collab_message)
        await self._db.flush()
        await self._db.refresh(collab_message)

        logger.info(
            "Stored message %s for session %s (mentions=%d, links=%d, commands=%d)",
            collab_message.id,
            session_id,
            len(parsed_content.get("mentions", [])),
            len(parsed_content.get("links", [])),
            len(parsed_content.get("commands", [])),
        )

        return collab_message

    # ── Group Chat Query ─────────────────────────────────────────────────────

    async def get_group_chat_info(
        self,
        wecom_config: WeComConfig,
        chatid: str,
    ) -> GroupChatInfo | None:
        """Get information about a group chat.

        Args:
            wecom_config: WeChat Work API configuration.
            chatid: Group chat ID to query.

        Returns:
            GroupChatInfo if found, None if not found or error.

        Requirements:
            - 7.8: Support querying group chat basic information
        """
        logger.debug("Getting group chat info: chatid=%s", chatid)

        try:
            result = await get_app_chat(
                corp_id=wecom_config.corp_id,
                corp_secret=wecom_config.corp_secret,
                chatid=chatid,
                api_base=wecom_config.api_base,
            )

            errcode = result.get("errcode", -1)
            if errcode == 0:
                chat_info = result.get("chat_info", {})
                return GroupChatInfo(
                    chatid=chat_info.get("chatid", chatid),
                    name=chat_info.get("name", ""),
                    owner=chat_info.get("owner", ""),
                    userlist=chat_info.get("userlist", []),
                    create_time=chat_info.get("create_time"),
                )
            else:
                errmsg = result.get("errmsg", "Unknown error")
                logger.warning(
                    "Failed to get group chat info for %s: errcode=%d, errmsg=%s",
                    chatid,
                    errcode,
                    errmsg,
                )
                return None

        except Exception as exc:
            logger.exception(
                "Exception while getting group chat info for %s: %s",
                chatid,
                exc,
            )
            return None

    async def get_group_chat_info_for_session(
        self,
        session: CollaborationSession,
    ) -> GroupChatInfo | None:
        """Get group chat information for a collaboration session.

        Convenience method that retrieves the WeChat Work configuration
        from the session's scenario and queries the group chat info.

        Args:
            session: The collaboration session with an associated group chat.

        Returns:
            GroupChatInfo if found, None if no group chat is associated
            or if the query fails.

        Requirements:
            - 7.8: Support querying group chat basic information
        """
        if not session.group_chat_id:
            logger.debug(
                "Session %s has no associated group chat",
                session.id,
            )
            return None

        # Get WeChat Work configuration for the scenario
        wecom_config = await self.get_wecom_config_for_scenario(session.scenario_id)
        if wecom_config is None:
            logger.warning(
                "No WeChat Work configuration found for session %s scenario %s",
                session.id,
                session.scenario_id,
            )
            return None

        return await self.get_group_chat_info(
            wecom_config=wecom_config,
            chatid=session.group_chat_id,
        )

    # ── Group Chat Update ────────────────────────────────────────────────────

    async def update_group_chat(
        self,
        wecom_config: WeComConfig,
        chatid: str,
        name: str = "",
        owner: str = "",
        add_users: list[str] | None = None,
        remove_users: list[str] | None = None,
    ) -> MessageSendResult:
        """Update group chat information.

        Args:
            wecom_config: WeChat Work API configuration.
            chatid: Group chat ID to update.
            name: New group name (optional).
            owner: New group owner (optional).
            add_users: Users to add to the group (optional).
            remove_users: Users to remove from the group (optional).

        Returns:
            MessageSendResult indicating success or failure.
        """
        logger.info(
            "Updating group chat %s: name=%s, owner=%s, add=%s, remove=%s",
            chatid,
            name or "(unchanged)",
            owner or "(unchanged)",
            add_users,
            remove_users,
        )

        try:
            result = await update_app_chat(
                corp_id=wecom_config.corp_id,
                corp_secret=wecom_config.corp_secret,
                chatid=chatid,
                name=name,
                owner=owner,
                add_user_list=add_users,
                del_user_list=remove_users,
                api_base=wecom_config.api_base,
            )

            errcode = result.get("errcode", -1)
            if errcode == 0:
                logger.info("Group chat %s updated successfully", chatid)
                return MessageSendResult(success=True)
            else:
                errmsg = result.get("errmsg", "Unknown error")
                logger.error(
                    "Failed to update group chat %s: errcode=%d, errmsg=%s",
                    chatid,
                    errcode,
                    errmsg,
                )
                return MessageSendResult(
                    success=False,
                    error_code=errcode,
                    error_message=errmsg,
                )

        except Exception as exc:
            logger.exception(
                "Exception while updating group chat %s: %s",
                chatid,
                exc,
            )
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message=str(exc),
            )

    # ── Error Handling and Notification ──────────────────────────────────────

    async def handle_creation_failure(
        self,
        session: CollaborationSession,
        error_code: int | None,
        error_message: str | None,
        notify_users: list[str] | None = None,
        send_email_notification: bool = True,
    ) -> None:
        """Handle group chat creation failure.

        Records the error information and notifies relevant personnel via email.

        Args:
            session: The collaboration session.
            error_code: Error code from the API.
            error_message: Error message.
            notify_users: List of user IDs to notify about the failure.
            send_email_notification: Whether to send email notification (default True).

        Requirements:
            - 7.9: Record error information and notify relevant personnel on failure
        """
        logger.error(
            "Group chat creation failed for session %s: code=%s, message=%s",
            session.id,
            error_code,
            error_message,
        )

        # Update session progress to record the failure
        progress = session.progress_summary or {}
        errors = progress.get("errors", [])
        errors.append({
            "type": "group_chat_creation_failed",
            "error_code": error_code,
            "error_message": error_message,
            "timestamp": datetime.now(UTC).isoformat(),
            "notified_users": notify_users or [],
        })
        progress["errors"] = errors
        session.progress_summary = progress
        await self._db.flush()

        # Create a system message to record the failure
        system_message = CollaborationMessage(
            id=uuid.uuid4(),
            session_id=session.id,
            source_channel="system",
            source_message_id=None,
            sender_id="system",
            sender_name="系统",
            content=f"群聊创建失败: {error_message} (错误码: {error_code})",
            message_type="event",
            msg_metadata={
                "event_type": "group_chat_creation_failed",
                "error_code": error_code,
                "error_message": error_message,
            },
            synced_to=[],
        )
        self._db.add(system_message)
        await self._db.flush()

        # Send email notification to relevant personnel (Requirement 7.9)
        if send_email_notification:
            await self._send_failure_notification_email(
                session=session,
                error_code=error_code,
                error_message=error_message,
                notify_users=notify_users,
            )

    async def _send_failure_notification_email(
        self,
        session: CollaborationSession,
        error_code: int | None,
        error_message: str | None,
        notify_users: list[str] | None = None,
    ) -> None:
        """Send email notification about group chat creation failure.

        Args:
            session: The collaboration session.
            error_code: Error code from the API.
            error_message: Error message.
            notify_users: List of user IDs to notify.

        Requirements:
            - 7.9: Notify relevant personnel on failure
        """
        # Import here to avoid circular imports
        from src.services.email_notification import EmailNotificationService

        try:
            email_service = EmailNotificationService(self._db)

            # Build custom email for failure notification
            subject_template = "[应急协同] {scenario_name} - 群聊创建失败"
            body_template = """
<h2>群聊创建失败通知</h2>

<p>在创建应急协同群聊时发生错误，请及时处理。</p>

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
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>错误码</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{error_code}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>错误信息</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{error_message}</td>
    </tr>
    <tr>
        <td style="padding: 8px; border: 1px solid #ddd;"><strong>发生时间</strong></td>
        <td style="padding: 8px; border: 1px solid #ddd;">{failure_time}</td>
    </tr>
</table>

<h3>建议操作</h3>
<ul>
    <li>检查企业微信应用配置是否正确</li>
    <li>确认群成员用户ID是否有效</li>
    <li>检查企业微信API调用限制</li>
    <li>手动创建群聊并关联到协同会话</li>
</ul>

<hr/>
<p style="color: #666; font-size: 12px;">
    此邮件由 AIOpsOS 应急协同系统自动发送，请勿直接回复。
</p>
"""

            extra_variables = {
                "error_code": str(error_code) if error_code is not None else "N/A",
                "error_message": error_message or "未知错误",
                "failure_time": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S"),
            }

            result = await email_service.send_custom_email(
                session=session,
                subject_template=subject_template,
                body_template=body_template,
                extra_variables=extra_variables,
                email_type="group_chat_creation_failed",
            )

            if result.success:
                logger.info(
                    "Sent failure notification email for session %s to %d recipients",
                    session.id,
                    len(result.recipients),
                )
            else:
                logger.warning(
                    "Failed to send failure notification email for session %s: %s",
                    session.id,
                    result.error,
                )

        except Exception as exc:
            # Log but don't raise - email notification failure shouldn't block the main flow
            logger.exception(
                "Exception while sending failure notification email for session %s: %s",
                session.id,
                exc,
            )

    # ── Helper Methods ───────────────────────────────────────────────────────

    async def get_wecom_config_for_scenario(
        self,
        scenario_id: uuid.UUID,
    ) -> WeComConfig | None:
        """Get WeChat Work configuration for a scenario.

        Looks up the WeChat Work notification channel associated with the
        scenario and extracts the API configuration.

        Args:
            scenario_id: The scenario ID.

        Returns:
            WeComConfig if found and valid, None otherwise.
        """
        # Get scenario with notification channels
        result = await self._db.execute(
            select(Scenario)
            .options(selectinload(Scenario.notification_channels))
            .where(Scenario.id == scenario_id)
        )
        scenario = result.scalar_one_or_none()

        if not scenario:
            logger.warning("Scenario %s not found", scenario_id)
            return None

        # Look for WeCom channel in associated channels
        for channel in scenario.notification_channels:
            if channel.channel_type == "wecom" and channel.is_active:
                try:
                    return WeComConfig.from_channel_config(channel.config or {})
                except ValueError as exc:
                    logger.warning(
                        "Invalid WeCom config for channel %s: %s",
                        channel.id,
                        exc,
                    )
                    continue

        # Fallback: look for any active WeCom channel
        result = await self._db.execute(
            select(NotificationChannel).where(
                NotificationChannel.channel_type == "wecom",
                NotificationChannel.is_active == True,  # noqa: E712
            )
        )
        channel = result.scalar_one_or_none()

        if channel:
            try:
                return WeComConfig.from_channel_config(channel.config or {})
            except ValueError as exc:
                logger.warning(
                    "Invalid WeCom config for fallback channel %s: %s",
                    channel.id,
                    exc,
                )

        logger.warning("No valid WeCom channel found for scenario %s", scenario_id)
        return None

    def generate_group_name(
        self,
        template: str,
        scenario_name: str,
        trigger_reason: str | None = None,
        alert_title: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a group chat name from a template.

        Substitutes variables in the template with actual values.
        Supported variables:
        - {scenario_name}: Name of the scenario
        - {timestamp}: Current timestamp in YYYY-MM-DD HH:MM format
        - {trigger_reason}: The trigger reason (truncated if too long)
        - {alert_title}: Alert title (truncated if too long)
        - Any additional kwargs

        Args:
            template: The name template string.
            scenario_name: Name of the triggering scenario.
            trigger_reason: Optional trigger reason.
            alert_title: Optional alert title.
            **kwargs: Additional variables for substitution.

        Returns:
            The generated group name (max 50 characters for WeChat Work).

        Requirements:
            - 7.3: Support group chat name templates with variable substitution
        """
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")

        # Truncate long values
        def truncate(value: str | None, max_len: int = 20) -> str:
            if not value:
                return ""
            if len(value) > max_len:
                return value[:max_len] + "..."
            return value

        # Build substitution dict
        substitutions = {
            "scenario_name": truncate(scenario_name, 15),
            "timestamp": timestamp,
            "trigger_reason": truncate(trigger_reason, 20),
            "alert_title": truncate(alert_title, 20),
            **kwargs,
        }

        # Perform substitution
        result = template
        for key, value in substitutions.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))

        # Ensure max length of 50 characters for WeChat Work
        if len(result) > 50:
            result = result[:47] + "..."

        return result

    # ── Scenario-Based Group Chat Creation ───────────────────────────────────

    async def create_group_chat_from_scenario_config(
        self,
        session: CollaborationSession,
        scenario: Scenario,
        trigger_reason: str | None = None,
        alert_title: str | None = None,
        extra_members: list[str] | None = None,
        **template_vars: Any,
    ) -> GroupChatCreateResult:
        """Create a group chat based on scenario collaboration configuration.

        This method extracts group chat settings from the scenario's
        collaboration_config and creates a group chat with the configured
        members, owner, and name template. It also records the association
        between the group chat and the collaboration session.

        Args:
            session: The collaboration session to associate with the group chat.
            scenario: The scenario containing collaboration configuration.
            trigger_reason: Optional trigger reason for name template.
            alert_title: Optional alert title for name template.
            extra_members: Additional members to add beyond the configured list.
            **template_vars: Additional variables for name template substitution.

        Returns:
            GroupChatCreateResult indicating success or failure.

        Requirements:
            - 7.2: Add specified group members based on scenario configuration
            - 7.3: Support group chat name templates with variable substitution
            - 7.4: Record group chat ID and collaboration session association
        """
        logger.info(
            "Creating group chat from scenario config: scenario=%s, session=%s",
            scenario.id,
            session.id,
        )

        # Get WeChat Work configuration
        wecom_config = await self.get_wecom_config_for_scenario(scenario.id)
        if not wecom_config:
            error_msg = "No valid WeChat Work configuration found for scenario"
            logger.error(
                "%s: scenario=%s",
                error_msg,
                scenario.id,
            )
            await self.handle_creation_failure(
                session=session,
                error_code=-1,
                error_message=error_msg,
            )
            return GroupChatCreateResult(
                success=False,
                error_code=-1,
                error_message=error_msg,
            )

        # Extract configuration from scenario (Requirement 7.2)
        collab_config = scenario.collaboration_config or {}
        members_result = self.extract_group_members_from_config(
            collab_config=collab_config,
            extra_members=extra_members,
        )

        if not members_result.success:
            await self.handle_creation_failure(
                session=session,
                error_code=-1,
                error_message=members_result.error_message,
            )
            return GroupChatCreateResult(
                success=False,
                error_code=-1,
                error_message=members_result.error_message,
            )

        # Generate group name from template (Requirement 7.3)
        group_name = self.generate_group_name_from_config(
            collab_config=collab_config,
            scenario_name=scenario.name,
            trigger_reason=trigger_reason,
            alert_title=alert_title,
            **template_vars,
        )

        # Create the group chat and associate with session (Requirement 7.4)
        result = await self.create_group_chat_for_session(
            session=session,
            wecom_config=wecom_config,
            group_name=group_name,
            owner=members_result.owner,
            members=members_result.members,
        )

        if not result.success:
            await self.handle_creation_failure(
                session=session,
                error_code=result.error_code,
                error_message=result.error_message,
                notify_users=members_result.members,
            )

        return result

    def extract_group_members_from_config(
        self,
        collab_config: dict[str, Any],
        extra_members: list[str] | None = None,
    ) -> GroupMemberExtractionResult:
        """Extract and validate group members from collaboration configuration.

        Extracts the group owner and member list from the scenario's
        collaboration_config. Validates that the configuration contains
        the required fields and that there are enough members.

        Args:
            collab_config: The collaboration_config from the scenario.
            extra_members: Additional members to add to the configured list.

        Returns:
            GroupMemberExtractionResult with owner, members, and validation status.

        Requirements:
            - 7.2: Add specified group members based on scenario configuration
        """
        # Extract owner
        owner = collab_config.get("group_owner", "")
        if not owner:
            return GroupMemberExtractionResult(
                success=False,
                owner="",
                members=[],
                error_message="Missing required field 'group_owner' in collaboration config",
            )

        # Extract configured members
        configured_members = collab_config.get("group_members", [])
        if not isinstance(configured_members, list):
            configured_members = []

        # Combine with extra members
        all_members = list(configured_members)
        if extra_members:
            for member in extra_members:
                if member not in all_members:
                    all_members.append(member)

        # Ensure owner is in the member list
        if owner not in all_members:
            all_members.insert(0, owner)

        # Validate minimum member count (WeChat Work requires at least 2)
        if len(all_members) < 2:
            return GroupMemberExtractionResult(
                success=False,
                owner=owner,
                members=all_members,
                error_message=f"Group chat requires at least 2 members, got {len(all_members)}",
            )

        logger.debug(
            "Extracted group members: owner=%s, members=%s",
            owner,
            all_members,
        )

        return GroupMemberExtractionResult(
            success=True,
            owner=owner,
            members=all_members,
        )

    def generate_group_name_from_config(
        self,
        collab_config: dict[str, Any],
        scenario_name: str,
        trigger_reason: str | None = None,
        alert_title: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Generate a group chat name from collaboration configuration.

        Uses the group_name_template from the collaboration config if available,
        otherwise falls back to a default template.

        Args:
            collab_config: The collaboration_config from the scenario.
            scenario_name: Name of the triggering scenario.
            trigger_reason: Optional trigger reason for substitution.
            alert_title: Optional alert title for substitution.
            **kwargs: Additional variables for template substitution.

        Returns:
            The generated group name (max 50 characters).

        Requirements:
            - 7.3: Support group chat name templates with variable substitution
        """
        # Get template from config or use default
        template = collab_config.get(
            "group_name_template",
            "[应急] {scenario_name} - {timestamp}",
        )

        return self.generate_group_name(
            template=template,
            scenario_name=scenario_name,
            trigger_reason=trigger_reason,
            alert_title=alert_title,
            **kwargs,
        )

    async def add_members_to_group(
        self,
        session: CollaborationSession,
        new_members: list[str],
    ) -> MessageSendResult:
        """Add new members to an existing group chat associated with a session.

        Args:
            session: The collaboration session with an associated group chat.
            new_members: List of user IDs to add to the group.

        Returns:
            MessageSendResult indicating success or failure.

        Requirements:
            - 7.2: Add specified group members based on scenario configuration
        """
        if not session.group_chat_id:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="Session has no associated group chat",
            )

        if not new_members:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="No members specified to add",
            )

        # Get WeChat Work configuration
        wecom_config = await self.get_wecom_config_for_scenario(session.scenario_id)
        if not wecom_config:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="No valid WeChat Work configuration found",
            )

        logger.info(
            "Adding members to group chat %s: %s",
            session.group_chat_id,
            new_members,
        )

        return await self.update_group_chat(
            wecom_config=wecom_config,
            chatid=session.group_chat_id,
            add_users=new_members,
        )

    async def remove_members_from_group(
        self,
        session: CollaborationSession,
        members_to_remove: list[str],
    ) -> MessageSendResult:
        """Remove members from an existing group chat associated with a session.

        Args:
            session: The collaboration session with an associated group chat.
            members_to_remove: List of user IDs to remove from the group.

        Returns:
            MessageSendResult indicating success or failure.

        Requirements:
            - 7.2: Add specified group members based on scenario configuration
        """
        if not session.group_chat_id:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="Session has no associated group chat",
            )

        if not members_to_remove:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="No members specified to remove",
            )

        # Get WeChat Work configuration
        wecom_config = await self.get_wecom_config_for_scenario(session.scenario_id)
        if not wecom_config:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="No valid WeChat Work configuration found",
            )

        logger.info(
            "Removing members from group chat %s: %s",
            session.group_chat_id,
            members_to_remove,
        )

        return await self.update_group_chat(
            wecom_config=wecom_config,
            chatid=session.group_chat_id,
            remove_users=members_to_remove,
        )

    async def update_group_name(
        self,
        session: CollaborationSession,
        new_name: str,
    ) -> MessageSendResult:
        """Update the name of a group chat associated with a session.

        Args:
            session: The collaboration session with an associated group chat.
            new_name: The new name for the group chat.

        Returns:
            MessageSendResult indicating success or failure.
        """
        if not session.group_chat_id:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="Session has no associated group chat",
            )

        if not new_name:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="New name cannot be empty",
            )

        # Get WeChat Work configuration
        wecom_config = await self.get_wecom_config_for_scenario(session.scenario_id)
        if not wecom_config:
            return MessageSendResult(
                success=False,
                error_code=-1,
                error_message="No valid WeChat Work configuration found",
            )

        # Truncate name if too long
        if len(new_name) > 50:
            new_name = new_name[:47] + "..."

        logger.info(
            "Updating group chat name for %s: %s",
            session.group_chat_id,
            new_name,
        )

        result = await self.update_group_chat(
            wecom_config=wecom_config,
            chatid=session.group_chat_id,
            name=new_name,
        )

        if result.success:
            # Update session record
            session.group_chat_name = new_name
            await self._db.flush()

        return result
