"""Unit tests for GroupChatManager service.

Tests the group chat message sending, receiving, and parsing functionality.

Requirements:
- 7.5: Support sending text and markdown messages to group chats
- 7.6: Support receiving messages from group chats and syncing to collaboration session
- 7.7: Parse message content and store to message records
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.group_chat_manager import (
    GroupChatManager,
    ReceivedMessage,
    WeComConfig,
)


class TestParseMessageContent:
    """Tests for parse_message_content method.

    Validates: Requirements 7.7
    """

    def test_parse_mentions(self) -> None:
        """Test extraction of @mentions from message content."""
        manager = GroupChatManager(None)
        content = "Hello @user1 and @user2, please check this."

        result = manager.parse_message_content(content)

        assert result["mentions"] == ["user1", "user2"]

    def test_parse_links(self) -> None:
        """Test extraction of URLs from message content."""
        manager = GroupChatManager(None)
        content = "Check https://example.com/page and http://test.org/path"

        result = manager.parse_message_content(content)

        assert len(result["links"]) == 2
        assert "https://example.com/page" in result["links"]
        assert "http://test.org/path" in result["links"]

    def test_parse_commands(self) -> None:
        """Test extraction of /commands from message content."""
        manager = GroupChatManager(None)
        content = "/status check /help"

        result = manager.parse_message_content(content)

        assert len(result["commands"]) == 2
        assert result["commands"][0]["command"] == "status"
        assert result["commands"][0]["args"] == "check"
        assert result["commands"][1]["command"] == "help"

    def test_parse_commands_ignores_url_paths(self) -> None:
        """Test that /paths in URLs are not treated as commands."""
        manager = GroupChatManager(None)
        content = "Check https://example.com/api/test and run /status"

        result = manager.parse_message_content(content)

        # Should only find /status, not /api or /test from URL
        assert len(result["commands"]) == 1
        assert result["commands"][0]["command"] == "status"

    def test_parse_hashtags(self) -> None:
        """Test extraction of #hashtags from message content."""
        manager = GroupChatManager(None)
        content = "This is #urgent and #critical"

        result = manager.parse_message_content(content)

        assert result["hashtags"] == ["urgent", "critical"]

    def test_parse_plain_text(self) -> None:
        """Test generation of plain text with mentions and links removed."""
        manager = GroupChatManager(None)
        content = "Hello @user check https://example.com please"

        result = manager.parse_message_content(content)

        assert "Hello" in result["plain_text"]
        assert "please" in result["plain_text"]
        assert "@user" not in result["plain_text"]
        assert "https://example.com" not in result["plain_text"]

    def test_parse_empty_content(self) -> None:
        """Test parsing of empty content."""
        manager = GroupChatManager(None)

        result = manager.parse_message_content("")

        assert result["mentions"] == []
        assert result["links"] == []
        assert result["commands"] == []
        assert result["hashtags"] == []
        assert result["plain_text"] == ""

    def test_parse_complex_message(self) -> None:
        """Test parsing of a complex message with multiple elements."""
        manager = GroupChatManager(None)
        content = "@admin 请检查 https://monitor.example.com/alerts #P0 /ack incident-123"

        result = manager.parse_message_content(content)

        assert "admin" in result["mentions"]
        assert "https://monitor.example.com/alerts" in result["links"]
        assert "P0" in result["hashtags"]
        assert any(cmd["command"] == "ack" for cmd in result["commands"])


class TestSendTextMessage:
    """Tests for send_text_message method.

    Validates: Requirements 7.5
    """

    @pytest.mark.asyncio
    async def test_send_text_message_success(self) -> None:
        """Test successful text message sending."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.send_app_chat_message",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {"errcode": 0, "errmsg": "ok"}

            result = await manager.send_text_message(
                wecom_config=wecom_config,
                chatid="test_chat_id",
                content="Hello, this is a test message",
            )

            assert result.success is True
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_text_message_failure(self) -> None:
        """Test text message sending failure."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.send_app_chat_message",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {"errcode": 40001, "errmsg": "invalid credential"}

            result = await manager.send_text_message(
                wecom_config=wecom_config,
                chatid="test_chat_id",
                content="Hello",
            )

            assert result.success is False
            assert result.error_code == 40001


