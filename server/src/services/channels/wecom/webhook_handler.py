"""
企业微信 HTTP Webhook 处理器 — Bot 模式 + Agent 模式回调。

处理流程：
- GET: URL 验证 (echostr 解密)
- POST: 消息回调 — 签名验证 → AES 解密 → 消息解析 → 路由分发
"""

from __future__ import annotations

import base64
import hashlib
import json as _json
import logging
import secrets
import struct
from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from .const import WEBHOOK_PATH_AGENT, WEBHOOK_PATH_BOT
from .message_parser import ParsedMessage, parse_message

logger = logging.getLogger(__name__)

WebhookMessageHandler = Callable[[ParsedMessage, dict], Awaitable[dict | None]]


# ── AES 加解密 ──────────────────────────────────────────────────────────────


def _pkcs7_pad(data: bytes, block_size: int = 32) -> bytes:
    pad_len = block_size - (len(data) % block_size)
    return data + bytes([pad_len] * pad_len)


def _pkcs7_unpad(data: bytes) -> bytes:
    pad_len = data[-1]
    if pad_len < 1 or pad_len > 32:
        raise ValueError("Invalid PKCS7 padding")
    return data[:-pad_len]


def _aes_decrypt(encrypted: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(encrypted) + decryptor.finalize()
    return _pkcs7_unpad(decrypted)


def _aes_encrypt(plaintext: bytes, key: bytes) -> bytes:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    iv = key[:16]
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    padded = _pkcs7_pad(plaintext)
    return encryptor.update(padded) + encryptor.finalize()


# ── 签名 ────────────────────────────────────────────────────────────────────


def _verify_signature(token: str, timestamp: str, nonce: str, encrypt: str, msg_signature: str) -> bool:
    parts = sorted([token, timestamp, nonce, encrypt])
    raw = "".join(parts).encode()
    return hashlib.sha1(raw).hexdigest() == msg_signature


def _compute_signature(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    parts = sorted([token, timestamp, nonce, encrypt])
    return hashlib.sha1("".join(parts).encode()).hexdigest()


# ── 消息加解密 ──────────────────────────────────────────────────────────────


def _decrypt_callback_message(encrypt: str, encoding_aes_key: str, receive_id: str) -> str:
    """解密企微加密回调消息。格式: random(16) + msg_len(4) + msg + receive_id"""
    aes_key = base64.b64decode(encoding_aes_key + "=")
    ciphertext = base64.b64decode(encrypt)
    decrypted = _aes_decrypt(ciphertext, aes_key)
    decrypted = decrypted[16:]  # skip random
    msg_len = struct.unpack("!I", decrypted[:4])[0]
    decrypted = decrypted[4:]
    msg = decrypted[:msg_len].decode("utf-8")
    rid = decrypted[msg_len:].decode("utf-8")
    if rid != receive_id:
        logger.warning("Webhook receive_id mismatch: expected=%s actual=%s", receive_id, rid)
    return msg


def _encrypt_reply_message(plaintext: str, encoding_aes_key: str, receive_id: str) -> str:
    """加密回复消息。"""
    aes_key = base64.b64decode(encoding_aes_key + "=")
    random_bytes = secrets.token_bytes(16)
    msg_bytes = plaintext.encode("utf-8")
    msg_len = struct.pack("!I", len(msg_bytes))
    raw = random_bytes + msg_len + msg_bytes + receive_id.encode("utf-8")
    encrypted = _aes_encrypt(raw, aes_key)
    return base64.b64encode(encrypted).decode()


# ── 路由构造 ────────────────────────────────────────────────────────────────


def create_webhook_router(
    token: str,
    encoding_aes_key: str,
    receive_id: str = "",
    on_message: WebhookMessageHandler | None = None,
) -> APIRouter:
    """创建 WeCom webhook FastAPI 路由。

    on_message: 收到解析后的消息时调用，返回要回复的 dict (msgtype + content)。
    返回 None 表示空回复。
    """
    router = APIRouter(tags=["wecom-webhook"])
    _handler = on_message

    async def _verify_and_decrypt(request: Request):
        """验证签名并解密消息体。返回 (plaintext_dict, timestamp, nonce) 或错误 Response。"""
        query = request.query_params
        msg_signature = query.get("msg_signature", "")
        timestamp = query.get("timestamp", "")
        nonce = query.get("nonce", "")
        echostr = query.get("echostr", "")

        if not msg_signature or not timestamp or not nonce:
            return PlainTextResponse("missing required query parameters", status_code=400)

        if request.method == "GET":
            if not echostr:
                return PlainTextResponse("missing echostr", status_code=400)
            if not _verify_signature(token, timestamp, nonce, echostr, msg_signature):
                return PlainTextResponse("signature verification failed", status_code=403)
            try:
                plaintext = _decrypt_callback_message(echostr, encoding_aes_key, receive_id)
                return PlainTextResponse(plaintext)
            except Exception as exc:
                logger.error("Webhook GET decrypt failed: %s", exc)
                return PlainTextResponse("decryption failed", status_code=403)

        # POST
        try:
            body = await request.body()
        except Exception:
            return PlainTextResponse("invalid body", status_code=400)

        content_type = request.headers.get("content-type", "")

        try:
            if "xml" in content_type:
                import xml.etree.ElementTree as ET
                root = ET.fromstring(body)
                encrypt_el = root.find("Encrypt")
                encrypt = encrypt_el.text if encrypt_el is not None else ""
            else:
                payload = _json.loads(body)
                encrypt = payload.get("encrypt", payload.get("Encrypt", ""))
        except Exception:
            return PlainTextResponse("invalid payload", status_code=400)

        if not encrypt:
            return PlainTextResponse("missing encrypt field", status_code=400)

        if not _verify_signature(token, timestamp, nonce, encrypt, msg_signature):
            return PlainTextResponse("signature verification failed", status_code=403)

        try:
            plaintext = _decrypt_callback_message(encrypt, encoding_aes_key, receive_id)
            message = _json.loads(plaintext)
        except Exception as exc:
            logger.error("Webhook POST decrypt failed: %s", exc)
            return PlainTextResponse("decrypt failed", status_code=400)

        return message, timestamp, nonce

    def _build_encrypted_reply(reply: dict | None, timestamp: str, nonce: str) -> Response:
        reply_json = _json.dumps(reply or {}, ensure_ascii=False)
        encrypted = _encrypt_reply_message(reply_json, encoding_aes_key, receive_id)
        sig = _compute_signature(token, timestamp, nonce, encrypted)
        return Response(
            _json.dumps({"encrypt": encrypted, "msgsignature": sig, "timestamp": timestamp, "nonce": nonce}),
            media_type="text/plain; charset=utf-8",
        )

    async def _handle(request: Request):
        result = await _verify_and_decrypt(request)
        # GET 请求返回了纯文本，直接返回
        if isinstance(result, Response):
            return result

        message, timestamp, nonce = result
        msgtype = message.get("msgtype", "")
        logger.info("Webhook message: msgtype=%s msgid=%s", msgtype, message.get("msgid"))

        parsed = parse_message(message)
        reply_data: dict | None = None
        if _handler and parsed.text:
            try:
                reply_data = await _handler(parsed, message)
            except Exception as exc:
                logger.exception("Webhook handler error: %s", exc)

        # Stream refresh — service-side polling for streaming reply
        if msgtype == "stream":
            return _build_encrypted_reply(reply_data, timestamp, nonce)

        return _build_encrypted_reply(reply_data, timestamp, nonce)

    @router.get(WEBHOOK_PATH_BOT)
    async def bot_webhook_get(request: Request):
        return await _handle(request)

    @router.post(WEBHOOK_PATH_BOT)
    async def bot_webhook_post(request: Request):
        return await _handle(request)

    @router.get(WEBHOOK_PATH_AGENT)
    async def agent_webhook_get(request: Request):
        return await _handle(request)

    @router.post(WEBHOOK_PATH_AGENT)
    async def agent_webhook_post(request: Request):
        return await _handle(request)

    return router
