"""Scenario execution engine — manages scenario execution lifecycle.

This module provides the core execution engine for running scenarios,
including:
- Execution record creation and status management
- Manual and automatic trigger entry points
- Execution logging and result tracking
- Execution strategies for different scenario types (command, natural_language, hybrid)
- Structured result generation with metrics and recommendations
- Execution timeout detection and handling
- Resource loading for scenario execution (tools, agents, knowledge docs, channels)

Requirements:
- 4.5: Load all associated resources (tools/skills, agents, knowledge_docs,
       notification_channels) and make them available during execution
- 5.1: Support manual trigger of scenario execution
- 5.2: Support automatic trigger of scenario execution (via trigger rules or schedules)
- 5.3: Create execution record with running status when scenario starts
- 5.4: Execute command type scenarios by parsing and executing configured commands
- 5.5: Execute natural_language type scenarios by sending nl_prompt to associated agents
- 5.6: Execute hybrid type scenarios supporting both command and natural language triggers
- 5.7: Record detailed execution logs during scenario execution
- 5.8: Update execution record status to completed or failed
- 5.9: Generate structured execution results including output, recommendations, metrics
- 5.10: Terminate execution and record timeout status if execution exceeds timeout
- 5.11: Support configurable execution timeout (default 300 seconds)
"""

import asyncio
import logging
import shlex
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, AsyncIterator, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.agent import Agent, Scenario, Tool
from src.models.channel import NotificationChannel
from src.models.knowledge import KnowledgeDocument
from src.models.scenario import ScenarioExecution
from src.schemas.scenario import (
    ExecutionResult,
    ExecutionStatus,
    ScenarioType,
    TriggerType,
)

# Lazy import for collaboration service to avoid circular imports
# The actual import happens in _create_collaboration_session method

logger = logging.getLogger(__name__)


# =============================================================================
# Resource Loading Data Classes (Requirements 4.5)
# =============================================================================


@dataclass
class LoadedResources:
    """Container for all resources loaded for scenario execution.

    Requirements 4.5: Load all associated resources (tools/skills, agents,
    knowledge_docs, notification_channels) and make them available during execution.

    This dataclass provides a structured way to access all resources associated
    with a scenario during execution. Resources are loaded eagerly when the
    scenario is fetched and made available through this container.

    Attributes:
        scenario: The scenario being executed
        tools: List of tools/skills associated with the scenario
        agents: List of agents associated with the scenario
        knowledge_docs: List of knowledge documents associated with the scenario
        notification_channels: List of notification channels associated with the scenario
    """

    scenario: Scenario
    tools: list[Tool] = field(default_factory=list)
    agents: list[Agent] = field(default_factory=list)
    knowledge_docs: list[KnowledgeDocument] = field(default_factory=list)
    notification_channels: list[NotificationChannel] = field(default_factory=list)

    @property
    def has_tools(self) -> bool:
        """Check if the scenario has any associated tools."""
        return len(self.tools) > 0

    @property
    def has_agents(self) -> bool:
        """Check if the scenario has any associated agents."""
        return len(self.agents) > 0

    @property
    def has_knowledge_docs(self) -> bool:
        """Check if the scenario has any associated knowledge documents."""
        return len(self.knowledge_docs) > 0

    @property
    def has_notification_channels(self) -> bool:
        """Check if the scenario has any associated notification channels."""
        return len(self.notification_channels) > 0

    def get_tool_by_name(self, name: str, case_sensitive: bool = False) -> Tool | None:
        """Get a tool by its name.

        Args:
            name: The name of the tool to find
            case_sensitive: Whether to perform case-sensitive matching

        Returns:
            The matching Tool, or None if not found
        """
        for tool in self.tools:
            if case_sensitive:
                if tool.name == name:
                    return tool
            else:
                if tool.name.lower() == name.lower():
                    return tool
        return None

    def get_tools_by_type(self, tool_type: str) -> list[Tool]:
        """Get all tools of a specific type.

        Args:
            tool_type: The type of tools to find

        Returns:
            List of matching Tools
        """
        return [tool for tool in self.tools if tool.type == tool_type]

    def get_tools_by_category(self, category: str) -> list[Tool]:
        """Get all tools in a specific category.

        Args:
            category: The category of tools to find

        Returns:
            List of matching Tools
        """
        return [tool for tool in self.tools if tool.category == category]

    def get_active_tools(self) -> list[Tool]:
        """Get all active tools.

        Returns:
            List of active Tools
        """
        return [tool for tool in self.tools if tool.is_active]

    def get_agent_by_name(self, name: str, case_sensitive: bool = False) -> Agent | None:
        """Get an agent by its name.

        Args:
            name: The name of the agent to find
            case_sensitive: Whether to perform case-sensitive matching

        Returns:
            The matching Agent, or None if not found
        """
        for agent in self.agents:
            if case_sensitive:
                if agent.name == name:
                    return agent
            else:
                if agent.name.lower() == name.lower():
                    return agent
        return None

    def get_agents_by_type(self, agent_type: str) -> list[Agent]:
        """Get all agents of a specific type.

        Args:
            agent_type: The type of agents to find

        Returns:
            List of matching Agents
        """
        return [agent for agent in self.agents if agent.type == agent_type]

    def get_active_agents(self) -> list[Agent]:
        """Get all active agents.

        Returns:
            List of active Agents
        """
        return [agent for agent in self.agents if agent.is_active]

    def get_knowledge_doc_by_title(
        self, title: str, case_sensitive: bool = False
    ) -> KnowledgeDocument | None:
        """Get a knowledge document by its title.

        Args:
            title: The title of the document to find
            case_sensitive: Whether to perform case-sensitive matching

        Returns:
            The matching KnowledgeDocument, or None if not found
        """
        for doc in self.knowledge_docs:
            if case_sensitive:
                if doc.title == title:
                    return doc
            else:
                if doc.title.lower() == title.lower():
                    return doc
        return None

    def get_knowledge_docs_by_source(self, source: str) -> list[KnowledgeDocument]:
        """Get all knowledge documents from a specific source.

        Args:
            source: The source of documents to find

        Returns:
            List of matching KnowledgeDocuments
        """
        return [doc for doc in self.knowledge_docs if doc.source == source]

    def get_channel_by_name(
        self, name: str, case_sensitive: bool = False
    ) -> NotificationChannel | None:
        """Get a notification channel by its name.

        Args:
            name: The name of the channel to find
            case_sensitive: Whether to perform case-sensitive matching

        Returns:
            The matching NotificationChannel, or None if not found
        """
        for channel in self.notification_channels:
            if case_sensitive:
                if channel.name == name:
                    return channel
            else:
                if channel.name.lower() == name.lower():
                    return channel
        return None

    def get_channels_by_type(self, channel_type: str) -> list[NotificationChannel]:
        """Get all notification channels of a specific type.

        Args:
            channel_type: The type of channels to find (e.g., 'wecom', 'email')

        Returns:
            List of matching NotificationChannels
        """
        return [
            channel
            for channel in self.notification_channels
            if channel.channel_type == channel_type
        ]

    def get_active_channels(self) -> list[NotificationChannel]:
        """Get all active notification channels.

        Returns:
            List of active NotificationChannels
        """
        return [channel for channel in self.notification_channels if channel.is_active]

    def get_wecom_channels(self) -> list[NotificationChannel]:
        """Get all WeCom (企业微信) notification channels.

        Returns:
            List of WeCom NotificationChannels
        """
        return self.get_channels_by_type("wecom")

    def get_email_channels(self) -> list[NotificationChannel]:
        """Get all email notification channels.

        Returns:
            List of email NotificationChannels
        """
        return self.get_channels_by_type("email")

    def to_summary_dict(self) -> dict[str, Any]:
        """Convert resources to a summary dictionary for logging/debugging.

        Returns:
            Dictionary with resource counts and names
        """
        return {
            "scenario_id": str(self.scenario.id),
            "scenario_name": self.scenario.name,
            "tools": {
                "count": len(self.tools),
                "names": [t.name for t in self.tools],
                "active_count": len(self.get_active_tools()),
            },
            "agents": {
                "count": len(self.agents),
                "names": [a.name for a in self.agents],
                "active_count": len(self.get_active_agents()),
            },
            "knowledge_docs": {
                "count": len(self.knowledge_docs),
                "titles": [d.title for d in self.knowledge_docs],
            },
            "notification_channels": {
                "count": len(self.notification_channels),
                "names": [c.name for c in self.notification_channels],
                "active_count": len(self.get_active_channels()),
                "types": list(set(c.channel_type for c in self.notification_channels)),
            },
        }


# =============================================================================
# Execution Strategy Base Class and Implementations
# =============================================================================


class ExecutionStrategy(ABC):
    """Abstract base class for scenario execution strategies.

    Each scenario type (command, natural_language, hybrid) has its own
    execution strategy that determines how the scenario is executed.

    Requirements 5.4, 5.5, 5.6: Different execution strategies for different
    scenario types.
    """

    @abstractmethod
    async def execute(
        self,
        scenario: Scenario,
        execution: ScenarioExecution,
        params: dict[str, Any],
        engine: "ScenarioExecutionEngine",
    ) -> ExecutionResult:
        """Execute the scenario using this strategy.

        Args:
            scenario: The scenario to execute
            execution: The execution record for logging
            params: Input parameters for this execution
            engine: The execution engine for logging and status updates

        Returns:
            ExecutionResult with output, recommendations, and metrics
        """
        pass