class TestSendMarkdownMessage:
    """Tests for send_markdown_message method.

    Validates: Requirements 7.5
    """

    @pytest.mark.asyncio
    async def test_send_markdown_message_success(self) -> None:
        """Test successful markdown message sending."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.send_app_chat_message",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {"errcode": 0, "errmsg": "ok"}

            result = await manager.send_markdown_message(
                wecom_config=wecom_config,
                chatid="test_chat_id",
                content="# Alert\n**Critical** issue detected",
            )

            assert result.success is True
            mock_send.assert_called_once()
            # Verify markdown type was used
            call_kwargs = mock_send.call_args.kwargs
            assert call_kwargs["msgtype"] == "markdown"


class TestProcessReceivedMessage:
    """Tests for process_received_message method.

    Validates: Requirements 7.6, 7.7
    """

    @pytest.mark.asyncio
    async def test_process_received_message_stores_correctly(self) -> None:
        """Test that received messages are stored correctly."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        manager = GroupChatManager(mock_db)

        session_id = uuid.uuid4()
        message = ReceivedMessage(
            message_id="msg_123",
            chatid="chat_456",
            sender_id="user_789",
            sender_name="Test User",
            content="Hello from group chat",
            message_type="text",
            timestamp=datetime.now(UTC),
        )

        result = await manager.process_received_message(session_id, message)
        _ = result  # Used for side effects, result verified via mock assertions

        # Verify message was added to database
        mock_db.add.assert_called_once()
        mock_db.flush.assert_called_once()

        # Verify the created message has correct attributes
        added_message = mock_db.add.call_args[0][0]
        assert added_message.session_id == session_id
        assert added_message.source_channel == "wecom"
        assert added_message.source_message_id == "msg_123"
        assert added_message.sender_id == "user_789"
        assert added_message.content == "Hello from group chat"


