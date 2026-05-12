---
name: send-channel-message
description: Send notifications through configured message channels (WeCom, DingTalk, Email, Webhook, etc.). Use when the user wants to send alerts, notifications, reports, or messages to team members or external systems.
version: 1.0.0
metadata:
  tags:
  - notification
  - message
  - channel
  - email
  - wecom
  - dingtalk
  - webhook
  - alert
license: MIT
---

# Send Channel Message

Send notifications and messages through configured notification channels in AIOpsOS.

## When to Activate

Activate this skill when the user asks to:
- "发送邮件给xxx" / "Send an email to xxx"
- "通知团队xxx" / "Notify the team about xxx"
- "发送告警消息" / "Send an alert message"
- "通过企业微信发送xxx" / "Send xxx via WeCom"
- "通过钉钉通知xxx" / "Notify via DingTalk"
- "发送测试消息" / "Send a test message"
- "把这个报告发送给xxx" / "Send this report to xxx"
- Any request involving sending notifications, alerts, or messages through channels

## Available Tool

### `send_channel_message`

Send a notification through a configured message channel.

**Parameters:**
- `channel_name` (required): Name of the configured channel to use (e.g., "企业微信告警", "邮件通知", "钉钉群")
- `title` (required): Message title/subject
- `message` (required): Message content body (supports markdown for some channels)
- `severity` (optional): Message severity level - one of: `info`, `warning`, `critical`, `error`. Default: `info`
- `recipients` (optional): List of recipient addresses (email addresses, user IDs, etc.). If not provided, uses channel's default recipients.

**Returns:**
- Success: Confirmation message with channel name and recipient info
- Failure: Error message explaining what went wrong

## Workflow

### Send a simple notification
1. Identify the channel name from user's request or ask which channel to use
2. Call `send_channel_message` with:
   - `channel_name`: The configured channel name
   - `title`: A clear, concise subject
   - `message`: The notification content
   - `severity`: Appropriate level based on content (info for general, warning for alerts, critical for urgent)

### Send to specific recipients
1. Get the channel name and recipient list from user
2. Call `send_channel_message` with the `recipients` parameter
3. Confirm delivery status

### Send a report or summary
1. Prepare the content (can include markdown formatting)
2. Use `severity: info` for regular reports
3. Include relevant data in the message body

## Examples

### Example 1: Send email notification
```
User: 发送一封邮件给 admin@company.com，告诉他服务器磁盘空间不足

Action: send_channel_message(
  channel_name="邮件通知",
  title="服务器磁盘空间告警",
  message="服务器磁盘空间使用率已超过 90%，请及时清理或扩容。\n\n服务器: prod-server-01\n当前使用率: 92%",
  severity="warning",
  recipients=["admin@company.com"]
)
```

### Example 2: Send WeCom alert
```
User: 通过企业微信通知运维团队，数据库连接数过高

Action: send_channel_message(
  channel_name="企业微信告警",
  title="数据库连接数告警",
  message="MySQL 数据库连接数已达到阈值\n\n- 当前连接数: 450\n- 最大连接数: 500\n- 建议: 检查是否有连接泄漏",
  severity="critical"
)
```

### Example 3: Send daily report
```
User: 把今天的系统运行报告发送到钉钉群

Action: send_channel_message(
  channel_name="钉钉运维群",
  title="系统日报 - 2024-01-15",
  message="## 今日系统运行概况\n\n- CPU 平均使用率: 45%\n- 内存使用率: 68%\n- 告警数量: 3 (已处理)\n- 服务可用性: 99.9%",
  severity="info"
)
```

## Answer Guidelines

- Always confirm the channel name before sending
- If the user doesn't specify a channel, list available channels and ask which one to use
- For urgent messages, use `severity: critical` or `severity: warning`
- Include relevant context in the message body
- After sending, confirm success or report any errors
- Use Chinese unless the user communicates in English

## Error Handling

Common errors and solutions:
- "Channel not found": Check the channel name spelling, or list available channels
- "No recipients configured": Either provide recipients in the call or configure default recipients in the channel settings
- "Authentication failed": Channel credentials may be invalid, suggest checking channel configuration
- "Rate limited": Wait and retry, or reduce message frequency

## Notes

- Channels must be configured in System Settings → Message Channels before use
- Each channel type (email, WeCom, DingTalk, webhook) has different configuration requirements
- Some channels support markdown formatting in messages
- Recipients format varies by channel type (email addresses, user IDs, webhook URLs, etc.)
