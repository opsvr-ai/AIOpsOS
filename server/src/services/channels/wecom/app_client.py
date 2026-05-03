"""企业微信应用 API 客户端 — access_token 管理、消息发送、群聊操作。

提供:
- get_access_token() — 带缓存的 token 获取 (缓存 7000s, token 有效期 7200s)
- send_message() — 发送应用消息 (支持 touser/toparty/totag)
- create_app_chat() — 创建应用群聊
- send_app_chat_message() — 向群聊发送消息
- get_app_chat() — 获取群聊信息
- update_app_chat() — 更新群聊信息
"""

from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

from .const import CLOUD_API_BASE

logger = logging.getLogger(__name__)

# ── Token 缓存 ────────────────────────────────────────────────────────────────

_token_cache: dict[str, tuple[str, float]] = {}
_TOKEN_REFRESH_MARGIN = 200  # 提前 200s 刷新


def _invalidate_token(cache_key: str):
    _token_cache.pop(cache_key, None)


async def get_access_token(
    corp_id: str,
    corp_secret: str,
    api_base: str = CLOUD_API_BASE,
) -> str:
    """获取企业微信应用 access_token，自动缓存。

    Token 有效期 7200s，在 7000s 时自动刷新。
    """
    cache_key = f"{api_base}:{corp_id}:{corp_secret}"
    cached = _token_cache.get(cache_key)
    if cached:
        token, expires_at = cached
        if time.time() < expires_at:
            return token

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{api_base}/cgi-bin/gettoken",
            params={"corpid": corp_id, "corpsecret": corp_secret},
            timeout=aiohttp.ClientTimeout(10),
        ) as resp:
            data = await resp.json()
            errcode = data.get("errcode", -1)
            if errcode != 0:
                raise RuntimeError(f"gettoken failed: errcode={errcode} errmsg={data.get('errmsg')}")
            token = data["access_token"]
            expires_in = data.get("expires_in", 7200)
            ttl = max(expires_in - _TOKEN_REFRESH_MARGIN, 60)
            _token_cache[cache_key] = (token, time.time() + ttl)
            logger.debug("WeCom app token cached (ttl=%ds)", ttl)
            return token


# ── 消息发送 ──────────────────────────────────────────────────────────────────


async def send_message(
    corp_id: str,
    corp_secret: str,
    agent_id: int,
    *,
    api_base: str = CLOUD_API_BASE,
    msgtype: str = "text",
    content: str = "",
    touser: str = "",
    toparty: str = "",
    totag: str = "",
    safe: int = 0,
) -> dict[str, Any]:
    """发送应用消息 — 支持 text / markdown。

    touser, toparty, totag 至少提供一个，多个用户用 '|' 分隔。
    返回形如 {"errcode":0,"errmsg":"ok","msgid":"..."} 的响应。
    """
    if not touser and not toparty and not totag:
        return {"errcode": -1, "errmsg": "touser/toparty/totag required"}

    token = await get_access_token(corp_id, corp_secret, api_base)
    body: dict[str, Any] = {
        "msgtype": msgtype,
        "agentid": int(agent_id),
        "safe": int(safe),
    }
    if touser:
        body["touser"] = touser
    if toparty:
        body["toparty"] = toparty
    if totag:
        body["totag"] = totag
    if msgtype == "text":
        body["text"] = {"content": content}
    elif msgtype == "markdown":
        body["markdown"] = {"content": content}
    else:
        return {"errcode": -1, "errmsg": f"unsupported msgtype: {msgtype}"}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{api_base}/cgi-bin/message/send?access_token={token}",
            json=body,
            timeout=aiohttp.ClientTimeout(10),
        ) as resp:
            result = await resp.json()
            if result.get("errcode") != 0:
                logger.error("WeCom app send_message failed: %s", result)
            return result


# ── 应用群聊 ──────────────────────────────────────────────────────────────────