class TestProcessReceivedMessageWithParsing:
    """Tests for process_received_message_with_parsing method.

    Validates: Requirements 7.6, 7.7
    """

    @pytest.mark.asyncio
    async def test_process_with_parsing_includes_parsed_metadata(self) -> None:
        """Test that parsed content is included in message metadata."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        manager = GroupChatManager(mock_db)

        session_id = uuid.uuid4()
        message = ReceivedMessage(
            message_id="msg_123",
            chatid="chat_456",
            sender_id="user_789",
            sender_name="Test User",
            content="@admin please check https://example.com #urgent",
            message_type="text",
            timestamp=datetime.now(UTC),
        )

        result = await manager.process_received_message_with_parsing(session_id, message)
        _ = result  # Used for side effects, result verified via mock assertions

        # Verify message was added
        mock_db.add.assert_called_once()

        # Verify parsed content is in metadata
        added_message = mock_db.add.call_args[0][0]
        assert "parsed" in added_message.msg_metadata
        parsed = added_message.msg_metadata["parsed"]
        assert "admin" in parsed["mentions"]
        assert "https://example.com" in parsed["links"]
        assert "urgent" in parsed["hashtags"]


class TestProcessBatchMessages:
    """Tests for process_batch_messages method.

    Validates: Requirements 7.6, 7.7
    """

    @pytest.mark.asyncio
    async def test_process_batch_messages_empty_list(self) -> None:
        """Test processing empty message list."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        result = await manager.process_batch_messages(uuid.uuid4(), [])

        assert result == []
        mock_db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_batch_messages_multiple(self) -> None:
        """Test processing multiple messages in batch."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.refresh = AsyncMock()

        manager = GroupChatManager(mock_db)

        session_id = uuid.uuid4()
        messages = [
            ReceivedMessage(
                message_id=f"msg_{i}",
                chatid="chat_456",
                sender_id=f"user_{i}",
                sender_name=f"User {i}",
                content=f"Message {i}",
                message_type="text",
                timestamp=datetime.now(UTC),
            )
            for i in range(3)
        ]

        result = await manager.process_batch_messages(session_id, messages)

        # Verify all messages were added
        assert mock_db.add.call_count == 3
        assert len(result) == 3


class TestSendMessageToSession:
    """Tests for send_message_to_session method.

    Validates: Requirements 7.5, 7.6
    """

    @pytest.mark.asyncio
    async def test_send_message_to_session_no_group_chat(self) -> None:
        """Test sending message to session without group chat."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        # Create mock session without group chat
        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.group_chat_id = None

        result = await manager.send_message_to_session(
            session=mock_session,
            content="Test message",
        )

        assert result.success is False
        assert "No group chat" in result.error_message

    @pytest.mark.asyncio
    async def test_send_message_to_session_success(self) -> None:
        """Test successful message sending to session."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        manager = GroupChatManager(mock_db)

        # Create mock session with group chat
        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.group_chat_id = "test_chat_id"

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.send_app_chat_message",
            new_callable=AsyncMock,
        ) as mock_send:
            mock_send.return_value = {"errcode": 0, "errmsg": "ok"}

            result = await manager.send_message_to_session(
                session=mock_session,
                content="Test message",
                wecom_config=wecom_config,
            )

            assert result.success is True
            # Verify message was recorded in session
            mock_db.add.assert_called_once()


class TestExtractGroupMembersFromConfig:
    """Tests for extract_group_members_from_config method.

    Validates: Requirements 7.2
    """

    def test_extract_valid_config(self) -> None:
        """Test extraction from valid configuration."""
        manager = GroupChatManager(None)
        config = {
            "group_owner": "admin",
            "group_members": ["user1", "user2", "user3"],
        }

        result = manager.extract_group_members_from_config(config)

        assert result.success is True
        assert result.owner == "admin"
        assert "admin" in result.members
        assert "user1" in result.members
        assert "user2" in result.members
        assert "user3" in result.members
        assert result.error_message is None

    def test_extract_missing_owner(self) -> None:
        """Test extraction fails when owner is missing."""
        manager = GroupChatManager(None)
        config = {
            "group_members": ["user1", "user2"],
        }

        result = manager.extract_group_members_from_config(config)

        assert result.success is False
        assert "group_owner" in result.error_message

    def test_extract_empty_owner(self) -> None:
        """Test extraction fails when owner is empty string."""
        manager = GroupChatManager(None)
        config = {
            "group_owner": "",
            "group_members": ["user1", "user2"],
        }

        result = manager.extract_group_members_from_config(config)

        assert result.success is False
        assert "group_owner" in result.error_message

    def test_extract_not_enough_members(self) -> None:
        """Test extraction fails when there are not enough members."""
        manager = GroupChatManager(None)
        config = {
            "group_owner": "admin",
            "group_members": [],
        }

        result = manager.extract_group_members_from_config(config)

        assert result.success is False
        assert "at least 2 members" in result.error_message

    def test_extract_owner_auto_added_to_members(self) -> None:
        """Test that owner is automatically added to members if not present."""
        manager = GroupChatManager(None)
        config = {
            "group_owner": "admin",
            "group_members": ["user1", "user2"],  # admin not in list
        }

        result = manager.extract_group_members_from_config(config)

        assert result.success is True
        assert "admin" in result.members
        assert result.members[0] == "admin"  # Owner should be first

    def test_extract_with_extra_members(self) -> None:
        """Test extraction with additional extra members."""
        manager = GroupChatManager(None)
        config = {
            "group_owner": "admin",
            "group_members": ["user1"],
        }

        result = manager.extract_group_members_from_config(
            config, extra_members=["user2", "user3"]
        )

        assert result.success is True
        assert "user2" in result.members
        assert "user3" in result.members

    def test_extract_extra_members_no_duplicates(self) -> None:
        """Test that extra members don't create duplicates."""
        manager = GroupChatManager(None)
        config = {
            "group_owner": "admin",
            "group_members": ["user1", "user2"],
        }

        result = manager.extract_group_members_from_config(
            config, extra_members=["user1", "user3"]  # user1 already in list
        )

        assert result.success is True
        # user1 should only appear once
        assert result.members.count("user1") == 1
        assert "user3" in result.members

    def test_extract_invalid_members_type(self) -> None:
        """Test extraction handles invalid members type gracefully."""
        manager = GroupChatManager(None)
        config = {
            "group_owner": "admin",
            "group_members": "not_a_list",  # Invalid type
        }

        result = manager.extract_group_members_from_config(
            config, extra_members=["user1"]
        )

        # Should still work with extra members
        assert result.success is True
        assert "admin" in result.members
        assert "user1" in result.members


