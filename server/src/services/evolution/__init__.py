"""Evolution-pipeline services.

This package holds the in-process pieces that wire candidate
promotion, prompt hot-reload, and shadow/AB routing together. Each
module is deliberately narrow so it can be unit-tested without
dragging in the rest of the pipeline:

* :mod:`prompt_registry` — lock-free snapshot of sub-agent prompts
* :mod:`prompt_reloader` — Kafka consumer driving the registry from
  ``ops.agent.promotion`` events (task 20).
* :mod:`candidate_store` — cohesive propose/read/update surface for
  skill / prompt_patch / tool_config candidates (task 21.4).
* :mod:`promoter` — status-machine driver for the three candidate
  kinds. Currently exposes the rollback surface (task 23.3); the
  forward-motion :meth:`Promoter.step` lands with task 23.1.
"""

from src.services.evolution.candidate_store import (
    ALL_STATUSES,
    CandidateRow,
    InvalidStateTransition,
    SkillCandidateStore,
    STATE_TRANSITIONS,
)
from src.services.evolution.prompt_registry import (
    PromotionEvent,
    PromptStatus,
    PromptVersion,
    ResolvedPrompt,
    SubAgentPromptRegistry,
    get_prompt_registry,
    shutdown_prompt_registry,
)
from src.services.evolution.prompt_reloader import (
    PROMOTION_TOPIC,
    PromptReloader,
)
from src.services.evolution.promoter import (
    Promoter,
    PromoterStepResult,
    RollbackResult,
    ShadowStatsProvider,
)
from src.services.evolution.shadow_runner import (
    SHADOW_EVAL_SET_NAME,
    CandidateRunResult,
    LiveRequest,
    ShadowComparisonStat,
    ShadowRunner,
)

__all__ = [
    "ALL_STATUSES",
    "CandidateRow",
    "CandidateRunResult",
    "InvalidStateTransition",
    "LiveRequest",
    "PROMOTION_TOPIC",
    "PromotionEvent",
    "Promoter",
    "PromoterStepResult",
    "PromptReloader",
    "PromptStatus",
    "PromptVersion",
    "ResolvedPrompt",
    "RollbackResult",
    "SHADOW_EVAL_SET_NAME",
    "STATE_TRANSITIONS",
    "ShadowComparisonStat",
    "ShadowRunner",
    "ShadowStatsProvider",
    "SkillCandidateStore",
    "SubAgentPromptRegistry",
    "get_prompt_registry",
    "shutdown_prompt_registry",
]