async def create_app_chat(
    corp_id: str,
    corp_secret: str,
    agent_id: int,
    *,
    name: str,
    owner: str,
    userlist: list[str],
    chatid: str = "",
    api_base: str = CLOUD_API_BASE,
) -> dict[str, Any]:
    """创建应用群聊。

    - name: 群聊名称 (最多 50 个 utf-8 字符)
    - owner: 群主 userid
    - userlist: 成员 userid 列表 (至少 2 人，含群主)
    - chatid: 可选，指定群聊 ID (最多 32 字符)
    返回 {"errcode":0,"errmsg":"ok","chatid":"..."}
    """
    if len(userlist) < 2:
        return {"errcode": -1, "errmsg": "userlist must have at least 2 members"}

    token = await get_access_token(corp_id, corp_secret, api_base)
    body: dict[str, Any] = {
        "name": name,
        "owner": owner,
        "userlist": userlist,
        "agentid": int(agent_id),
    }
    if chatid:
        body["chatid"] = chatid

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{api_base}/cgi-bin/appchat/create?access_token={token}",
            json=body,
            timeout=aiohttp.ClientTimeout(10),
        ) as resp:
            result = await resp.json()
            if result.get("errcode") != 0:
                logger.error("WeCom app create_app_chat failed: %s", result)
            return result


async def send_app_chat_message(
    corp_id: str,
    corp_secret: str,
    *,
    chatid: str,
    msgtype: str = "text",
    content: str = "",
    safe: int = 0,
    api_base: str = CLOUD_API_BASE,
) -> dict[str, Any]:
    """向应用群聊发送消息。

    返回 {"errcode":0,"errmsg":"ok"}。
    """
    token = await get_access_token(corp_id, corp_secret, api_base)
    body: dict[str, Any] = {
        "chatid": chatid,
        "msgtype": msgtype,
        "safe": int(safe),
    }
    if msgtype == "text":
        body["text"] = {"content": content}
    elif msgtype == "markdown":
        body["markdown"] = {"content": content}
    else:
        return {"errcode": -1, "errmsg": f"unsupported msgtype: {msgtype}"}

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{api_base}/cgi-bin/appchat/send?access_token={token}",
            json=body,
            timeout=aiohttp.ClientTimeout(10),
        ) as resp:
            result = await resp.json()
            if result.get("errcode") != 0:
                logger.error("WeCom app send_chat_message failed: %s", result)
            return result


async def get_app_chat(
    corp_id: str,
    corp_secret: str,
    chatid: str,
    api_base: str = CLOUD_API_BASE,
) -> dict[str, Any]:
    """获取应用群聊信息。返回 {"errcode":0,"errmsg":"ok","chat_info":{...}}"""
    token = await get_access_token(corp_id, corp_secret, api_base)

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{api_base}/cgi-bin/appchat/get",
            params={"access_token": token, "chatid": chatid},
            timeout=aiohttp.ClientTimeout(10),
        ) as resp:
            result = await resp.json()
            if result.get("errcode") != 0:
                logger.error("WeCom app get_app_chat failed: %s", result)
            return result


async def update_app_chat(
    corp_id: str,
    corp_secret: str,
    *,
    chatid: str,
    name: str = "",
    owner: str = "",
    add_user_list: list[str] | None = None,
    del_user_list: list[str] | None = None,
    api_base: str = CLOUD_API_BASE,
) -> dict[str, Any]:
    """更新应用群聊信息。返回 {"errcode":0,"errmsg":"ok"}"""
    token = await get_access_token(corp_id, corp_secret, api_base)
    body: dict[str, Any] = {"chatid": chatid}
    if name:
        body["name"] = name
    if owner:
        body["owner"] = owner
    if add_user_list:
        body["add_user_list"] = add_user_list
    if del_user_list:
        body["del_user_list"] = del_user_list

    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{api_base}/cgi-bin/appchat/update?access_token={token}",
            json=body,
            timeout=aiohttp.ClientTimeout(10),
        ) as resp:
            result = await resp.json()
            if result.get("errcode") != 0:
                logger.error("WeCom app update_app_chat failed: %s", result)
            return result
