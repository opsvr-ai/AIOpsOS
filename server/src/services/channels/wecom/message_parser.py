"""企业微信消息解析 — 从 WsFrame body 提取文本、图片、文件、引用、事件。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .const import (
    AUTH_TYPE_MAP,
    EVENT_AUTH_CHANGE,
    EVENT_TEMPLATE_CARD,
)


@dataclass
class ParsedMessage:
    """解析后的企业微信消息。"""

    text: str = ""
    image_urls: list[str] = field(default_factory=list)
    image_aes_keys: dict[str, str] = field(default_factory=dict)
    file_urls: list[str] = field(default_factory=list)
    file_aes_keys: dict[str, str] = field(default_factory=dict)
    quote_content: str | None = None
    # 入站元数据
    msgid: str = ""
    chatid: str = ""
    chattype: str = "single"
    sender_userid: str = ""
    response_url: str = ""
    req_id: str = ""
    msgtype: str = ""


def _build_template_card_event_text(body: dict) -> str | None:
    """将模板卡片事件回调转为可路由给大模型的文本。"""
    event = body.get("event", {})
    if body.get("msgtype") != "event" or event.get("eventtype") != EVENT_TEMPLATE_CARD:
        return None

    tce = event.get("template_card_event", {})
    selected_items = tce.get("selected_items", {}).get("selected_item", [])
    lines = []
    for item in selected_items:
        qk = item.get("question_key", "unknown_question").strip()
        option_ids = item.get("option_ids", {}).get("option_id", []) or []
        lines.append(f"- {qk}: {', '.join(option_ids) if option_ids else '(未选择)'}")

    sender = body.get("from", {}).get("userid", "")
    chatid = body.get("chatid") or sender

    parts = [
        "[企业微信模板卡片回调]",
        "event_type(事件类型): template_card_event",
        f"card_type(卡片类型): {tce.get('card_type', '')}",
        f"event_key(事件 key): {tce.get('event_key', '')}",
        f"task_id(任务 id): {tce.get('task_id', '')}",
        f"chat_id(会话 id): {chatid}",
        f"from.userid(发送人 id): {sender}",
        "selected_items(选择项):" if lines else "selected_items(选择项): []",
        *lines,
    ]
    return "\n".join(parts)


def _build_auth_change_event_text(body: dict) -> str | None:
    """将权限变更事件回调转为可路由给大模型的文本。"""
    event = body.get("event", {})
    ace = event.get("auth_change_event")
    if body.get("msgtype") != "event" or event.get("eventtype") != EVENT_AUTH_CHANGE or not ace:
        return None

    auth_list = ace.get("auth_list", [])
    descriptions = [AUTH_TYPE_MAP.get(c, f"未知权限({c})") for c in auth_list]
    has_doc = 2 in auth_list

    if has_doc:
        hint = "用户已授予文档内容读取权限，请继续之前的文档操作。"
    elif auth_list:
        hint = (
            "当前授权不包含文档内容读取权限，无法继续文档操作。"
            "请引导用户授予「获取成员文档内容」权限，该权限需要向管理员申请。"
        )
    else:
        hint = "当前无任何文档权限，无法继续文档操作。请引导用户完成文档授权。"

    sender = body.get("from", {}).get("userid", "")
    chatid = body.get("from", {}).get("chat_id") or body.get("chatid") or sender

    return "\n".join([
        "[企业微信文档权限变更回调]",
        "event_type(事件类型): auth_change_event",
        f"auth_list(当前权限列表): [{', '.join(map(str, auth_list))}] ({'、'.join(descriptions) or '无'})",
        f"chat_id(会话 id): {chatid}",
        f"from.userid(发送人 id): {sender}",
        "",
        f"[操作指引] {hint}",
    ])


def parse_message(body: dict[str, Any]) -> ParsedMessage:
    """解析企业微信推送消息 body，返回 ParsedMessage。

    支持: text, image, voice, video, file, mixed, stream, event 及引用消息。
    """
    text_parts: list[str] = []
    image_urls: list[str] = []
    image_aes_keys: dict[str, str] = {}
    file_urls: list[str] = []
    file_aes_keys: dict[str, str] = {}
    quote_content: str | None = None
    msgtype = body.get("msgtype", "")

    # ── 事件回调 ──────────────────────────────────────────────────────────
    if msgtype == "event":
        auth_text = _build_auth_change_event_text(body)
        if auth_text:
            text_parts.append(auth_text)
        else:
            card_text = _build_template_card_event_text(body)
            if card_text:
                text_parts.append(card_text)
        return ParsedMessage(
            text="\n".join(text_parts),
            image_urls=image_urls,
            image_aes_keys=image_aes_keys,
            file_urls=file_urls,
            file_aes_keys=file_aes_keys,
            quote_content=quote_content,
            msgid=body.get("msgid", ""),
            chatid=body.get("chatid") or body.get("from", {}).get("userid", ""),
            chattype=body.get("chattype", "single"),
            sender_userid=body.get("from", {}).get("userid", ""),
            response_url=body.get("response_url", ""),
            req_id="",
            msgtype=msgtype,
        )

    # ── 图文混排 ──────────────────────────────────────────────────────────
    if msgtype == "mixed" and body.get("mixed", {}).get("msg_item"):
        for item in body["mixed"]["msg_item"]:
            if item.get("msgtype") == "text" and item.get("text", {}).get("content"):
                text_parts.append(item["text"]["content"])
            elif item.get("msgtype") == "image" and item.get("image", {}).get("url"):
                url = item["image"]["url"]
                image_urls.append(url)
                if item["image"].get("aeskey"):
                    image_aes_keys[url] = item["image"]["aeskey"]
    else:
        # ── 单条消息 ──────────────────────────────────────────────────────
        if body.get("text", {}).get("content"):
            text_parts.append(body["text"]["content"])

        if msgtype == "voice" and body.get("voice", {}).get("content"):
            text_parts.append(body["voice"]["content"])

        if body.get("image", {}).get("url"):
            url = body["image"]["url"]
            image_urls.append(url)
            if body["image"].get("aeskey"):
                image_aes_keys[url] = body["image"]["aeskey"]

        if msgtype == "file" and body.get("file", {}).get("url"):
            url = body["file"]["url"]
            file_urls.append(url)
            if body["file"].get("aeskey"):
                file_aes_keys[url] = body["file"]["aeskey"]

        if msgtype == "video" and body.get("video", {}).get("url"):
            url = body["video"]["url"]
            file_urls.append(url)
            if body["video"].get("aeskey"):
                file_aes_keys[url] = body["video"]["aeskey"]

    # ── 引用消息 ──────────────────────────────────────────────────────────
    quote = body.get("quote", {})
    if quote:
        qtype = quote.get("msgtype", "")
        if qtype == "text" and quote.get("text", {}).get("content"):
            quote_content = quote["text"]["content"]
        elif qtype == "voice" and quote.get("voice", {}).get("content"):
            quote_content = quote["voice"]["content"]
        elif qtype == "image" and quote.get("image", {}).get("url"):
            image_urls.append(quote["image"]["url"])
            if quote["image"].get("aeskey"):
                image_aes_keys[quote["image"]["url"]] = quote["image"]["aeskey"]
        elif qtype == "file" and quote.get("file", {}).get("url"):
            file_urls.append(quote["file"]["url"])
            if quote["file"].get("aeskey"):
                file_aes_keys[quote["file"]["url"]] = quote["file"]["aeskey"]
        elif qtype == "video" and quote.get("video", {}).get("url"):
            file_urls.append(quote["video"]["url"])
            if quote["video"].get("aeskey"):
                file_aes_keys[quote["video"]["url"]] = quote["video"]["aeskey"]

    text = "\n".join(text_parts).strip()

    return ParsedMessage(
        text=text,
        image_urls=image_urls,
        image_aes_keys=image_aes_keys,
        file_urls=file_urls,
        file_aes_keys=file_aes_keys,
        quote_content=quote_content,
        msgid=body.get("msgid", ""),
        chatid=body.get("chatid") or body.get("from", {}).get("userid", ""),
        chattype=body.get("chattype", "single"),
        sender_userid=body.get("from", {}).get("userid", ""),
        response_url=body.get("response_url", ""),
        req_id="",
        msgtype=msgtype,
    )