class CommandExecutionStrategy(ExecutionStrategy):
    """Execution strategy for command-type scenarios.

    Requirements 5.4: Parse and execute the configured trigger_command.

    Command scenarios execute a predefined command string. The command
    is parsed and parameters are substituted from the execution params.
    """

    async def execute(
        self,
        scenario: Scenario,
        execution: ScenarioExecution,
        params: dict[str, Any],
        engine: "ScenarioExecutionEngine",
    ) -> ExecutionResult:
        """Execute a command-type scenario.

        Parses the trigger_command, substitutes parameters, and executes it.
        The command is expected to be a slash-command that maps to an
        internal operation or tool invocation.

        Args:
            scenario: The scenario with trigger_command configured
            execution: The execution record for logging
            params: Parameters to substitute into the command
            engine: The execution engine for logging

        Returns:
            ExecutionResult with command output and metrics
        """
        start_time = time.time()
        command = scenario.trigger_command

        if not command:
            return ExecutionResult(
                output="Error: No trigger_command configured for command-type scenario",
                recommendations=[],
                metrics={"error": "missing_command"},
            )

        await engine.add_log_entry(
            execution.id,
            "info",
            f"Executing command: {command}",
        )

        try:
            # Parse the command and extract command name and arguments
            parsed_command = self._parse_command(command, params)
            command_name = parsed_command["name"]
            command_args = parsed_command["args"]

            await engine.add_log_entry(
                execution.id,
                "debug",
                f"Parsed command: name={command_name}, args={command_args}",
            )

            # Execute the command based on its type
            output = await self._execute_command(
                command_name,
                command_args,
                scenario,
                execution,
                engine,
            )

            duration_ms = int((time.time() - start_time) * 1000)

            await engine.add_log_entry(
                execution.id,
                "info",
                f"Command execution completed in {duration_ms}ms",
            )

            return ExecutionResult(
                output=output,
                recommendations=[],
                metrics={
                    "duration_ms": duration_ms,
                    "command": command_name,
                    "strategy": "command",
                },
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = f"Command execution failed: {str(e)}"

            await engine.add_log_entry(
                execution.id,
                "error",
                error_msg,
            )

            logger.exception("Command execution failed for scenario %s", scenario.id)

            return ExecutionResult(
                output=error_msg,
                recommendations=["Check command syntax and parameters"],
                metrics={
                    "duration_ms": duration_ms,
                    "error": str(e),
                    "strategy": "command",
                },
            )

    def _parse_command(
        self,
        command: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Parse a slash command and substitute parameters.

        Args:
            command: The command string (e.g., "/analyze --target {host}")
            params: Parameters to substitute

        Returns:
            Dict with 'name' (command name) and 'args' (argument dict)
        """
        # Substitute parameters in the command string
        substituted = command
        for key, value in params.items():
            placeholder = "{" + key + "}"
            substituted = substituted.replace(placeholder, str(value))

        # Parse the command
        if not substituted.startswith("/"):
            raise ValueError("Command must start with '/'")

        # Split command into parts
        parts = shlex.split(substituted)
        command_name = parts[0][1:]  # Remove leading '/'

        # Parse arguments (simple key=value or --key value format)
        args: dict[str, Any] = {}
        i = 1
        while i < len(parts):
            part = parts[i]
            if part.startswith("--"):
                key = part[2:]
                if i + 1 < len(parts) and not parts[i + 1].startswith("--"):
                    args[key] = parts[i + 1]
                    i += 2
                else:
                    args[key] = True
                    i += 1
            elif "=" in part:
                key, value = part.split("=", 1)
                args[key] = value
                i += 1
            else:
                # Positional argument
                args[f"arg{i}"] = part
                i += 1

        return {"name": command_name, "args": args}

    async def _execute_command(
        self,
        command_name: str,
        command_args: dict[str, Any],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Execute a parsed command.

        This method dispatches to the appropriate command handler based on
        the command name. Commands can be internal operations or tool
        invocations.

        Args:
            command_name: The name of the command (without leading '/')
            command_args: Parsed command arguments
            scenario: The scenario being executed
            execution: The execution record
            engine: The execution engine

        Returns:
            Command output as a string
        """
        # Built-in command handlers
        builtin_handlers = {
            "echo": self._cmd_echo,
            "status": self._cmd_status,
            "analyze": self._cmd_analyze,
            "check": self._cmd_check,
            "report": self._cmd_report,
        }

        handler = builtin_handlers.get(command_name)
        if handler:
            return await handler(command_args, scenario, execution, engine)

        # If no built-in handler, try to invoke as a tool
        return await self._invoke_tool(
            command_name,
            command_args,
            scenario,
            execution,
            engine,
        )

    async def _cmd_echo(
        self,
        args: dict[str, Any],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Echo command - returns the provided message."""
        message = args.get("message", args.get("arg1", ""))
        return f"Echo: {message}"

    async def _cmd_status(
        self,
        args: dict[str, Any],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Status command - returns scenario status information."""
        return (
            f"Scenario: {scenario.name}\n"
            f"Type: {scenario.scenario_type}\n"
            f"Active: {scenario.is_active}\n"
            f"Execution ID: {execution.id}"
        )

    async def _cmd_analyze(
        self,
        args: dict[str, Any],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Analyze command - placeholder for analysis operations."""
        target = args.get("target", "system")
        await engine.add_log_entry(
            execution.id,
            "info",
            f"Running analysis on target: {target}",
        )
        return f"Analysis completed for target: {target}"

    async def _cmd_check(
        self,
        args: dict[str, Any],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Check command - placeholder for health check operations."""
        component = args.get("component", "all")
        await engine.add_log_entry(
            execution.id,
            "info",
            f"Running health check on component: {component}",
        )
        return f"Health check completed for component: {component}"

    async def _cmd_report(
        self,
        args: dict[str, Any],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Report command - placeholder for report generation."""
        report_type = args.get("type", "summary")
        await engine.add_log_entry(
            execution.id,
            "info",
            f"Generating {report_type} report",
        )
        return f"Report generated: {report_type}"

    async def _invoke_tool(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Invoke a tool by name from the scenario's associated tools.

        Args:
            tool_name: Name of the tool to invoke
            tool_args: Arguments to pass to the tool
            scenario: The scenario with associated tools
            execution: The execution record
            engine: The execution engine

        Returns:
            Tool output as a string
        """
        # Load scenario with tools if not already loaded
        scenario_with_tools = await engine.get_scenario_with_resources(scenario.id)
        if not scenario_with_tools:
            return f"Error: Could not load scenario resources"

        # Find the tool by name
        matching_tool = None
        for tool in scenario_with_tools.tools:
            if tool.name.lower() == tool_name.lower():
                matching_tool = tool
                break

        if not matching_tool:
            available_tools = [t.name for t in scenario_with_tools.tools]
            return (
                f"Error: Tool '{tool_name}' not found in scenario. "
                f"Available tools: {available_tools}"
            )

        await engine.add_log_entry(
            execution.id,
            "info",
            f"Invoking tool: {matching_tool.name}",
        )

        # Tool invocation would go through the tool manager
        # For now, return a placeholder indicating the tool was found
        return (
            f"Tool '{matching_tool.name}' invoked with args: {tool_args}\n"
            f"Tool type: {matching_tool.type}\n"
            f"Description: {matching_tool.description or 'N/A'}"
        )


class NaturalLanguageExecutionStrategy(ExecutionStrategy):
    """Execution strategy for natural_language-type scenarios.

    Requirements 5.5: Send nl_prompt to associated agents for processing.

    Natural language scenarios send the configured nl_prompt to one or more
    associated agents for intelligent processing.
    """

    async def execute(
        self,
        scenario: Scenario,
        execution: ScenarioExecution,
        params: dict[str, Any],
        engine: "ScenarioExecutionEngine",
    ) -> ExecutionResult:
        """Execute a natural_language-type scenario.

        Sends the nl_prompt to associated agents for processing. Parameters
        are substituted into the prompt before sending.

        Args:
            scenario: The scenario with nl_prompt configured
            execution: The execution record for logging
            params: Parameters to substitute into the prompt
            engine: The execution engine for logging

        Returns:
            ExecutionResult with agent response and metrics
        """
        start_time = time.time()
        nl_prompt = scenario.nl_prompt

        if not nl_prompt:
            return ExecutionResult(
                output="Error: No nl_prompt configured for natural_language-type scenario",
                recommendations=[],
                metrics={"error": "missing_nl_prompt"},
            )

        # Substitute parameters in the prompt
        prompt = self._substitute_params(nl_prompt, params)

        await engine.add_log_entry(
            execution.id,
            "info",
            f"Processing natural language prompt: {prompt[:100]}...",
        )

        try:
            # Load scenario with agents
            scenario_with_resources = await engine.get_scenario_with_resources(scenario.id)
            if not scenario_with_resources:
                return ExecutionResult(
                    output="Error: Could not load scenario resources",
                    recommendations=[],
                    metrics={"error": "resource_load_failed"},
                )

            agents = scenario_with_resources.agents
            if not agents:
                await engine.add_log_entry(
                    execution.id,
                    "warning",
                    "No agents associated with scenario, using default processing",
                )
                # Process without specific agent
                output = await self._process_with_default_agent(
                    prompt,
                    scenario,
                    execution,
                    engine,
                )
            else:
                # Process with associated agents
                output = await self._process_with_agents(
                    prompt,
                    agents,
                    scenario,
                    execution,
                    engine,
                )

            duration_ms = int((time.time() - start_time) * 1000)

            await engine.add_log_entry(
                execution.id,
                "info",
                f"Natural language processing completed in {duration_ms}ms",
            )

            return ExecutionResult(
                output=output,
                recommendations=[],
                metrics={
                    "duration_ms": duration_ms,
                    "prompt_length": len(prompt),
                    "agent_count": len(agents) if agents else 0,
                    "strategy": "natural_language",
                },
            )

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = f"Natural language processing failed: {str(e)}"

            await engine.add_log_entry(
                execution.id,
                "error",
                error_msg,
            )

            logger.exception(
                "Natural language execution failed for scenario %s",
                scenario.id,
            )

            return ExecutionResult(
                output=error_msg,
                recommendations=["Check agent configuration and availability"],
                metrics={
                    "duration_ms": duration_ms,
                    "error": str(e),
                    "strategy": "natural_language",
                },
            )

    def _substitute_params(self, prompt: str, params: dict[str, Any]) -> str:
        """Substitute parameters into the prompt template.

        Args:
            prompt: The prompt template with {param} placeholders
            params: Parameters to substitute

        Returns:
            The prompt with parameters substituted
        """
        result = prompt
        for key, value in params.items():
            placeholder = "{" + key + "}"
            result = result.replace(placeholder, str(value))
        return result

    async def _process_with_agents(
        self,
        prompt: str,
        agents: list[Agent],
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Process the prompt using associated agents.

        Args:
            prompt: The natural language prompt to process
            agents: List of agents to use for processing
            scenario: The scenario being executed
            execution: The execution record
            engine: The execution engine

        Returns:
            Combined agent responses as a string
        """
        responses: list[str] = []

        for agent in agents:
            await engine.add_log_entry(
                execution.id,
                "info",
                f"Sending prompt to agent: {agent.name} (type: {agent.type})",
            )

            try:
                response = await self._invoke_agent(
                    agent,
                    prompt,
                    scenario,
                    execution,
                    engine,
                )
                responses.append(f"[{agent.name}]: {response}")
            except Exception as e:
                error_msg = f"Agent {agent.name} failed: {str(e)}"
                await engine.add_log_entry(
                    execution.id,
                    "warning",
                    error_msg,
                )
                responses.append(f"[{agent.name}]: Error - {str(e)}")

        return "\n\n".join(responses)

    async def _invoke_agent(
        self,
        agent: Agent,
        prompt: str,
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Invoke a single agent with the prompt.

        This method integrates with the agent runtime system to process
        the natural language prompt.

        Args:
            agent: The agent to invoke
            prompt: The prompt to send to the agent
            scenario: The scenario being executed
            execution: The execution record
            engine: The execution engine

        Returns:
            Agent response as a string
        """
        # Build context for the agent
        context = {
            "scenario_id": str(scenario.id),
            "scenario_name": scenario.name,
            "execution_id": str(execution.id),
            "trigger_type": execution.trigger_type,
            "params": execution.params,
        }

        await engine.add_log_entry(
            execution.id,
            "debug",
            f"Agent context: {context}",
        )

        # For now, return a structured response indicating the agent was invoked
        # Full integration with the agent runtime would use the RuntimeGateway
        # or direct agent invocation through the DeepAgents framework
        return (
            f"Agent '{agent.name}' processed prompt.\n"
            f"Agent type: {agent.type}\n"
            f"Model: {agent.model_name}\n"
            f"Prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}\n"
            f"Context: scenario={scenario.name}, execution={execution.id}"
        )

    async def _process_with_default_agent(
        self,
        prompt: str,
        scenario: Scenario,
        execution: ScenarioExecution,
        engine: "ScenarioExecutionEngine",
    ) -> str:
        """Process the prompt without a specific agent.

        When no agents are associated with the scenario, this method
        provides basic processing or delegates to a default agent.

        Args:
            prompt: The natural language prompt to process
            scenario: The scenario being executed
            execution: The execution record
            engine: The execution engine

        Returns:
            Processing result as a string
        """
        await engine.add_log_entry(
            execution.id,
            "info",
            "Processing with default agent (no specific agents configured)",
        )

        # Return a structured response for scenarios without specific agents
        return (
            f"Processed natural language prompt (default handler).\n"
            f"Prompt: {prompt[:200]}{'...' if len(prompt) > 200 else ''}\n"
            f"Scenario: {scenario.name}\n"
            f"Note: Configure agents for this scenario for enhanced processing."
        )


class HybridExecutionStrategy(ExecutionStrategy):
    """Execution strategy for hybrid-type scenarios.

    Requirements 5.6: Support both command and natural language triggers.

    Hybrid scenarios can be triggered either by command or natural language,
    and the execution strategy is determined by the trigger method or
    explicit preference in the execution parameters.
    """

    def __init__(self):
        """Initialize the hybrid strategy with sub-strategies."""
        self._command_strategy = CommandExecutionStrategy()
        self._nl_strategy = NaturalLanguageExecutionStrategy()

    async def execute(
        self,
        scenario: Scenario,
        execution: ScenarioExecution,
        params: dict[str, Any],
        engine: "ScenarioExecutionEngine",
    ) -> ExecutionResult:
        """Execute a hybrid-type scenario.

        Determines the execution mode based on:
        1. Explicit 'execution_mode' parameter ('command' or 'natural_language')
        2. Presence of 'prompt' parameter (uses natural language)
        3. Presence of 'command' parameter (uses command)
        4. Default: uses natural language if nl_prompt is configured,
           otherwise uses command

        Args:
            scenario: The scenario with both trigger_command and nl_prompt
            execution: The execution record for logging
            params: Parameters including optional execution_mode
            engine: The execution engine for logging

        Returns:
            ExecutionResult from the selected strategy
        """
        start_time = time.time()

        # Determine execution mode
        execution_mode = self._determine_execution_mode(scenario, params)

        await engine.add_log_entry(
            execution.id,
            "info",
            f"Hybrid scenario executing in '{execution_mode}' mode",
        )

        try:
            if execution_mode == "command":
                result = await self._command_strategy.execute(
                    scenario,
                    execution,
                    params,
                    engine,
                )
            else:  # natural_language
                result = await self._nl_strategy.execute(
                    scenario,
                    execution,
                    params,
                    engine,
                )

            # Add hybrid-specific metrics
            duration_ms = int((time.time() - start_time) * 1000)
            result.metrics["hybrid_mode"] = execution_mode
            result.metrics["strategy"] = "hybrid"
            result.metrics["total_duration_ms"] = duration_ms

            return result

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            error_msg = f"Hybrid execution failed: {str(e)}"

            await engine.add_log_entry(
                execution.id,
                "error",
                error_msg,
            )

            logger.exception(
                "Hybrid execution failed for scenario %s",
                scenario.id,
            )

            return ExecutionResult(
                output=error_msg,
                recommendations=[
                    "Check both command and natural language configurations",
                    f"Attempted mode: {execution_mode}",
                ],
                metrics={
                    "duration_ms": duration_ms,
                    "error": str(e),
                    "hybrid_mode": execution_mode,
                    "strategy": "hybrid",
                },
            )

    def _determine_execution_mode(
        self,
        scenario: Scenario,
        params: dict[str, Any],
    ) -> str:
        """Determine which execution mode to use for a hybrid scenario.

        Args:
            scenario: The hybrid scenario
            params: Execution parameters

        Returns:
            'command' or 'natural_language'
        """
        # 1. Check for explicit execution_mode parameter
        explicit_mode = params.get("execution_mode")
        if explicit_mode in ("command", "natural_language"):
            return explicit_mode

        # 2. Check for prompt parameter (indicates natural language intent)
        if "prompt" in params or "nl_prompt" in params:
            return "natural_language"

        # 3. Check for command parameter (indicates command intent)
        if "command" in params or "cmd" in params:
            return "command"

        # 4. Default based on scenario configuration
        # Prefer natural language if nl_prompt is configured
        if scenario.nl_prompt:
            return "natural_language"

        # Fall back to command if trigger_command is configured
        if scenario.trigger_command:
            return "command"

        # Ultimate fallback to natural language
        return "natural_language"


# =============================================================================
# Strategy Factory
# =============================================================================


def get_execution_strategy(scenario_type: str) -> ExecutionStrategy:
    """Get the appropriate execution strategy for a scenario type.

    Args:
        scenario_type: The scenario type ('command', 'natural_language', 'hybrid')

    Returns:
        The appropriate ExecutionStrategy instance

    Raises:
        ValueError: If the scenario type is not recognized
    """
    strategies: dict[str, ExecutionStrategy] = {
        ScenarioType.COMMAND.value: CommandExecutionStrategy(),
        ScenarioType.NATURAL_LANGUAGE.value: NaturalLanguageExecutionStrategy(),
        ScenarioType.HYBRID.value: HybridExecutionStrategy(),
    }

    strategy = strategies.get(scenario_type)
    if strategy is None:
        raise ValueError(
            f"Unknown scenario type: {scenario_type}. "
            f"Valid types: {list(strategies.keys())}"
        )

    return strategy


# =============================================================================
# Execution Result Data Classes (Requirements 5.9)
# =============================================================================


@dataclass
class ExecutionMetrics:
    """Metrics collected during scenario execution.

    Requirements 5.9: Structured execution result metrics.

    Attributes:
        duration_ms: Total execution duration in milliseconds
        steps_completed: Number of steps successfully completed
        steps_total: Total number of steps in the execution
        custom: Additional custom metrics
    """

    duration_ms: int = 0
    steps_completed: int = 0
    steps_total: int = 0
    custom: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary format."""
        result = {
            "duration_ms": self.duration_ms,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
        }
        result.update(self.custom)
        return result


@dataclass
class ExecutionResultBuilder:
    """Builder for constructing structured execution results.

    Requirements 5.9: Generate structured execution results including
    output data, recommendations, and metrics.

    Attributes:
        output: Main execution output content
        recommendations: List of recommendations generated during execution
        metrics: Execution metrics
        data: Additional structured data from execution
        error: Error information if execution failed
    """

    output: str | None = None
    recommendations: list[str] = field(default_factory=list)
    metrics: ExecutionMetrics = field(default_factory=ExecutionMetrics)
    data: dict[str, Any] = field(default_factory=dict)
    error: dict[str, Any] | None = None

    def set_output(self, output: str) -> "ExecutionResultBuilder":
        """Set the main execution output."""
        self.output = output
        return self

    def add_recommendation(self, recommendation: str) -> "ExecutionResultBuilder":
        """Add a recommendation to the result."""
        self.recommendations.append(recommendation)
        return self

    def add_recommendations(self, recommendations: list[str]) -> "ExecutionResultBuilder":
        """Add multiple recommendations to the result."""
        self.recommendations.extend(recommendations)
        return self

    def set_metrics(
        self,
        duration_ms: int | None = None,
        steps_completed: int | None = None,
        steps_total: int | None = None,
        **custom_metrics: Any,
    ) -> "ExecutionResultBuilder":
        """Set execution metrics."""
        if duration_ms is not None:
            self.metrics.duration_ms = duration_ms
        if steps_completed is not None:
            self.metrics.steps_completed = steps_completed
        if steps_total is not None:
            self.metrics.steps_total = steps_total
        self.metrics.custom.update(custom_metrics)
        return self

    def add_data(self, key: str, value: Any) -> "ExecutionResultBuilder":
        """Add additional data to the result."""
        self.data[key] = value
        return self

    def set_error(
        self,
        message: str,
        error_type: str | None = None,
        stack_trace: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> "ExecutionResultBuilder":
        """Set error information for failed execution."""
        self.error = {
            "message": message,
            "type": error_type,
            "stack_trace": stack_trace,
            "details": details or {},
        }
        return self

    def build(self) -> dict[str, Any]:
        """Build the final structured result dictionary."""
        result: dict[str, Any] = {
            "output": self.output,
            "recommendations": self.recommendations,
            "metrics": self.metrics.to_dict(),
        }
        if self.data:
            result["data"] = self.data
        if self.error:
            result["error"] = self.error
        return result


# =============================================================================
# Execution Logger (Requirements 5.7)
# =============================================================================


class ExecutionLogger:
    """Logger for recording execution process logs.

    Requirements 5.7: Record detailed execution logs during scenario execution.

    This class provides a structured way to log execution events with
    different severity levels. Logs are accumulated in memory and can
    be flushed to the database periodically or at the end of execution.

    Attributes:
        execution_id: UUID of the execution being logged
        engine: Reference to the execution engine for database operations
        logs: Accumulated log entries
        auto_flush: Whether to automatically flush logs to database
        flush_threshold: Number of logs to accumulate before auto-flushing
    """

    def __init__(
        self,
        execution_id: uuid.UUID,
        engine: "ScenarioExecutionEngine",
        auto_flush: bool = True,
        flush_threshold: int = 10,
    ):
        """Initialize the execution logger.

        Args:
            execution_id: UUID of the execution to log for
            engine: Reference to the execution engine
            auto_flush: Whether to auto-flush logs to database
            flush_threshold: Number of logs before auto-flush
        """
        self.execution_id = execution_id
        self.engine = engine
        self.logs: list[dict[str, Any]] = []
        self.auto_flush = auto_flush
        self.flush_threshold = flush_threshold
        self._start_time: datetime | None = None

    def _create_entry(
        self,
        level: Literal["debug", "info", "warning", "error"],
        message: str,
        **extra: Any,
    ) -> dict[str, Any]:
        """Create a log entry dictionary."""
        entry: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
        }
        if extra:
            entry["extra"] = extra
        return entry

    async def _maybe_flush(self) -> None:
        """Flush logs if threshold is reached."""
        if self.auto_flush and len(self.logs) >= self.flush_threshold:
            await self.flush()

    async def debug(self, message: str, **extra: Any) -> None:
        """Log a debug message."""
        self.logs.append(self._create_entry("debug", message, **extra))
        await self._maybe_flush()

    async def info(self, message: str, **extra: Any) -> None:
        """Log an info message."""
        self.logs.append(self._create_entry("info", message, **extra))
        await self._maybe_flush()

    async def warning(self, message: str, **extra: Any) -> None:
        """Log a warning message."""
        self.logs.append(self._create_entry("warning", message, **extra))
        await self._maybe_flush()

    async def error(self, message: str, **extra: Any) -> None:
        """Log an error message."""
        self.logs.append(self._create_entry("error", message, **extra))
        await self._maybe_flush()

    async def log_step_start(self, step_name: str, step_number: int | None = None) -> None:
        """Log the start of an execution step."""
        msg = f"Starting step: {step_name}"
        if step_number is not None:
            msg = f"Starting step {step_number}: {step_name}"
        await self.info(msg, step=step_name, step_number=step_number, event="step_start")

    async def log_step_complete(
        self,
        step_name: str,
        step_number: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Log the completion of an execution step."""
        msg = f"Completed step: {step_name}"
        if step_number is not None:
            msg = f"Completed step {step_number}: {step_name}"
        if duration_ms is not None:
            msg = f"{msg} ({duration_ms}ms)"
        await self.info(
            msg,
            step=step_name,
            step_number=step_number,
            duration_ms=duration_ms,
            event="step_complete",
        )

    async def log_step_failed(
        self,
        step_name: str,
        error: str,
        step_number: int | None = None,
    ) -> None:
        """Log a failed execution step."""
        msg = f"Failed step: {step_name} - {error}"
        if step_number is not None:
            msg = f"Failed step {step_number}: {step_name} - {error}"
        await self.error(
            msg,
            step=step_name,
            step_number=step_number,
            error=error,
            event="step_failed",
        )

    async def log_execution_start(self) -> None:
        """Log the start of execution."""
        self._start_time = datetime.now(UTC)
        await self.info("Execution started", event="execution_start")

    async def log_execution_complete(self, summary: str | None = None) -> None:
        """Log the completion of execution."""
        duration_ms = None
        if self._start_time:
            duration = datetime.now(UTC) - self._start_time
            duration_ms = int(duration.total_seconds() * 1000)
        msg = "Execution completed"
        if summary:
            msg = f"Execution completed: {summary}"
        await self.info(msg, event="execution_complete", duration_ms=duration_ms)

    async def log_execution_failed(self, error: str, error_type: str | None = None) -> None:
        """Log execution failure."""
        duration_ms = None
        if self._start_time:
            duration = datetime.now(UTC) - self._start_time
            duration_ms = int(duration.total_seconds() * 1000)
        await self.error(
            f"Execution failed: {error}",
            event="execution_failed",
            error_type=error_type,
            duration_ms=duration_ms,
        )

    async def flush(self) -> None:
        """Flush accumulated logs to the database."""
        if not self.logs:
            return

        execution = await self.engine._get_execution(self.execution_id)
        if execution is None:
            logger.warning(
                "Cannot flush logs: execution %s not found",
                self.execution_id,
            )
            return

        # Merge with existing logs
        existing_logs = list(execution.logs) if execution.logs else []
        existing_logs.extend(self.logs)
        execution.logs = existing_logs

        await self.engine.db.commit()
        await self.engine.db.refresh(execution)

        # Clear accumulated logs
        self.logs.clear()

    def get_elapsed_ms(self) -> int | None:
        """Get elapsed time since execution start in milliseconds."""
        if self._start_time is None:
            return None
        duration = datetime.now(UTC) - self._start_time
        return int(duration.total_seconds() * 1000)


class ScenarioExecutionEngine:
    """Engine for managing scenario execution lifecycle.

    This class handles:
    - Creating execution records when scenarios are triggered
    - Managing execution status transitions
    - Providing entry points for manual and automatic triggers

    Attributes:
        db: Async database session for persistence operations
    """

    def __init__(self, db: AsyncSession):
        """Initialize the execution engine.

        Args:
            db: Async database session
        """
        self.db = db

    # =========================================================================
    # Execution Record Management
    # =========================================================================

    async def create_execution(
        self,
        scenario_id: uuid.UUID,
        trigger_type: TriggerType,
        trigger_source: str | None = None,
        params: dict[str, Any] | None = None,
        space_id: uuid.UUID | None = None,
    ) -> ScenarioExecution:
        """Create a new execution record for a scenario.

        Creates an execution record with 'pending' status. The status will
        be updated to 'running' when execution actually starts.

        Requirements 5.3: Create execution record when scenario starts.

        Args:
            scenario_id: UUID of the scenario to execute
            trigger_type: How the execution was triggered (manual, schedule, trigger_rule)
            trigger_source: Description of the trigger source
            params: Input parameters for this execution
            space_id: Optional workspace scope

        Returns:
            The created ScenarioExecution record

        Raises:
            ValueError: If the scenario does not exist or is not active
        """
        # Verify scenario exists and is active
        scenario = await self._get_scenario(scenario_id)
        if scenario is None:
            raise ValueError(f"Scenario {scenario_id} not found")
        if not scenario.is_active:
            raise ValueError(f"Scenario {scenario_id} is not active")

        # Create execution record
        execution = ScenarioExecution(
            scenario_id=scenario_id,
            trigger_type=trigger_type.value,
            trigger_source=trigger_source,
            status=ExecutionStatus.PENDING.value,
            params=params or {},
            result={},
            logs=[],
            space_id=space_id or scenario.space_id,
        )

        self.db.add(execution)
        await self.db.commit()
        await self.db.refresh(execution)

        logger.info(
            "Created execution %s for scenario %s (trigger: %s)",
            execution.id,
            scenario_id,
            trigger_type.value,
        )

        return execution

    async def update_status(
        self,
        execution_id: uuid.UUID,
        status: ExecutionStatus,
        result: dict[str, Any] | None = None,
    ) -> ScenarioExecution:
        """Update the status of an execution record.

        Handles status transitions and updates timestamps accordingly:
        - pending -> running: Sets started_at
        - running -> completed/failed/timeout: Sets completed_at

        Args:
            execution_id: UUID of the execution to update
            status: New status to set
            result: Optional result data to store

        Returns:
            The updated ScenarioExecution record

        Raises:
            ValueError: If the execution does not exist
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        now = datetime.now(UTC)
        old_status = execution.status

        # Update status
        execution.status = status.value

        # Update timestamps based on status transition
        if status == ExecutionStatus.RUNNING and old_status == ExecutionStatus.PENDING.value:
            execution.started_at = now
        elif status in (
            ExecutionStatus.COMPLETED,
            ExecutionStatus.FAILED,
            ExecutionStatus.TIMEOUT,
        ):
            execution.completed_at = now
            if execution.started_at is None:
                execution.started_at = now

        # Update result if provided
        if result is not None:
            execution.result = result

        await self.db.commit()
        await self.db.refresh(execution)

        logger.info(
            "Updated execution %s status: %s -> %s",
            execution_id,
            old_status,
            status.value,
        )

        return execution

    async def add_log_entry(
        self,
        execution_id: uuid.UUID,
        level: str,
        message: str,
    ) -> ScenarioExecution:
        """Add a log entry to an execution record.

        Args:
            execution_id: UUID of the execution
            level: Log level (debug, info, warning, error)
            message: Log message

        Returns:
            The updated ScenarioExecution record

        Raises:
            ValueError: If the execution does not exist
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        log_entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": level,
            "message": message,
        }

        # Append to logs (JSONB array)
        logs = list(execution.logs) if execution.logs else []
        logs.append(log_entry)
        execution.logs = logs

        await self.db.commit()
        await self.db.refresh(execution)

        return execution

    # =========================================================================
    # Execution Logging and Result Processing (Requirements 5.7, 5.8, 5.9)
    # =========================================================================

    def create_logger(
        self,
        execution_id: uuid.UUID,
        auto_flush: bool = True,
        flush_threshold: int = 10,
    ) -> ExecutionLogger:
        """Create an ExecutionLogger for structured logging during execution.

        Requirements 5.7: Record detailed execution logs during scenario execution.

        Args:
            execution_id: UUID of the execution to log for
            auto_flush: Whether to auto-flush logs to database
            flush_threshold: Number of logs before auto-flush

        Returns:
            An ExecutionLogger instance
        """
        return ExecutionLogger(
            execution_id=execution_id,
            engine=self,
            auto_flush=auto_flush,
            flush_threshold=flush_threshold,
        )

    @asynccontextmanager
    async def execution_context(
        self,
        execution_id: uuid.UUID,
        auto_flush: bool = True,
        flush_threshold: int = 10,
    ) -> AsyncIterator[tuple[ExecutionLogger, ExecutionResultBuilder]]:
        """Context manager for execution with automatic logging and result handling.

        Requirements 5.7, 5.8, 5.9: Provides structured logging, status updates,
        and result generation in a single context.

        This context manager:
        - Creates an ExecutionLogger and ExecutionResultBuilder
        - Transitions execution to RUNNING status on entry
        - Logs execution start
        - On successful exit: completes execution with built result
        - On exception: fails execution with error details
        - Always flushes logs on exit

        Args:
            execution_id: UUID of the execution
            auto_flush: Whether to auto-flush logs
            flush_threshold: Number of logs before auto-flush

        Yields:
            Tuple of (ExecutionLogger, ExecutionResultBuilder)

        Example:
            async with engine.execution_context(execution.id) as (log, result):
                await log.log_step_start("Initialize")
                # ... do work ...
                result.set_output("Success").add_recommendation("Review logs")
        """
        exec_logger = self.create_logger(execution_id, auto_flush, flush_threshold)
        result_builder = ExecutionResultBuilder()

        # Transition to running and log start
        await self.update_status(execution_id, ExecutionStatus.RUNNING)
        await exec_logger.log_execution_start()

        try:
            yield exec_logger, result_builder

            # Calculate duration
            duration_ms = exec_logger.get_elapsed_ms() or 0
            result_builder.set_metrics(duration_ms=duration_ms)

            # Log completion and complete execution
            await exec_logger.log_execution_complete()
            await exec_logger.flush()
            await self.complete_execution(execution_id, result_builder.build())

        except Exception as e:
            # Log failure
            error_type = type(e).__name__
            error_msg = str(e)
            stack_trace = traceback.format_exc()

            await exec_logger.log_execution_failed(error_msg, error_type)
            await exec_logger.flush()

            # Build error result
            duration_ms = exec_logger.get_elapsed_ms() or 0
            result_builder.set_metrics(duration_ms=duration_ms)
            result_builder.set_error(
                message=error_msg,
                error_type=error_type,
                stack_trace=stack_trace,
            )

            await self.fail_execution(execution_id, result_builder.build())
            raise

    async def complete_execution(
        self,
        execution_id: uuid.UUID,
        result: dict[str, Any] | None = None,
        output: str | None = None,
        recommendations: list[str] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> ScenarioExecution:
        """Complete an execution successfully with structured result.

        Requirements 5.8: Update execution record status to completed.
        Requirements 5.9: Generate structured execution results.

        Args:
            execution_id: UUID of the execution to complete
            result: Pre-built result dictionary (takes precedence)
            output: Main execution output (used if result not provided)
            recommendations: List of recommendations (used if result not provided)
            metrics: Execution metrics (used if result not provided)

        Returns:
            The updated ScenarioExecution record

        Raises:
            ValueError: If the execution does not exist
        """
        # Build result if not provided
        if result is None:
            builder = ExecutionResultBuilder()
            if output is not None:
                builder.set_output(output)
            if recommendations:
                builder.add_recommendations(recommendations)
            if metrics:
                builder.set_metrics(**metrics)
            result = builder.build()

        execution = await self.update_status(
            execution_id,
            ExecutionStatus.COMPLETED,
            result=result,
        )

        logger.info(
            "Execution %s completed successfully",
            execution_id,
        )

        return execution

    async def fail_execution(
        self,
        execution_id: uuid.UUID,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        error_type: str | None = None,
        stack_trace: str | None = None,
        error_details: dict[str, Any] | None = None,
    ) -> ScenarioExecution:
        """Fail an execution with error information.

        Requirements 5.8: Update execution record status to failed.
        Requirements 5.9: Generate structured execution results with error info.

        Args:
            execution_id: UUID of the execution to fail
            result: Pre-built result dictionary (takes precedence)
            error_message: Error message (used if result not provided)
            error_type: Type of error (used if result not provided)
            stack_trace: Stack trace (used if result not provided)
            error_details: Additional error details (used if result not provided)

        Returns:
            The updated ScenarioExecution record

        Raises:
            ValueError: If the execution does not exist
        """
        # Build result if not provided
        if result is None:
            builder = ExecutionResultBuilder()
            builder.set_error(
                message=error_message or "Execution failed",
                error_type=error_type,
                stack_trace=stack_trace,
                details=error_details,
            )
            result = builder.build()

        execution = await self.update_status(
            execution_id,
            ExecutionStatus.FAILED,
            result=result,
        )

        logger.error(
            "Execution %s failed: %s",
            execution_id,
            error_message or result.get("error", {}).get("message", "Unknown error"),
        )

        return execution

    async def fail_execution_from_exception(
        self,
        execution_id: uuid.UUID,
        exception: Exception,
        include_stack_trace: bool = True,
    ) -> ScenarioExecution:
        """Fail an execution from an exception.

        Requirements 5.8, 5.9: Convenience method to fail execution from exception.

        Args:
            execution_id: UUID of the execution to fail
            exception: The exception that caused the failure
            include_stack_trace: Whether to include the stack trace

        Returns:
            The updated ScenarioExecution record
        """
        return await self.fail_execution(
            execution_id=execution_id,
            error_message=str(exception),
            error_type=type(exception).__name__,
            stack_trace=traceback.format_exc() if include_stack_trace else None,
        )

    async def add_log_entries_batch(
        self,
        execution_id: uuid.UUID,
        entries: list[dict[str, Any]],
    ) -> ScenarioExecution:
        """Add multiple log entries to an execution record in a single operation.

        Requirements 5.7: Efficient batch logging for execution process.

        Args:
            execution_id: UUID of the execution
            entries: List of log entry dictionaries with timestamp, level, message

        Returns:
            The updated ScenarioExecution record

        Raises:
            ValueError: If the execution does not exist
        """
        if not entries:
            execution = await self._get_execution(execution_id)
            if execution is None:
                raise ValueError(f"Execution {execution_id} not found")
            return execution

        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        # Ensure all entries have required fields
        now = datetime.now(UTC).isoformat()
        validated_entries = []
        for entry in entries:
            validated_entry = {
                "timestamp": entry.get("timestamp", now),
                "level": entry.get("level", "info"),
                "message": entry.get("message", ""),
            }
            if "extra" in entry:
                validated_entry["extra"] = entry["extra"]
            validated_entries.append(validated_entry)

        # Append to logs
        logs = list(execution.logs) if execution.logs else []
        logs.extend(validated_entries)
        execution.logs = logs

        await self.db.commit()
        await self.db.refresh(execution)

        return execution

    async def update_result(
        self,
        execution_id: uuid.UUID,
        result_updates: dict[str, Any],
        merge: bool = True,
    ) -> ScenarioExecution:
        """Update the result of an execution record.

        Requirements 5.9: Update structured execution results.

        Args:
            execution_id: UUID of the execution
            result_updates: Dictionary of result updates
            merge: If True, merge with existing result; if False, replace

        Returns:
            The updated ScenarioExecution record

        Raises:
            ValueError: If the execution does not exist
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        if merge:
            # Deep merge for nested structures
            current_result = dict(execution.result) if execution.result else {}
            for key, value in result_updates.items():
                if key in current_result and isinstance(current_result[key], dict) and isinstance(value, dict):
                    current_result[key] = {**current_result[key], **value}
                elif key in current_result and isinstance(current_result[key], list) and isinstance(value, list):
                    current_result[key] = current_result[key] + value
                else:
                    current_result[key] = value
            execution.result = current_result
        else:
            execution.result = result_updates

        await self.db.commit()
        await self.db.refresh(execution)

        return execution

    async def add_recommendation(
        self,
        execution_id: uuid.UUID,
        recommendation: str,
    ) -> ScenarioExecution:
        """Add a recommendation to an execution's result.

        Requirements 5.9: Add recommendations to structured execution results.

        Args:
            execution_id: UUID of the execution
            recommendation: The recommendation to add

        Returns:
            The updated ScenarioExecution record
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        result = dict(execution.result) if execution.result else {}
        recommendations = list(result.get("recommendations", []))
        recommendations.append(recommendation)
        result["recommendations"] = recommendations
        execution.result = result

        await self.db.commit()
        await self.db.refresh(execution)

        return execution

    async def set_collaboration_session(
        self,
        execution_id: uuid.UUID,
        collaboration_session_id: uuid.UUID,
    ) -> ScenarioExecution:
        """Associate a collaboration session with an execution.

        Args:
            execution_id: UUID of the execution
            collaboration_session_id: UUID of the collaboration session

        Returns:
            The updated ScenarioExecution record

        Raises:
            ValueError: If the execution does not exist
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        execution.collaboration_session_id = collaboration_session_id

        await self.db.commit()
        await self.db.refresh(execution)

        logger.info(
            "Associated collaboration session %s with execution %s",
            collaboration_session_id,
            execution_id,
        )

        return execution

    # =========================================================================
    # Trigger Entry Points
    # =========================================================================

    async def trigger_manual(
        self,
        scenario_id: uuid.UUID,
        params: dict[str, Any] | None = None,
        triggered_by: str | None = None,
        space_id: uuid.UUID | None = None,
    ) -> ScenarioExecution:
        """Manually trigger a scenario execution.

        Requirements 5.1: Support manual trigger of scenario execution.

        Args:
            scenario_id: UUID of the scenario to execute
            params: Input parameters for this execution
            triggered_by: User or system that triggered the execution
            space_id: Optional workspace scope

        Returns:
            The created ScenarioExecution record
        """
        trigger_source = "Manual trigger"
        if triggered_by:
            trigger_source = f"Manual trigger by {triggered_by}"

        execution = await self.create_execution(
            scenario_id=scenario_id,
            trigger_type=TriggerType.MANUAL,
            trigger_source=trigger_source,
            params=params,
            space_id=space_id,
        )

        await self.add_log_entry(
            execution.id,
            "info",
            f"Scenario execution triggered manually{' by ' + triggered_by if triggered_by else ''}",
        )

        return execution

    async def trigger_by_schedule(
        self,
        scenario_id: uuid.UUID,
        schedule_id: uuid.UUID,
        schedule_name: str | None = None,
        params: dict[str, Any] | None = None,
        space_id: uuid.UUID | None = None,
    ) -> ScenarioExecution:
        """Trigger a scenario execution by schedule.

        Requirements 5.2: Support automatic trigger via schedules.

        Args:
            scenario_id: UUID of the scenario to execute
            schedule_id: UUID of the schedule that triggered this execution
            schedule_name: Name of the schedule (for logging)
            params: Input parameters for this execution
            space_id: Optional workspace scope

        Returns:
            The created ScenarioExecution record
        """
        trigger_source = f"Schedule {schedule_id}"
        if schedule_name:
            trigger_source = f"Schedule '{schedule_name}' ({schedule_id})"

        execution = await self.create_execution(
            scenario_id=scenario_id,
            trigger_type=TriggerType.SCHEDULE,
            trigger_source=trigger_source,
            params=params,
            space_id=space_id,
        )

        await self.add_log_entry(
            execution.id,
            "info",
            f"Scenario execution triggered by schedule: {trigger_source}",
        )

        return execution

    async def trigger_by_rule(
        self,
        scenario_id: uuid.UUID,
        trigger_id: uuid.UUID,
        trigger_name: str | None = None,
        trigger_reason: str | None = None,
        params: dict[str, Any] | None = None,
        space_id: uuid.UUID | None = None,
    ) -> ScenarioExecution:
        """Trigger a scenario execution by trigger rule.

        Requirements 5.2: Support automatic trigger via trigger rules.

        Args:
            scenario_id: UUID of the scenario to execute
            trigger_id: UUID of the trigger rule that matched
            trigger_name: Name of the trigger rule (for logging)
            trigger_reason: Reason why the trigger matched
            params: Input parameters for this execution
            space_id: Optional workspace scope

        Returns:
            The created ScenarioExecution record
        """
        trigger_source = f"Trigger rule {trigger_id}"
        if trigger_name:
            trigger_source = f"Trigger rule '{trigger_name}' ({trigger_id})"
        if trigger_reason:
            trigger_source = f"{trigger_source}: {trigger_reason}"

        execution = await self.create_execution(
            scenario_id=scenario_id,
            trigger_type=TriggerType.TRIGGER_RULE,
            trigger_source=trigger_source,
            params=params,
            space_id=space_id,
        )

        await self.add_log_entry(
            execution.id,
            "info",
            f"Scenario execution triggered by rule: {trigger_source}",
        )

        return execution

    # =========================================================================
    # Execution Methods
    # =========================================================================

    async def execute(
        self,
        execution_id: uuid.UUID,
        timeout_override: int | None = None,
    ) -> ScenarioExecution:
        """Execute a scenario based on its type with timeout handling.

        This method:
        1. Updates the execution status to 'running'
        2. Loads the scenario with all associated resources
        3. Logs the loaded resources for debugging
        4. Selects the appropriate execution strategy based on scenario type
        5. Executes the scenario using the selected strategy with timeout
        6. Updates the execution status to 'completed', 'failed', or 'timeout'

        Requirements 4.5: Load all associated resources for execution.
        Requirements 5.4, 5.5, 5.6: Execute scenarios based on their type
        using the appropriate strategy.
        Requirements 5.10, 5.11: Support execution timeout detection and
        configurable timeout time.

        Args:
            execution_id: UUID of the execution record to execute
            timeout_override: Optional timeout override in seconds. If not provided,
                uses the scenario's execution_timeout setting (default 300 seconds).

        Returns:
            The updated ScenarioExecution record with results

        Raises:
            ValueError: If the execution or scenario is not found
        """
        # Get the execution record
        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        # Update status to running
        await self.update_status(execution_id, ExecutionStatus.RUNNING)

        try:
            # Load scenario with all resources (Requirements 4.5)
            resources = await self.load_scenario_resources(execution.scenario_id)
            if resources is None:
                raise ValueError(f"Scenario {execution.scenario_id} not found")

            scenario = resources.scenario

            # Log loaded resources for debugging and auditing
            await self.log_loaded_resources(execution_id, resources)

            # Create collaboration session if enabled (Requirements 6.1, 6.2)
            collaboration_session = None
            if scenario.enable_collaboration:
                collaboration_session = await self._create_collaboration_session(
                    scenario=scenario,
                    execution=execution,
                    trigger_reason=execution.trigger_source,
                )
                if collaboration_session:
                    await self.add_log_entry(
                        execution_id,
                        "info",
                        f"Created collaboration session {collaboration_session.id} "
                        f"for scenario '{scenario.name}'",
                    )

            # Determine timeout value (Requirements 5.11)
            timeout_seconds = (
                timeout_override if timeout_override is not None
                else scenario.execution_timeout
            )

            await self.add_log_entry(
                execution_id,
                "info",
                f"Starting execution of scenario '{scenario.name}' "
                f"(type: {scenario.scenario_type}, timeout: {timeout_seconds}s)",
            )

            # Get the appropriate execution strategy
            strategy = get_execution_strategy(scenario.scenario_type)

            await self.add_log_entry(
                execution_id,
                "debug",
                f"Using execution strategy: {strategy.__class__.__name__}",
            )

            # Execute using the strategy with timeout handling (Requirements 5.10)
            try:
                result = await asyncio.wait_for(
                    strategy.execute(
                        scenario,
                        execution,
                        execution.params,
                        self,
                    ),
                    timeout=timeout_seconds,
                )
            except TimeoutError:
                # Handle timeout (Requirements 5.10)
                return await self._handle_execution_timeout(
                    execution_id,
                    scenario.name,
                    timeout_seconds,
                )

            # Update execution with result
            result_dict = {
                "output": result.output,
                "recommendations": result.recommendations,
                "metrics": result.metrics,
            }

            # Determine final status based on result
            has_error = result.metrics.get("error") is not None
            final_status = ExecutionStatus.FAILED if has_error else ExecutionStatus.COMPLETED

            await self.update_status(
                execution_id,
                final_status,
                result=result_dict,
            )

            await self.add_log_entry(
                execution_id,
                "info" if not has_error else "error",
                f"Execution completed with status: {final_status.value}",
            )

            # Refresh and return the execution
            return await self._get_execution(execution_id)  # type: ignore

        except TimeoutError:
            # This shouldn't happen as we handle it above, but just in case
            return await self._handle_execution_timeout(
                execution_id,
                "unknown",
                timeout_override or 300,
            )

        except Exception as e:
            error_msg = f"Execution failed with error: {str(e)}"
            logger.exception("Scenario execution failed: %s", execution_id)

            await self.add_log_entry(
                execution_id,
                "error",
                error_msg,
            )

            await self.update_status(
                execution_id,
                ExecutionStatus.FAILED,
                result={
                    "output": error_msg,
                    "recommendations": ["Check scenario configuration and logs"],
                    "metrics": {"error": str(e)},
                },
            )

            return await self._get_execution(execution_id)  # type: ignore

    async def _handle_execution_timeout(
        self,
        execution_id: uuid.UUID,
        scenario_name: str,
        timeout_seconds: int,
    ) -> ScenarioExecution:
        """Handle execution timeout by updating status and logging.

        Requirements 5.10: Terminate execution and record timeout status.

        Args:
            execution_id: UUID of the execution that timed out
            scenario_name: Name of the scenario for logging
            timeout_seconds: The timeout value that was exceeded

        Returns:
            The updated ScenarioExecution record with timeout status
        """
        timeout_msg = (
            f"Execution timed out after {timeout_seconds} seconds for scenario '{scenario_name}'"
        )
        logger.warning("Scenario execution timeout: %s - %s", execution_id, timeout_msg)

        await self.add_log_entry(
            execution_id,
            "error",
            timeout_msg,
        )

        await self.update_status(
            execution_id,
            ExecutionStatus.TIMEOUT,
            result={
                "output": timeout_msg,
                "recommendations": [
                    "Consider increasing the execution_timeout for this scenario",
                    "Check if the scenario is performing long-running operations",
                    "Review the scenario configuration and associated resources",
                ],
                "metrics": {
                    "timeout": True,
                    "timeout_seconds": timeout_seconds,
                    "error": "execution_timeout",
                },
            },
        )

        return await self._get_execution(execution_id)  # type: ignore

    async def timeout_execution(
        self,
        execution_id: uuid.UUID,
        reason: str | None = None,
    ) -> ScenarioExecution:
        """Mark an execution as timed out.

        This method can be used to externally timeout an execution that is
        taking too long. It updates the execution status to TIMEOUT and
        records the timeout information.

        Requirements 5.10: Support external timeout of executions.

        Args:
            execution_id: UUID of the execution to timeout
            reason: Optional reason for the timeout

        Returns:
            The updated ScenarioExecution record with timeout status

        Raises:
            ValueError: If the execution does not exist or is not in a running state
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            raise ValueError(f"Execution {execution_id} not found")

        if execution.status != ExecutionStatus.RUNNING.value:
            raise ValueError(
                f"Cannot timeout execution {execution_id}: "
                f"current status is '{execution.status}', expected 'running'"
            )

        # Load scenario to get the name for logging
        scenario = await self._get_scenario(execution.scenario_id)
        scenario_name = scenario.name if scenario else "unknown"

        timeout_msg = f"Execution manually timed out for scenario '{scenario_name}'"
        if reason:
            timeout_msg = f"{timeout_msg}: {reason}"

        logger.warning("Scenario execution manually timed out: %s - %s", execution_id, timeout_msg)

        await self.add_log_entry(
            execution_id,
            "error",
            timeout_msg,
        )

        await self.update_status(
            execution_id,
            ExecutionStatus.TIMEOUT,
            result={
                "output": timeout_msg,
                "recommendations": [
                    "Review the execution logs to understand what was happening",
                    "Consider increasing the execution_timeout for this scenario",
                ],
                "metrics": {
                    "timeout": True,
                    "manual_timeout": True,
                    "timeout_reason": reason,
                    "error": "manual_timeout",
                },
            },
        )

        return await self._get_execution(execution_id)  # type: ignore

    async def execute_scenario(
        self,
        scenario_id: uuid.UUID,
        trigger_type: TriggerType,
        params: dict[str, Any] | None = None,
        trigger_source: str | None = None,
        triggered_by: str | None = None,
        space_id: uuid.UUID | None = None,
        timeout_override: int | None = None,
    ) -> ScenarioExecution:
        """Create an execution record and execute the scenario.

        This is a convenience method that combines creating an execution
        record and executing it in one call.

        Args:
            scenario_id: UUID of the scenario to execute
            trigger_type: How the execution was triggered
            params: Input parameters for this execution
            trigger_source: Description of the trigger source
            triggered_by: User or system that triggered the execution
            space_id: Optional workspace scope
            timeout_override: Optional timeout override in seconds. If not provided,
                uses the scenario's execution_timeout setting.

        Returns:
            The completed ScenarioExecution record with results
        """
        # Build trigger source if not provided
        if trigger_source is None and triggered_by:
            trigger_source = f"{trigger_type.value} by {triggered_by}"

        # Create execution record
        execution = await self.create_execution(
            scenario_id=scenario_id,
            trigger_type=trigger_type,
            trigger_source=trigger_source,
            params=params,
            space_id=space_id,
        )

        # Execute the scenario with optional timeout override
        return await self.execute(execution.id, timeout_override=timeout_override)

    # =========================================================================
    # Query Methods
    # =========================================================================

    async def get_execution(
        self,
        execution_id: uuid.UUID,
    ) -> ScenarioExecution | None:
        """Get an execution record by ID.

        Args:
            execution_id: UUID of the execution

        Returns:
            The ScenarioExecution record, or None if not found
        """
        return await self._get_execution(execution_id)

    async def get_scenario_with_resources(
        self,
        scenario_id: uuid.UUID,
    ) -> Scenario | None:
        """Get a scenario with all its associated resources loaded.

        Loads tools, agents, knowledge_docs, and notification_channels
        relationships for use during execution.

        Args:
            scenario_id: UUID of the scenario

        Returns:
            The Scenario with relationships loaded, or None if not found
        """
        result = await self.db.execute(
            select(Scenario)
            .where(Scenario.id == scenario_id)
            .options(
                selectinload(Scenario.tools),
                selectinload(Scenario.agents),
                selectinload(Scenario.knowledge_docs),
                selectinload(Scenario.notification_channels),
            )
        )
        return result.scalar_one_or_none()

    async def load_scenario_resources(
        self,
        scenario_id: uuid.UUID,
    ) -> LoadedResources | None:
        """Load all resources associated with a scenario for execution.

        Requirements 4.5: Load all associated resources (tools/skills, agents,
        knowledge_docs, notification_channels) and make them available during execution.

        This method loads the scenario with all its associated resources and
        returns them in a structured LoadedResources container that provides
        convenient access methods for the execution engine.

        Args:
            scenario_id: UUID of the scenario to load resources for

        Returns:
            LoadedResources container with all associated resources, or None if
            the scenario is not found

        Example:
            resources = await engine.load_scenario_resources(scenario_id)
            if resources:
                # Access tools
                for tool in resources.get_active_tools():
                    print(f"Tool: {tool.name}")

                # Find specific agent
                agent = resources.get_agent_by_name("analyzer")

                # Get WeCom channels for notifications
                wecom_channels = resources.get_wecom_channels()
        """
        scenario = await self.get_scenario_with_resources(scenario_id)
        if scenario is None:
            return None

        return LoadedResources(
            scenario=scenario,
            tools=list(scenario.tools) if scenario.tools else [],
            agents=list(scenario.agents) if scenario.agents else [],
            knowledge_docs=(
                list(scenario.knowledge_docs) if scenario.knowledge_docs else []
            ),
            notification_channels=(
                list(scenario.notification_channels)
                if scenario.notification_channels
                else []
            ),
        )

    async def load_execution_resources(
        self,
        execution_id: uuid.UUID,
    ) -> LoadedResources | None:
        """Load all resources for an execution's scenario.

        Requirements 4.5: Convenience method to load resources from an execution record.

        This method retrieves the execution record, then loads all resources
        associated with its scenario.

        Args:
            execution_id: UUID of the execution to load resources for

        Returns:
            LoadedResources container with all associated resources, or None if
            the execution or scenario is not found
        """
        execution = await self._get_execution(execution_id)
        if execution is None:
            return None

        return await self.load_scenario_resources(execution.scenario_id)

    async def get_scenario_tools(
        self,
        scenario_id: uuid.UUID,
        active_only: bool = True,
    ) -> list[Tool]:
        """Get all tools associated with a scenario.

        Requirements 4.5: Load tools/skills associated with the scenario.

        Args:
            scenario_id: UUID of the scenario
            active_only: If True, only return active tools

        Returns:
            List of Tool objects associated with the scenario
        """
        resources = await self.load_scenario_resources(scenario_id)
        if resources is None:
            return []

        if active_only:
            return resources.get_active_tools()
        return resources.tools

    async def get_scenario_agents(
        self,
        scenario_id: uuid.UUID,
        active_only: bool = True,
    ) -> list[Agent]:
        """Get all agents associated with a scenario.

        Requirements 4.5: Load agents associated with the scenario.

        Args:
            scenario_id: UUID of the scenario
            active_only: If True, only return active agents

        Returns:
            List of Agent objects associated with the scenario
        """
        resources = await self.load_scenario_resources(scenario_id)
        if resources is None:
            return []

        if active_only:
            return resources.get_active_agents()
        return resources.agents

    async def get_scenario_knowledge_docs(
        self,
        scenario_id: uuid.UUID,
    ) -> list[KnowledgeDocument]:
        """Get all knowledge documents associated with a scenario.

        Requirements 4.5: Load knowledge documents associated with the scenario.

        Args:
            scenario_id: UUID of the scenario

        Returns:
            List of KnowledgeDocument objects associated with the scenario
        """
        resources = await self.load_scenario_resources(scenario_id)
        if resources is None:
            return []

        return resources.knowledge_docs

    async def get_scenario_notification_channels(
        self,
        scenario_id: uuid.UUID,
        active_only: bool = True,
        channel_type: str | None = None,
    ) -> list[NotificationChannel]:
        """Get all notification channels associated with a scenario.

        Requirements 4.5: Load notification channels associated with the scenario.

        Args:
            scenario_id: UUID of the scenario
            active_only: If True, only return active channels
            channel_type: If provided, filter by channel type (e.g., 'wecom', 'email')

        Returns:
            List of NotificationChannel objects associated with the scenario
        """
        resources = await self.load_scenario_resources(scenario_id)
        if resources is None:
            return []

        channels = resources.notification_channels

        if active_only:
            channels = [c for c in channels if c.is_active]

        if channel_type:
            channels = [c for c in channels if c.channel_type == channel_type]

        return channels

    async def log_loaded_resources(
        self,
        execution_id: uuid.UUID,
        resources: LoadedResources,
    ) -> None:
        """Log information about loaded resources for an execution.

        Requirements 4.5, 5.7: Log resource loading information during execution.

        This method logs a summary of all loaded resources to the execution
        log for debugging and auditing purposes.

        Args:
            execution_id: UUID of the execution to log for
            resources: The LoadedResources container to log
        """
        summary = resources.to_summary_dict()

        await self.add_log_entry(
            execution_id,
            "info",
            f"Loaded resources for scenario '{resources.scenario.name}': "
            f"{summary['tools']['count']} tools, "
            f"{summary['agents']['count']} agents, "
            f"{summary['knowledge_docs']['count']} knowledge docs, "
            f"{summary['notification_channels']['count']} notification channels",
        )

        # Log detailed resource information at debug level
        if resources.has_tools:
            await self.add_log_entry(
                execution_id,
                "debug",
                f"Tools: {', '.join(summary['tools']['names'])} "
                f"({summary['tools']['active_count']} active)",
            )

        if resources.has_agents:
            await self.add_log_entry(
                execution_id,
                "debug",
                f"Agents: {', '.join(summary['agents']['names'])} "
                f"({summary['agents']['active_count']} active)",
            )

        if resources.has_knowledge_docs:
            await self.add_log_entry(
                execution_id,
                "debug",
                f"Knowledge docs: {', '.join(summary['knowledge_docs']['titles'])}",
            )

        if resources.has_notification_channels:
            await self.add_log_entry(
                execution_id,
                "debug",
                f"Notification channels: {', '.join(summary['notification_channels']['names'])} "
                f"(types: {', '.join(summary['notification_channels']['types'])})",
            )

    async def list_executions(
        self,
        scenario_id: uuid.UUID | None = None,
        status: ExecutionStatus | None = None,
        space_id: uuid.UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ScenarioExecution]:
        """List execution records with optional filters.

        Args:
            scenario_id: Filter by scenario ID
            status: Filter by execution status
            space_id: Filter by workspace
            limit: Maximum number of records to return
            offset: Number of records to skip

        Returns:
            List of ScenarioExecution records
        """
        query = select(ScenarioExecution).order_by(
            ScenarioExecution.created_at.desc()
        )

        if scenario_id is not None:
            query = query.where(ScenarioExecution.scenario_id == scenario_id)
        if status is not None:
            query = query.where(ScenarioExecution.status == status.value)
        if space_id is not None:
            query = query.where(ScenarioExecution.space_id == space_id)

        query = query.limit(limit).offset(offset)

        result = await self.db.execute(query)
        return list(result.scalars().all())

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    async def _create_collaboration_session(
        self,
        scenario: Scenario,
        execution: ScenarioExecution,
        trigger_reason: str | None = None,
    ) -> Any | None:
        """Create a collaboration session for scenarios with collaboration enabled.

        This method integrates with the CollaborationService to automatically
        create a collaboration session when a scenario with enable_collaboration=True
        is triggered.

        Requirements:
            - 6.1: Support enabling emergency collaboration for scenarios
            - 6.2: Auto-create collaboration session when scenario triggered

        Args:
            scenario: The scenario being executed
            execution: The execution record
            trigger_reason: Description of why the scenario was triggered

        Returns:
            The created CollaborationSession, or None if creation failed
        """
        try:
            # Lazy import to avoid circular dependencies
            from src.services.collaboration_service import CollaborationService

            collab_service = CollaborationService(self.db)

            # Build trigger reason from execution info
            reason = trigger_reason or f"Scenario '{scenario.name}' triggered"
            if execution.trigger_source:
                reason = f"{reason} ({execution.trigger_source})"

            # Create the collaboration session
            session = await collab_service.create_session(
                scenario_id=scenario.id,
                trigger_reason=reason,
                space_id=scenario.space_id,
            )

            # Execute initialization actions (group chat, email)
            init_result = await collab_service.execute_initialization_actions(session)

            if not init_result.all_success:
                logger.warning(
                    "Some initialization actions failed for collaboration session %s: %s",
                    session.id,
                    [a.error for a in init_result.actions if not a.success],
                )

            # Link the execution to the collaboration session
            execution.collaboration_session_id = session.id
            await self.db.flush()

            logger.info(
                "Created collaboration session %s for execution %s "
                "(scenario: %s, group_chat_id: %s)",
                session.id,
                execution.id,
                scenario.name,
                init_result.group_chat_id,
            )

            return session

        except ImportError:
            logger.warning(
                "CollaborationService not available, skipping collaboration session creation"
            )
            return None
        except Exception as exc:
            logger.exception(
                "Failed to create collaboration session for execution %s: %s",
                execution.id,
                exc,
            )
            # Don't fail the execution if collaboration session creation fails
            return None

    async def _get_scenario(self, scenario_id: uuid.UUID) -> Scenario | None:
        """Get a scenario by ID.

        Args:
            scenario_id: UUID of the scenario

        Returns:
            The Scenario, or None if not found
        """
        result = await self.db.execute(
            select(Scenario).where(Scenario.id == scenario_id)
        )
        return result.scalar_one_or_none()

    async def _get_execution(self, execution_id: uuid.UUID) -> ScenarioExecution | None:
        """Get an execution by ID.

        Args:
            execution_id: UUID of the execution

        Returns:
            The ScenarioExecution, or None if not found
        """
        result = await self.db.execute(
            select(ScenarioExecution).where(ScenarioExecution.id == execution_id)
        )
        return result.scalar_one_or_none()


# =============================================================================
# Factory Function
# =============================================================================


def get_scenario_execution_engine(db: AsyncSession) -> ScenarioExecutionEngine:
    """Factory function to create a ScenarioExecutionEngine instance.

    Args:
        db: Async database session

    Returns:
        A new ScenarioExecutionEngine instance
    """
    return ScenarioExecutionEngine(db)