class TestGenerateGroupNameFromConfig:
    """Tests for generate_group_name_from_config method.

    Validates: Requirements 7.3
    """

    def test_generate_with_custom_template(self) -> None:
        """Test name generation with custom template."""
        manager = GroupChatManager(None)
        config = {
            "group_name_template": "[紧急] {scenario_name} - {alert_title}",
        }

        result = manager.generate_group_name_from_config(
            config,
            scenario_name="故障定界",
            alert_title="CPU告警",
        )

        assert "[紧急]" in result
        assert "故障定界" in result
        assert "CPU告警" in result

    def test_generate_with_default_template(self) -> None:
        """Test name generation with default template when not configured."""
        manager = GroupChatManager(None)
        config = {}  # No template configured

        result = manager.generate_group_name_from_config(
            config,
            scenario_name="TestScenario",
        )

        assert "[应急]" in result
        assert "TestScenario" in result or "Test..." in result

    def test_generate_truncates_long_names(self) -> None:
        """Test that generated names are truncated to 50 characters."""
        manager = GroupChatManager(None)
        config = {
            "group_name_template": "{scenario_name} - {trigger_reason} - {alert_title}",
        }

        result = manager.generate_group_name_from_config(
            config,
            scenario_name="Very Long Scenario Name That Exceeds Limits",
            trigger_reason="This is a very long trigger reason",
            alert_title="This is a very long alert title",
        )

        assert len(result) <= 50

    def test_generate_with_timestamp(self) -> None:
        """Test that timestamp variable is substituted."""
        manager = GroupChatManager(None)
        config = {
            "group_name_template": "[应急] {timestamp}",
        }

        result = manager.generate_group_name_from_config(
            config,
            scenario_name="Test",
        )

        # Should contain date-like pattern
        assert "-" in result  # Date format includes dashes

    def test_generate_with_custom_variables(self) -> None:
        """Test name generation with custom template variables."""
        manager = GroupChatManager(None)
        config = {
            "group_name_template": "[{severity}] {scenario_name}",
        }

        result = manager.generate_group_name_from_config(
            config,
            scenario_name="故障处理",
            severity="P0",
        )

        assert "[P0]" in result
        assert "故障处理" in result


class TestAddMembersToGroup:
    """Tests for add_members_to_group method.

    Validates: Requirements 7.2
    """

    @pytest.mark.asyncio
    async def test_add_members_no_group_chat(self) -> None:
        """Test adding members fails when session has no group chat."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.group_chat_id = None

        result = await manager.add_members_to_group(
            session=mock_session,
            new_members=["user1", "user2"],
        )

        assert result.success is False
        assert "no associated group chat" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_add_members_empty_list(self) -> None:
        """Test adding empty member list fails."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.group_chat_id = "test_chat_id"

        result = await manager.add_members_to_group(
            session=mock_session,
            new_members=[],
        )

        assert result.success is False
        assert "no members" in result.error_message.lower()


class TestRemoveMembersFromGroup:
    """Tests for remove_members_from_group method.

    Validates: Requirements 7.2
    """

    @pytest.mark.asyncio
    async def test_remove_members_no_group_chat(self) -> None:
        """Test removing members fails when session has no group chat."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.group_chat_id = None

        result = await manager.remove_members_from_group(
            session=mock_session,
            members_to_remove=["user1"],
        )

        assert result.success is False
        assert "no associated group chat" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_remove_members_empty_list(self) -> None:
        """Test removing empty member list fails."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.group_chat_id = "test_chat_id"

        result = await manager.remove_members_from_group(
            session=mock_session,
            members_to_remove=[],
        )

        assert result.success is False
        assert "no members" in result.error_message.lower()


class TestUpdateGroupName:
    """Tests for update_group_name method."""

    @pytest.mark.asyncio
    async def test_update_name_no_group_chat(self) -> None:
        """Test updating name fails when session has no group chat."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.group_chat_id = None

        result = await manager.update_group_name(
            session=mock_session,
            new_name="New Name",
        )

        assert result.success is False
        assert "no associated group chat" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_update_name_empty_name(self) -> None:
        """Test updating with empty name fails."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.group_chat_id = "test_chat_id"

        result = await manager.update_group_name(
            session=mock_session,
            new_name="",
        )

        assert result.success is False
        assert "cannot be empty" in result.error_message.lower()


