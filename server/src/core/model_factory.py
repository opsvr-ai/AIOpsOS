import logging
import time as _time

from langchain_openai import ChatOpenAI
from sqlalchemy import select

from src.core.redis import cache_delete, cache_get, cache_set
from src.models.base import async_session_factory
from src.models.model_provider import ModelProvider

logger = logging.getLogger(__name__)

_MODEL_CACHE_TTL: int = 300


async def invalidate_model_cache(model_type: str | None = None) -> None:
    """Invalidate cached model provider configs in Redis."""
    if model_type:
        try:
            await cache_delete(f"model:provider:{model_type}")
        except Exception:
            pass
    else:
        try:
            from src.core.redis import cache_delete_pattern
            await cache_delete_pattern("model:provider:*")
        except Exception:
            pass


def _serialize_provider(p: ModelProvider) -> dict:
    return {
        "id": str(p.id),
        "provider_type": p.provider_type,
        "api_key": p.api_key,
        "base_url": p.base_url,
        "model_name": p.model_name,
        "config": p.config or {},
    }


def _build_model_from_provider(provider: ModelProvider):
    cfg = provider.config or {}
    temperature = cfg.get("temperature", 0.2)
    max_tokens = cfg.get("max_tokens", None)
    max_retries = cfg.get("max_retries", 1)
    timeout = cfg.get("timeout", 120)
    connect_timeout = cfg.get("connect_timeout", 10)
    request_timeout = (connect_timeout, timeout) if connect_timeout else timeout

    if provider.provider_type == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            api_key=provider.api_key,
            base_url=provider.base_url or None,
            model_name=provider.model_name,
            temperature=temperature,
            max_tokens=max_tokens or 4096,
            timeout=request_timeout,
            max_retries=max_retries,
        )

    is_deepseek = provider.base_url and "deepseek" in provider.base_url.lower()
    cls = _get_deepseek_class() if is_deepseek else ChatOpenAI
    return cls(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=provider.model_name,
        temperature=temperature,
        timeout=request_timeout,
        max_retries=max_retries,
        **(dict(max_completion_tokens=max_tokens) if max_tokens else {}),
    )


def _get_deepseek_class():
    from src.agent.deep_agent import DeepSeekChatOpenAI
    return DeepSeekChatOpenAI


class NoModelProviderError(Exception):
    """Raised when no active ModelProvider is configured in the platform."""


async def get_default_model(model_type: str = "llm"):
    cache_key = f"model:provider:{model_type}"

    try:
        cached_cfg = await cache_get(cache_key)
    except Exception:
        cached_cfg = None

    if cached_cfg:
        return _build_model_from_config(cached_cfg)

    async with async_session_factory() as db:
        result = await db.execute(
            select(ModelProvider)
            .where(ModelProvider.is_active, ModelProvider.model_type == model_type)
            .order_by(ModelProvider.is_default.desc(), ModelProvider.priority.asc())
            .limit(1)
        )
        provider = result.scalar_one_or_none()

    if provider:
        logger.info("using model provider: %s (%s/%s)", provider.name, provider.provider_type, provider.model_name)
        try:
            await cache_set(cache_key, _serialize_provider(provider), ttl=_MODEL_CACHE_TTL)
        except Exception:
            pass
        return _build_model_from_provider(provider)

    raise NoModelProviderError(
        "未配置模型服务商。请在控制中心 > 模型配置中添加并激活一个模型服务商。"
    )


def _build_model_from_config(cfg: dict):
    """Build a model from a cached provider config dict."""
    provider_type = cfg["provider_type"]
    api_key = cfg["api_key"]
    base_url = cfg["base_url"]
    model_name = cfg["model_name"]
    config = cfg.get("config", {})
    temperature = config.get("temperature", 0.2)
    max_tokens = config.get("max_tokens", None)
    max_retries = config.get("max_retries", 1)
    timeout = config.get("timeout", 120)
    connect_timeout = config.get("connect_timeout", 10)
    request_timeout = (connect_timeout, timeout) if connect_timeout else timeout

    if provider_type == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            api_key=api_key, base_url=base_url or None,
            model_name=model_name, temperature=temperature,
            max_tokens=max_tokens or 4096, timeout=request_timeout,
            max_retries=max_retries,
        )

    is_deepseek = base_url and "deepseek" in base_url.lower()
    cls = _get_deepseek_class() if is_deepseek else ChatOpenAI
    return cls(
        api_key=api_key, base_url=base_url, model=model_name,
        temperature=temperature, timeout=request_timeout,
        max_retries=max_retries,
        **(dict(max_completion_tokens=max_tokens) if max_tokens else {}),
    )


async def get_embedding_model():
    return await get_default_model(model_type="embedding")


async def get_rerank_model():
    return await get_default_model(model_type="rerank")


async def get_model_for_agent(agent):
    if agent.model_provider_id:
        cache_key = f"model:agent:{agent.model_provider_id}"
        try:
            cached_cfg = await cache_get(cache_key)
        except Exception:
            cached_cfg = None
        if cached_cfg:
            return _build_model_from_config(cached_cfg)

        async with async_session_factory() as db:
            result = await db.execute(
                select(ModelProvider).where(ModelProvider.id == agent.model_provider_id)
            )
            provider = result.scalar_one_or_none()
            if provider:
                try:
                    await cache_set(cache_key, _serialize_provider(provider), ttl=_MODEL_CACHE_TTL)
                except Exception:
                    pass
                return _build_model_from_provider(provider)
    return await get_default_model()
