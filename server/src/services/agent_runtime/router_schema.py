"""Router decision schema + structured tool for RouterLLM.

Spec: .kiro/specs/agent-runtime-optimization-evolution, task 13.1 /
R-1.3 / R-1.4 / R-1.9 / R-10.1 / R-10.6.

This module intentionally has **no runtime I/O** — it only declares:

* :class:`RouterDecision` — the JSON schema RouterLLM is required to
  produce, including a post-validation ops-keyword promotion rule
  (:func:`promote_if_ops_keyword`) that upgrades accidental
  ``route="direct"`` decisions on ops-flavoured user messages back to
  ``route="executor"``.
* :data:`RouterDecisionTool` — a LangChain :class:`StructuredTool`
  whose ``args_schema`` is :class:`RouterDecision`. The callable body
  is a no-op: we only use this tool shape to build the ``tool_choice``
  payload for ``llm.bind_tools``.
* :data:`ROUTER_SYSTEM_PROMPT` — the short (<600 token) system prompt
  that tells the model how to route. Matches design.md §
  "RouterLLM 详细设计".

Keeping this module pure-python + pure-pydantic means tests can import
it without hitting Redis / model providers / LangChain networking.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# Ops keyword list (R-10.6)
# ---------------------------------------------------------------------------

# Keep these short — the rule is a "promote ops-looking direct routes to
# executor" safety net, not an intent classifier. Longer lists would
# over-trigger on benign chit-chat.
OPS_KEYWORDS: tuple[str, ...] = (
    "执行",
    "查询",
    "分析",
    "故障",
    "告警",
    "部署",
    "排查",
    "重启",
)


# ---------------------------------------------------------------------------
# RouterDecision schema
# ---------------------------------------------------------------------------


class RouterDecision(BaseModel):
    """Structured routing decision produced by RouterLLM.

    See ``ROUTER_SYSTEM_PROMPT`` for the JSON contract the model is
    expected to honour. Field constraints mirror R-10.1:

    * ``route`` is the trichotomy direct / executor / subagent.
    * ``suggested_tools`` is capped at 5 entries so Executor can build
      a narrow tool subset without blowing its prompt budget.
    * ``confidence`` is on [0, 1]. Values below the 0.4 gate (R-1.9)
      force the gateway into the full-executor fallback.
    """

    model_config = ConfigDict(extra="ignore")

    route: Literal["direct", "executor", "subagent"]
    direct_answer: str | None = None
    subagent_name: str | None = None
    suggested_tools: list[str] = Field(default_factory=list)
    reason: str = ""
    confidence: float = Field(ge=0.0, le=1.0)

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @field_validator("suggested_tools", mode="before")
    @classmethod
    def _coerce_suggested_tools(cls, v: Any) -> list[str]:
        """Accept ``None`` / strings / tuples and cap at 5 unique names.

        The router prompt asks for ≤5 tools but LLMs occasionally emit
        more, emit ``None`` when they mean "no tools", or wrap the list
        in a string. Normalise here so downstream code can always iterate
        safely.
        """
        if v is None:
            return []
        if isinstance(v, str):
            # Sometimes the model emits a comma-separated string despite
            # the schema. Split defensively.
            v = [tok.strip() for tok in v.split(",") if tok.strip()]
        if not isinstance(v, (list, tuple)):
            return []
        out: list[str] = []
        seen: set[str] = set()
        for item in v:
            name = str(item).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            out.append(name)
            if len(out) >= 5:
                break
        return out

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def fallback_executor(cls, reason: str) -> "RouterDecision":
        """Safety-net decision used whenever RouterLLM fails.

        Confidence is 0.0 so the gateway's low-confidence rule
        (``< 0.4`` → full executor) also triggers on this result, which
        means downstream callers don't need to special-case the
        fallback.
        """
        return cls(
            route="executor",
            direct_answer=None,
            subagent_name=None,
            suggested_tools=[],
            reason=reason,
            confidence=0.0,
        )


# ---------------------------------------------------------------------------
# Post-validation: ops-keyword promotion (R-10.6)
# ---------------------------------------------------------------------------


def promote_if_ops_keyword(
    decision: RouterDecision, user_message: str
) -> RouterDecision:
    """Upgrade a ``direct`` route to ``executor`` when the user's message
    contains an ops verb (R-10.6).

    Rationale: RouterLLM sometimes answers an ops question from its own
    prior knowledge ("CPU 太高怎么排查") without invoking any tool. For
    operations workloads that almost always means a wrong answer —
    the user wants the system to actually *do* something.
    """
    if decision.route != "direct":
        return decision
    text = user_message or ""
    for kw in OPS_KEYWORDS:
        if kw in text:
            return decision.model_copy(
                update={
                    "route": "executor",
                    "direct_answer": None,
                }
            )
    return decision


# ---------------------------------------------------------------------------
# RouterDecisionTool — structured tool wrapper for ``bind_tools``
# ---------------------------------------------------------------------------


def _decide_noop(**kwargs: Any) -> str:
    """No-op body for ``RouterDecisionTool``.

    We only ever use this tool to build the ``tool_choice`` payload for
    ``llm.bind_tools`` — the LLM never actually executes it; instead it
    *emits* a tool call whose args we parse as a :class:`RouterDecision`.
    The function body is present solely so LangChain accepts the tool.
    """
    return json.dumps(kwargs, ensure_ascii=False, default=str)


_ROUTER_TOOL_DESCRIPTION = (
    "Emit the routing decision for the current user message. "
    "Decide whether to answer directly (chit-chat / greetings), delegate "
    "to a named sub-agent, or invoke the executor with a short list of "
    "suggested tools. Use 'executor' whenever the user asks to run, "
    "query, diagnose, restart, or deploy anything."
)


RouterDecisionTool: StructuredTool = StructuredTool.from_function(
    func=_decide_noop,
    name="decide",
    description=_ROUTER_TOOL_DESCRIPTION,
    args_schema=RouterDecision,
    return_direct=True,
)


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------


ROUTER_SYSTEM_PROMPT: str = """你是 AIOpsOS 的请求路由器。只能通过调用名为 `decide` 的工具输出决策，绝不要输出普通文本。

目标：把单条用户消息分类到三条路径之一：
- direct: 纯闲聊、问候、简单事实确认，可以不依赖任何工具直接回答。
- executor: 任何需要查询/执行/排查/分析/部署/重启/读文件/调用 API 的运维请求。
- subagent: 明确需要由某个专职子 agent（knowledge / monitor / ops / analysis / cmdb_ingestion / a2ui_generator / report_generator）处理的复杂任务。

硬性规则：
1. 只允许通过 `decide` 工具一次性返回结果，不要解释。
2. `suggested_tools` 最多 5 个，名称必须来自提供的工具索引；不确定就留空。
3. `route=direct` 且你并不确信答案时，请改成 `executor`。
4. `route=direct` 时必须同时给出 `direct_answer`；`route=subagent` 时必须给出 `subagent_name`。
5. 只要消息里出现"执行/查询/分析/故障/告警/部署/排查/重启"等运维动词，就优先选 `executor`。
6. `confidence` 反映你对分类正确性的把握（0.0-1.0）；低于 0.4 的决策会被系统自动降级到全量工具。
"""


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------


__all__ = [
    "OPS_KEYWORDS",
    "ROUTER_SYSTEM_PROMPT",
    "RouterDecision",
    "RouterDecisionTool",
    "promote_if_ops_keyword",
]