class TestGetGroupChatInfo:
    """Tests for get_group_chat_info method.

    Validates: Requirements 7.8
    """

    @pytest.mark.asyncio
    async def test_get_group_chat_info_success(self) -> None:
        """Test successful retrieval of group chat information."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.get_app_chat",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "errcode": 0,
                "errmsg": "ok",
                "chat_info": {
                    "chatid": "test_chat_id",
                    "name": "Test Group",
                    "owner": "admin",
                    "userlist": ["admin", "user1", "user2"],
                    "create_time": 1704067200,
                },
            }

            result = await manager.get_group_chat_info(
                wecom_config=wecom_config,
                chatid="test_chat_id",
            )

            assert result is not None
            assert result.chatid == "test_chat_id"
            assert result.name == "Test Group"
            assert result.owner == "admin"
            assert result.userlist == ["admin", "user1", "user2"]
            assert result.create_time == 1704067200

    @pytest.mark.asyncio
    async def test_get_group_chat_info_not_found(self) -> None:
        """Test retrieval when group chat is not found."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.get_app_chat",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "errcode": 86001,
                "errmsg": "chat not found",
            }

            result = await manager.get_group_chat_info(
                wecom_config=wecom_config,
                chatid="nonexistent_chat",
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_get_group_chat_info_api_error(self) -> None:
        """Test retrieval when API returns an error."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.get_app_chat",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.return_value = {
                "errcode": 40001,
                "errmsg": "invalid credential",
            }

            result = await manager.get_group_chat_info(
                wecom_config=wecom_config,
                chatid="test_chat_id",
            )

            assert result is None

    @pytest.mark.asyncio
    async def test_get_group_chat_info_exception(self) -> None:
        """Test retrieval when an exception occurs."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch(
            "src.services.group_chat_manager.get_app_chat",
            new_callable=AsyncMock,
        ) as mock_get:
            mock_get.side_effect = Exception("Network error")

            result = await manager.get_group_chat_info(
                wecom_config=wecom_config,
                chatid="test_chat_id",
            )

            assert result is None


class TestGetGroupChatInfoForSession:
    """Tests for get_group_chat_info_for_session method.

    Validates: Requirements 7.8
    """

    @pytest.mark.asyncio
    async def test_get_info_for_session_no_group_chat(self) -> None:
        """Test retrieval when session has no associated group chat."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.group_chat_id = None

        result = await manager.get_group_chat_info_for_session(mock_session)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_info_for_session_no_wecom_config(self) -> None:
        """Test retrieval when no WeChat Work config is available."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.group_chat_id = "test_chat_id"

        with patch.object(
            manager,
            "get_wecom_config_for_scenario",
            new_callable=AsyncMock,
        ) as mock_get_config:
            mock_get_config.return_value = None

            result = await manager.get_group_chat_info_for_session(mock_session)

            assert result is None

    @pytest.mark.asyncio
    async def test_get_info_for_session_success(self) -> None:
        """Test successful retrieval of group chat info for session."""
        mock_db = AsyncMock()
        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.group_chat_id = "test_chat_id"

        wecom_config = WeComConfig(
            corp_id="test_corp",
            corp_secret="test_secret",
            agent_id=1000001,
        )

        with patch.object(
            manager,
            "get_wecom_config_for_scenario",
            new_callable=AsyncMock,
        ) as mock_get_config, patch(
            "src.services.group_chat_manager.get_app_chat",
            new_callable=AsyncMock,
        ) as mock_get_chat:
            mock_get_config.return_value = wecom_config
            mock_get_chat.return_value = {
                "errcode": 0,
                "errmsg": "ok",
                "chat_info": {
                    "chatid": "test_chat_id",
                    "name": "Test Group",
                    "owner": "admin",
                    "userlist": ["admin", "user1"],
                    "create_time": 1704067200,
                },
            }

            result = await manager.get_group_chat_info_for_session(mock_session)

            assert result is not None
            assert result.chatid == "test_chat_id"
            assert result.name == "Test Group"
            mock_get_config.assert_called_once_with(mock_session.scenario_id)


