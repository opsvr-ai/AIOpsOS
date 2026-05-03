"""企业微信渠道常量 — 协议命令、超时、限制、消息模板。"""

CHANNEL_ID = "wecom"

# ── WebSocket 命令 ──────────────────────────────────────────────────────────

WECOM_CMD_SUBSCRIBE = "aibot_subscribe"
WECOM_CMD_CALLBACK = "aibot_msg_callback"
WECOM_CMD_EVENT_CALLBACK = "aibot_event_callback"
WECOM_CMD_RESPONSE = "aibot_response"
WECOM_CMD_SEND_MSG = "aibot_send_msg"
WECOM_CMD_PING = "ping"
WECOM_CMD_GET_MCP_CONFIG = "aibot_get_mcp_config"
WECOM_CMD_SEND_BIZ_MSG = "aibot_send_biz_msg"
WECOM_CMD_ENTER_EVENT_REPLY = "ww_ai_robot_enter_event"

# ── 超时 (秒) ──────────────────────────────────────────────────────────────

IMAGE_DOWNLOAD_TIMEOUT = 30
FILE_DOWNLOAD_TIMEOUT = 60
REPLY_SEND_TIMEOUT = 15
MESSAGE_PROCESS_TIMEOUT = 6 * 60
WS_HEARTBEAT_INTERVAL = 30
WS_RECONNECT_DELAY_MIN = 1
WS_RECONNECT_DELAY_MAX = 60
WS_MAX_AUTH_FAILURES = 5

# ── 消息状态 TTL ───────────────────────────────────────────────────────────

MESSAGE_STATE_TTL = 10 * 60
MESSAGE_STATE_CLEANUP_INTERVAL = 60
MESSAGE_STATE_MAX_SIZE = 500

# ── 错误码 ─────────────────────────────────────────────────────────────────

STREAM_EXPIRED_ERRCODE = 846608
ERRCODE_INVALID_REQ_ID = 846605
ERRCODE_INVALID_CHATID = 86201

# ── 消息模板 ───────────────────────────────────────────────────────────────

THINKING_MESSAGE = "<think></think>"
MEDIA_IMAGE_PLACEHOLDER = "<media:image>"
MEDIA_DOCUMENT_PLACEHOLDER = "<media:document>"

# ── 媒体大小上限 (字节) ───────────────────────────────────────────────────

IMAGE_MAX_BYTES = 10 * 1024 * 1024
VIDEO_MAX_BYTES = 10 * 1024 * 1024
VOICE_MAX_BYTES = 2 * 1024 * 1024
FILE_MAX_BYTES = 20 * 1024 * 1024

# ── 文本分块上限 ──────────────────────────────────────────────────────────

TEXT_CHUNK_LIMIT = 4000

# ── Webhook 路径 ───────────────────────────────────────────────────────────

WEBHOOK_PATH_BOT = "/wecom/bot"
WEBHOOK_PATH_AGENT = "/wecom/agent"

# ── 企业微信 API 端点 ──────────────────────────────────────────────────────

CLOUD_API_BASE = "https://qyapi.weixin.qq.com"
CLOUD_WS_BASE = "wss://openws.work.weixin.qq.com"
API_GET_TOKEN = "/cgi-bin/gettoken"
API_SEND_MESSAGE = "/cgi-bin/message/send"
API_UPLOAD_MEDIA = "/cgi-bin/media/upload"
API_DOWNLOAD_MEDIA = "/cgi-bin/media/get"

# ── 事件类型 ───────────────────────────────────────────────────────────────

EVENT_CHECK_UPDATE = "enter_check_update"
EVENT_TEMPLATE_CARD = "template_card_event"
EVENT_AUTH_CHANGE = "auth_change_event"

# ── 权限类型映射 ───────────────────────────────────────────────────────────

AUTH_TYPE_MAP = {
    1: "新建和编辑文档",
    2: "获取成员文档内容",
}

# ── 模板卡片合法 card_type ─────────────────────────────────────────────────

VALID_CARD_TYPES = [
    "text_notice",
    "news_notice",
    "button_interaction",
    "vote_interaction",
    "multiple_interaction",
]
