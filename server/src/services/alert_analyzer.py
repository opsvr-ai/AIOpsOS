"""Alert auto-analysis orchestrator.

Runs when an alert is created and matches one or more trigger rules.
Uses the deep agent with scenario context to produce structured analysis.
"""

import json
import logging
from datetime import UTC, datetime

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


async def analyze(alert, triggers) -> None:
    """Run auto-analysis on an alert using matched triggers.

    Updates alert status to 'analyzing' before running, sets
    analysis_result with structured output, and transitions
    to 'awaiting_review' on completion.
    """

    alert.status = "analyzing"

    all_outputs: list[dict] = []

    for trigger in triggers:
        try:
            output = await _run_analysis_for_trigger(alert, trigger)
            all_outputs.append(output)
        except Exception:
            logger.exception(
                "Analysis failed for alert %s, trigger %s", alert.id, trigger.id
            )
            all_outputs.append({
                "trigger_id": str(trigger.id),
                "error": "Analysis execution failed",
            })

    summary_parts: list[str] = []
    for o in all_outputs:
        if "summary" in o:
            summary_parts.append(o["summary"])

    alert.analysis_result = {
        "summary": "\n\n".join(summary_parts) if summary_parts else "Analysis produced no output.",
        "full_output": json.dumps(all_outputs, ensure_ascii=False),
        "triggers": [str(t.id) for t in triggers],
        "analyzed_at": datetime.now(UTC).isoformat(),
    }
    alert.status = "awaiting_review"


async def _run_analysis_for_trigger(alert, trigger) -> dict:
    """Run a single analysis pass for one trigger."""

    from src.models.agent import Scenario
    from src.models.base import async_session_factory

    scenario_name = "unknown"
    async with async_session_factory() as db:
        scenario = await db.get(Scenario, trigger.scenario_id)
        scenario_name = scenario.name if scenario else "unknown"

    prompt = (
        f"[SYSTEM: You are a senior SRE diagnosing an alert. "
        f"Analyze the alert data and produce structured findings in Chinese.]\n\n"
        f"Scenario: {scenario_name}\n"
        f"Alert Title: {alert.title}\n"
        f"Severity: {alert.severity}\n"
        f"Source: {alert.source}\n"
        f"Raw Data: {json.dumps(alert.raw_event, ensure_ascii=False)}\n\n"
        f"Please provide:\n"
        f"1. Root cause analysis\n"
        f"2. Impact assessment\n"
        f"3. Recommended actions\n"
    )

    agent = await get_deep_agent()
    result = await agent.ainvoke({
        "messages": [HumanMessage(content=prompt)],
    })

    output_text = ""
    messages = result.get("messages", [])
    if messages:
        output_text = str(messages[-1].content) if hasattr(messages[-1], "content") else str(messages[-1])

    return {
        "trigger_id": str(trigger.id),
        "scenario_name": scenario_name,
        "summary": output_text[:1000] if output_text else "No analysis produced.",
        "full_output": output_text,
        "analyzed_at": datetime.now(UTC).isoformat(),
    }