class TestHandleCreationFailure:
    """Tests for handle_creation_failure method.

    Validates: Requirements 7.9
    """

    @pytest.mark.asyncio
    async def test_handle_failure_records_error(self) -> None:
        """Test that failure is recorded in session progress."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.progress_summary = {}
        mock_session.config_snapshot = {"scenario_name": "Test Scenario"}

        # Mock email service to avoid actual email sending
        with patch(
            "src.services.email_notification.EmailNotificationService"
        ) as mock_email_service_class:
            mock_email_service = MagicMock()
            mock_email_service.send_custom_email = AsyncMock(
                return_value=MagicMock(success=True, recipients=[])
            )
            mock_email_service_class.return_value = mock_email_service

            await manager.handle_creation_failure(
                session=mock_session,
                error_code=40001,
                error_message="invalid credential",
                notify_users=["user1", "user2"],
            )

        # Verify error was recorded in progress_summary
        assert "errors" in mock_session.progress_summary
        errors = mock_session.progress_summary["errors"]
        assert len(errors) == 1
        assert errors[0]["type"] == "group_chat_creation_failed"
        assert errors[0]["error_code"] == 40001
        assert errors[0]["error_message"] == "invalid credential"
        assert errors[0]["notified_users"] == ["user1", "user2"]

    @pytest.mark.asyncio
    async def test_handle_failure_creates_system_message(self) -> None:
        """Test that a system message is created for the failure."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.progress_summary = {}
        mock_session.config_snapshot = {"scenario_name": "Test Scenario"}

        # Mock email service
        with patch(
            "src.services.email_notification.EmailNotificationService"
        ) as mock_email_service_class:
            mock_email_service = MagicMock()
            mock_email_service.send_custom_email = AsyncMock(
                return_value=MagicMock(success=True, recipients=[])
            )
            mock_email_service_class.return_value = mock_email_service

            await manager.handle_creation_failure(
                session=mock_session,
                error_code=40001,
                error_message="invalid credential",
            )

        # Verify system message was added
        mock_db.add.assert_called_once()
        added_message = mock_db.add.call_args[0][0]
        assert added_message.source_channel == "system"
        assert added_message.message_type == "event"
        assert "群聊创建失败" in added_message.content
        assert added_message.msg_metadata["event_type"] == "group_chat_creation_failed"

    @pytest.mark.asyncio
    async def test_handle_failure_sends_email_notification(self) -> None:
        """Test that email notification is sent on failure."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.progress_summary = {}
        mock_session.config_snapshot = {"scenario_name": "Test Scenario"}

        # Mock email service
        with patch(
            "src.services.email_notification.EmailNotificationService"
        ) as mock_email_service_class:
            mock_email_service = MagicMock()
            mock_email_service.send_custom_email = AsyncMock(
                return_value=MagicMock(success=True, recipients=["admin@example.com"])
            )
            mock_email_service_class.return_value = mock_email_service

            await manager.handle_creation_failure(
                session=mock_session,
                error_code=40001,
                error_message="invalid credential",
            )

            # Verify email service was called
            mock_email_service.send_custom_email.assert_called_once()
            call_kwargs = mock_email_service.send_custom_email.call_args.kwargs
            assert call_kwargs["email_type"] == "group_chat_creation_failed"
            assert "error_code" in call_kwargs["extra_variables"]
            assert "error_message" in call_kwargs["extra_variables"]

    @pytest.mark.asyncio
    async def test_handle_failure_skips_email_when_disabled(self) -> None:
        """Test that email notification is skipped when disabled."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.progress_summary = {}
        mock_session.config_snapshot = {"scenario_name": "Test Scenario"}

        # Mock email service
        with patch(
            "src.services.email_notification.EmailNotificationService"
        ) as mock_email_service_class:
            mock_email_service = MagicMock()
            mock_email_service.send_custom_email = AsyncMock()
            mock_email_service_class.return_value = mock_email_service

            await manager.handle_creation_failure(
                session=mock_session,
                error_code=40001,
                error_message="invalid credential",
                send_email_notification=False,  # Disable email
            )

            # Verify email service was NOT called
            mock_email_service_class.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_failure_continues_on_email_error(self) -> None:
        """Test that failure handling continues even if email fails."""
        mock_db = AsyncMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()

        manager = GroupChatManager(mock_db)

        mock_session = MagicMock()
        mock_session.id = uuid.uuid4()
        mock_session.scenario_id = uuid.uuid4()
        mock_session.progress_summary = {}
        mock_session.config_snapshot = {"scenario_name": "Test Scenario"}

        # Mock email service to raise exception
        with patch(
            "src.services.email_notification.EmailNotificationService"
        ) as mock_email_service_class:
            mock_email_service_class.side_effect = Exception("Email service error")

            # Should not raise exception
            await manager.handle_creation_failure(
                session=mock_session,
                error_code=40001,
                error_message="invalid credential",
            )

        # Verify error was still recorded despite email failure
        assert "errors" in mock_session.progress_summary
        assert len(mock_session.progress_summary["errors"]) == 1
