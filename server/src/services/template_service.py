"""Scenario template service — provides built-in scenario templates.

This module defines the built-in scenario templates for common operations:
- fault_isolation: 故障定界模板
- health_inspection: 健康巡检模板
- capacity_prediction: 容量预测模板
- alert_analysis: 告警分析模板

Requirements 2.1, 2.4, 2.5: THE Scenario_Template_System SHALL provide built-in
templates with default parameter schemas and recommended tools/agents.

Requirements 2.2, 2.3, 2.6: THE Scenario_Template_System SHALL support template
application with auto-fill, user customization, and template source tracking.
"""

import logging
from typing import Any

from src.schemas.scenario import (
    CollaborationConfig,
    RecommendedAgent,
    RecommendedTool,
    ScenarioCreate,
    ScenarioFromTemplateCreate,
    ScenarioTemplateResponse,
    ScenarioType,
    TemplateId,
    TemplateParamSchema,
)

logger = logging.getLogger(__name__)


class TemplateService:
    """Service for managing scenario templates.

    Provides access to built-in scenario templates with default configurations,
    parameter schemas, and recommended resources.

    Requirements:
        - 2.1: Provide built-in templates (fault_isolation, health_inspection,
               capacity_prediction, alert_analysis)
        - 2.4: Provide default parameter schema definitions for each template
        - 2.5: Associate recommended tools and agents with each template
    """

    def __init__(self) -> None:
        """Initialize the template service with built-in templates."""
        self._templates: dict[str, ScenarioTemplateResponse] = {}
        self._initialize_builtin_templates()

    def _initialize_builtin_templates(self) -> None:
        """Initialize all built-in templates."""
        self._templates[TemplateId.FAULT_ISOLATION.value] = self._create_fault_isolation_template()
        self._templates[TemplateId.HEALTH_INSPECTION.value] = self._create_health_inspection_template()
        self._templates[TemplateId.CAPACITY_PREDICTION.value] = self._create_capacity_prediction_template()
        self._templates[TemplateId.ALERT_ANALYSIS.value] = self._create_alert_analysis_template()
        logger.info("Initialized %d built-in scenario templates", len(self._templates))

    def _create_fault_isolation_template(self) -> ScenarioTemplateResponse:
        """Create the fault isolation template.

        故障定界模板：用于快速定位故障根因，支持多维度分析。
        """
        return ScenarioTemplateResponse(
            template_id=TemplateId.FAULT_ISOLATION.value,
            name="故障定界",
            description=(
                "故障定界场景模板，用于快速定位故障根因。支持多维度分析，包括日志分析、"
                "指标关联、拓扑追踪等，帮助运维人员快速定位问题源头。"
            ),
            scenario_type=ScenarioType.HYBRID,
            default_trigger_command="/fault-isolate",
            default_nl_prompt=(
                "分析当前告警和相关指标，定位故障根因。请检查以下维度：\n"
                "1. 告警关联分析：查找相关联的告警事件\n"
                "2. 日志分析：检查错误日志和异常模式\n"
                "3. 指标分析：分析关键性能指标的异常变化\n"
                "4. 拓扑分析：追踪服务调用链路\n"
                "5. 变更关联：检查近期变更记录"
            ),
            default_params_schema=[
                TemplateParamSchema(
                    name="alert_id",
                    type="string",
                    description="触发故障定界的告警ID",
                    required=False,
                ),
                TemplateParamSchema(
                    name="target_host",
                    type="string",
                    description="目标主机或服务名称",
                    required=False,
                ),
                TemplateParamSchema(
                    name="time_range",
                    type="string",
                    description="分析时间范围，如 '1h', '30m', '24h'",
                    required=False,
                    default="1h",
                ),
                TemplateParamSchema(
                    name="analysis_depth",
                    type="string",
                    description="分析深度",
                    required=False,
                    default="standard",
                    enum=["quick", "standard", "deep"],
                ),
            ],
            recommended_tools=[
                RecommendedTool(
                    tool_name="log_search",
                    description="日志搜索工具，用于查询和分析日志",
                    category="observability",
                ),
                RecommendedTool(
                    tool_name="metric_query",
                    description="指标查询工具，用于查询监控指标",
                    category="observability",
                ),
                RecommendedTool(
                    tool_name="topology_trace",
                    description="拓扑追踪工具，用于分析服务调用链",
                    category="observability",
                ),
                RecommendedTool(
                    tool_name="alert_correlate",
                    description="告警关联工具，用于分析告警之间的关联关系",
                    category="alerting",
                ),
                RecommendedTool(
                    tool_name="change_history",
                    description="变更历史查询工具，用于查询近期变更记录",
                    category="cmdb",
                ),
            ],
            recommended_agents=[
                RecommendedAgent(
                    agent_name="fault_analysis_agent",
                    description="故障分析智能体，专注于故障根因分析",
                    agent_type="specialist",
                ),
                RecommendedAgent(
                    agent_name="log_analysis_agent",
                    description="日志分析智能体，专注于日志模式识别",
                    agent_type="specialist",
                ),
            ],
            default_execution_timeout=600,
            default_collaboration_config=CollaborationConfig(
                auto_create_group=True,
                group_name_template="[故障定界] {scenario_name} - {timestamp}",
                send_email=True,
            ),
        )

    def _create_health_inspection_template(self) -> ScenarioTemplateResponse:
        """Create the health inspection template.

        健康巡检模板：用于定期检查系统健康状态。
        """
        return ScenarioTemplateResponse(
            template_id=TemplateId.HEALTH_INSPECTION.value,
            name="健康巡检",
            description=(
                "健康巡检场景模板，用于定期检查系统健康状态。支持多维度健康检查，"
                "包括资源使用率、服务可用性、性能指标等，生成健康报告和优化建议。"
            ),
            scenario_type=ScenarioType.HYBRID,
            default_trigger_command="/health-check",
            default_nl_prompt=(
                "执行系统健康巡检，检查以下维度：\n"
                "1. 资源使用率：CPU、内存、磁盘、网络\n"
                "2. 服务可用性：关键服务的运行状态\n"
                "3. 性能指标：响应时间、吞吐量、错误率\n"
                "4. 安全状态：证书有效期、安全补丁\n"
                "5. 配置合规：配置项是否符合最佳实践\n"
                "生成健康报告并提供优化建议。"
            ),
            default_params_schema=[
                TemplateParamSchema(
                    name="scope",
                    type="string",
                    description="巡检范围",
                    required=False,
                    default="all",
                    enum=["all", "infrastructure", "application", "database", "network"],
                ),
                TemplateParamSchema(
                    name="target_hosts",
                    type="array",
                    description="目标主机列表，为空则检查所有主机",
                    required=False,
                ),
                TemplateParamSchema(
                    name="check_items",
                    type="array",
                    description="检查项列表，为空则执行所有检查",
                    required=False,
                ),
                TemplateParamSchema(
                    name="report_format",
                    type="string",
                    description="报告格式",
                    required=False,
                    default="summary",
                    enum=["summary", "detailed", "executive"],
                ),
            ],
            recommended_tools=[
                RecommendedTool(
                    tool_name="resource_monitor",
                    description="资源监控工具，用于检查资源使用率",
                    category="monitoring",
                ),
                RecommendedTool(
                    tool_name="service_health",
                    description="服务健康检查工具，用于检查服务可用性",
                    category="monitoring",
                ),
                RecommendedTool(
                    tool_name="performance_check",
                    description="性能检查工具，用于分析性能指标",
                    category="monitoring",
                ),
                RecommendedTool(
                    tool_name="security_scan",
                    description="安全扫描工具，用于检查安全状态",
                    category="security",
                ),
                RecommendedTool(
                    tool_name="config_audit",
                    description="配置审计工具，用于检查配置合规性",
                    category="compliance",
                ),
            ],
            recommended_agents=[
                RecommendedAgent(
                    agent_name="health_check_agent",
                    description="健康检查智能体，专注于系统健康评估",
                    agent_type="specialist",
                ),
                RecommendedAgent(
                    agent_name="report_generator_agent",
                    description="报告生成智能体，专注于生成结构化报告",
                    agent_type="specialist",
                ),
            ],
            default_execution_timeout=900,
            default_collaboration_config=CollaborationConfig(
                auto_create_group=False,
                send_email=True,
            ),
        )

    def _create_capacity_prediction_template(self) -> ScenarioTemplateResponse:
        """Create the capacity prediction template.

        容量预测模板：用于预测资源容量需求。
        """
        return ScenarioTemplateResponse(
            template_id=TemplateId.CAPACITY_PREDICTION.value,
            name="容量预测",
            description=(
                "容量预测场景模板，用于预测资源容量需求。基于历史数据和趋势分析，"
                "预测未来资源使用情况，提供扩容建议和成本优化方案。"
            ),
            scenario_type=ScenarioType.HYBRID,
            default_trigger_command="/capacity-predict",
            default_nl_prompt=(
                "执行容量预测分析：\n"
                "1. 收集历史资源使用数据\n"
                "2. 分析使用趋势和周期性模式\n"
                "3. 预测未来资源需求\n"
                "4. 识别潜在的容量瓶颈\n"
                "5. 提供扩容建议和成本优化方案"
            ),
            default_params_schema=[
                TemplateParamSchema(
                    name="resource_type",
                    type="string",
                    description="资源类型",
                    required=False,
                    default="all",
                    enum=["all", "cpu", "memory", "disk", "network", "database"],
                ),
                TemplateParamSchema(
                    name="prediction_horizon",
                    type="string",
                    description="预测时间范围",
                    required=False,
                    default="30d",
                    enum=["7d", "14d", "30d", "90d"],
                ),
                TemplateParamSchema(
                    name="target_services",
                    type="array",
                    description="目标服务列表，为空则分析所有服务",
                    required=False,
                ),
                TemplateParamSchema(
                    name="confidence_level",
                    type="number",
                    description="预测置信度阈值 (0-1)",
                    required=False,
                    default=0.8,
                ),
            ],
            recommended_tools=[
                RecommendedTool(
                    tool_name="metric_history",
                    description="历史指标查询工具，用于获取历史数据",
                    category="observability",
                ),
                RecommendedTool(
                    tool_name="trend_analysis",
                    description="趋势分析工具，用于分析数据趋势",
                    category="analytics",
                ),
                RecommendedTool(
                    tool_name="capacity_model",
                    description="容量模型工具，用于预测资源需求",
                    category="analytics",
                ),
                RecommendedTool(
                    tool_name="cost_calculator",
                    description="成本计算工具，用于估算扩容成本",
                    category="finops",
                ),
            ],
            recommended_agents=[
                RecommendedAgent(
                    agent_name="capacity_planning_agent",
                    description="容量规划智能体，专注于容量预测和规划",
                    agent_type="specialist",
                ),
                RecommendedAgent(
                    agent_name="cost_optimization_agent",
                    description="成本优化智能体，专注于资源成本优化",
                    agent_type="specialist",
                ),
            ],
            default_execution_timeout=1200,
            default_collaboration_config=CollaborationConfig(
                auto_create_group=False,
                send_email=True,
            ),
        )

    def _create_alert_analysis_template(self) -> ScenarioTemplateResponse:
        """Create the alert analysis template.

        告警分析模板：用于分析告警模式和趋势。
        """
        return ScenarioTemplateResponse(
            template_id=TemplateId.ALERT_ANALYSIS.value,
            name="告警分析",
            description=(
                "告警分析场景模板，用于分析告警模式和趋势。支持告警聚合、关联分析、"
                "根因推断等功能，帮助运维人员理解告警全貌并快速响应。"
            ),
            scenario_type=ScenarioType.HYBRID,
            default_trigger_command="/alert-analyze",
            default_nl_prompt=(
                "执行告警分析：\n"
                "1. 告警聚合：按类型、来源、时间聚合告警\n"
                "2. 关联分析：分析告警之间的关联关系\n"
                "3. 趋势分析：分析告警数量和类型的变化趋势\n"
                "4. 根因推断：推断告警的可能根因\n"
                "5. 优先级排序：根据影响范围和紧急程度排序\n"
                "6. 处理建议：提供告警处理建议"
            ),
            default_params_schema=[
                TemplateParamSchema(
                    name="time_range",
                    type="string",
                    description="分析时间范围",
                    required=False,
                    default="24h",
                    enum=["1h", "6h", "12h", "24h", "7d"],
                ),
                TemplateParamSchema(
                    name="severity_filter",
                    type="array",
                    description="告警级别过滤",
                    required=False,
                    enum=["critical", "warning", "info"],
                ),
                TemplateParamSchema(
                    name="source_filter",
                    type="array",
                    description="告警来源过滤",
                    required=False,
                ),
                TemplateParamSchema(
                    name="analysis_mode",
                    type="string",
                    description="分析模式",
                    required=False,
                    default="comprehensive",
                    enum=["quick", "comprehensive", "deep"],
                ),
                TemplateParamSchema(
                    name="include_resolved",
                    type="boolean",
                    description="是否包含已解决的告警",
                    required=False,
                    default=False,
                ),
            ],
            recommended_tools=[
                RecommendedTool(
                    tool_name="alert_query",
                    description="告警查询工具，用于查询告警数据",
                    category="alerting",
                ),
                RecommendedTool(
                    tool_name="alert_correlate",
                    description="告警关联工具，用于分析告警关联关系",
                    category="alerting",
                ),
                RecommendedTool(
                    tool_name="alert_aggregate",
                    description="告警聚合工具，用于聚合相似告警",
                    category="alerting",
                ),
                RecommendedTool(
                    tool_name="root_cause_inference",
                    description="根因推断工具，用于推断告警根因",
                    category="analytics",
                ),
                RecommendedTool(
                    tool_name="impact_analysis",
                    description="影响分析工具，用于分析告警影响范围",
                    category="analytics",
                ),
            ],
            recommended_agents=[
                RecommendedAgent(
                    agent_name="alert_analysis_agent",
                    description="告警分析智能体，专注于告警模式分析",
                    agent_type="specialist",
                ),
                RecommendedAgent(
                    agent_name="incident_response_agent",
                    description="事件响应智能体，专注于告警处理建议",
                    agent_type="specialist",
                ),
            ],
            default_execution_timeout=300,
            default_collaboration_config=CollaborationConfig(
                auto_create_group=True,
                group_name_template="[告警分析] {scenario_name} - {timestamp}",
                send_email=True,
            ),
        )

    def get_template(self, template_id: str) -> ScenarioTemplateResponse | None:
        """Get a template by its ID.

        Args:
            template_id: The template identifier (e.g., 'fault_isolation')

        Returns:
            The template response if found, None otherwise.

        Requirements:
            - 2.1: Provide access to built-in templates
        """
        template = self._templates.get(template_id)
        if template is None:
            logger.warning("Template not found: %s", template_id)
        return template

    def list_templates(self) -> list[ScenarioTemplateResponse]:
        """List all available templates.

        Returns:
            List of all available scenario templates.

        Requirements:
            - 2.1: Provide access to built-in templates
        """
        return list(self._templates.values())

    def get_template_ids(self) -> list[str]:
        """Get all available template IDs.

        Returns:
            List of template identifiers.
        """
        return list(self._templates.keys())

    def template_exists(self, template_id: str) -> bool:
        """Check if a template exists.

        Args:
            template_id: The template identifier to check.

        Returns:
            True if the template exists, False otherwise.
        """
        return template_id in self._templates

    def get_template_config(self, template_id: str) -> dict[str, Any] | None:
        """Get template configuration as a dictionary for scenario creation.

        This method returns the template configuration in a format suitable
        for creating a new scenario based on the template.

        Args:
            template_id: The template identifier.

        Returns:
            Dictionary with template configuration, or None if not found.

        Requirements:
            - 2.2: Auto-fill template predefined configuration items
        """
        template = self.get_template(template_id)
        if template is None:
            return None

        # Convert params_schema to dict format
        params_schema = {}
        for param in template.default_params_schema:
            param_def: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
                "required": param.required,
            }
            if param.default is not None:
                param_def["default"] = param.default
            if param.enum is not None:
                param_def["enum"] = param.enum
            params_schema[param.name] = param_def

        # Build collaboration config dict
        collab_config = {}
        if template.default_collaboration_config:
            collab_config = template.default_collaboration_config.model_dump()

        return {
            "template_id": template.template_id,
            "name": template.name,
            "description": template.description,
            "scenario_type": template.scenario_type.value,
            "trigger_command": template.default_trigger_command,
            "nl_prompt": template.default_nl_prompt,
            "params_schema": params_schema,
            "execution_timeout": template.default_execution_timeout,
            "collaboration_config": collab_config,
            "recommended_tools": [t.model_dump() for t in template.recommended_tools],
            "recommended_agents": [a.model_dump() for a in template.recommended_agents],
        }

    def apply_template(
        self,
        request: ScenarioFromTemplateCreate,
    ) -> ScenarioCreate:
        """Apply a template to create a ScenarioCreate object.

        This method takes a template ID and user customizations, merges them
        with template defaults, and returns a ScenarioCreate object ready
        for scenario creation.

        Args:
            request: The request containing template_id and user customizations.

        Returns:
            A ScenarioCreate object with merged template and user values.

        Raises:
            ValueError: If the template_id is invalid or template not found.

        Requirements:
            - 2.2: Auto-fill template predefined configuration items
            - 2.3: Allow user customization on top of template
            - 2.6: Record scenario's template source
        """
        # Validate and get template
        template = self.get_template(request.template_id)
        if template is None:
            valid_templates = ", ".join(self.get_template_ids())
            raise ValueError(
                f"Template '{request.template_id}' not found. "
                f"Valid templates: {valid_templates}"
            )

        logger.info(
            "Applying template '%s' to create scenario '%s'",
            request.template_id,
            request.name,
        )

        # Build params_schema: merge template defaults with user customizations
        params_schema = self._build_params_schema(template, request.params_schema)

        # Determine trigger_command: user override or template default
        trigger_command = self._resolve_trigger_command(template, request.trigger_command)

        # Determine nl_prompt: user override or template default
        nl_prompt = self._resolve_nl_prompt(template, request.nl_prompt)

        # Determine description: user override or template default
        description = request.description if request.description else template.description

        # Determine execution_timeout: user override or template default
        execution_timeout = (
            request.execution_timeout
            if request.execution_timeout is not None
            else template.default_execution_timeout
        )

        # Determine collaboration config: user override or template default
        collaboration_config = self._resolve_collaboration_config(template, request)

        # Determine enable_collaboration: user override or based on template config
        enable_collaboration = self._resolve_enable_collaboration(template, request)

        # Build the ScenarioCreate object
        scenario_create = ScenarioCreate(
            name=request.name,
            description=description,
            scenario_type=template.scenario_type,
            trigger_command=trigger_command,
            nl_prompt=nl_prompt,
            params_schema=params_schema,
            execution_timeout=execution_timeout,
            is_active=request.is_active,
            enable_collaboration=enable_collaboration,
            collaboration_config=collaboration_config,
            template_id=request.template_id,  # Record template source (Req 2.6)
            tool_ids=request.tool_ids or [],
            agent_ids=request.agent_ids or [],
            knowledge_doc_ids=request.knowledge_doc_ids,
            channel_ids=request.channel_ids,
            space_id=request.space_id,
        )

        logger.info(
            "Successfully applied template '%s' for scenario '%s' "
            "(type=%s, enable_collaboration=%s)",
            request.template_id,
            request.name,
            template.scenario_type.value,
            enable_collaboration,
        )

        return scenario_create

    def _build_params_schema(
        self,
        template: ScenarioTemplateResponse,
        user_params: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Build params_schema by merging template defaults with user customizations.

        Args:
            template: The template containing default params schema.
            user_params: User-provided params schema overrides.

        Returns:
            Merged params_schema dictionary.

        Requirements:
            - 2.2: Auto-fill template predefined configuration items
            - 2.3: Allow user customization on top of template
        """
        # Start with template defaults
        params_schema: dict[str, Any] = {}
        for param in template.default_params_schema:
            param_def: dict[str, Any] = {
                "type": param.type,
                "description": param.description,
                "required": param.required,
            }
            if param.default is not None:
                param_def["default"] = param.default
            if param.enum is not None:
                param_def["enum"] = param.enum
            params_schema[param.name] = param_def

        # Merge user customizations if provided
        if user_params:
            for key, value in user_params.items():
                if key in params_schema:
                    # Update existing param with user values
                    if isinstance(value, dict):
                        params_schema[key].update(value)
                    else:
                        # If user provides a simple value, treat it as default override
                        params_schema[key]["default"] = value
                else:
                    # Add new user-defined param
                    params_schema[key] = value

        return params_schema

    def _resolve_trigger_command(
        self,
        template: ScenarioTemplateResponse,
        user_command: str | None,
    ) -> str | None:
        """Resolve trigger_command from user override or template default.

        Args:
            template: The template containing default trigger command.
            user_command: User-provided trigger command override.

        Returns:
            The resolved trigger command.

        Requirements:
            - 2.2: Auto-fill template predefined configuration items
            - 2.3: Allow user customization on top of template
        """
        if user_command is not None:
            # Validate user command format
            if not user_command.startswith("/"):
                raise ValueError("trigger_command must start with '/'")
            return user_command
        return template.default_trigger_command

    def _resolve_nl_prompt(
        self,
        template: ScenarioTemplateResponse,
        user_prompt: str | None,
    ) -> str | None:
        """Resolve nl_prompt from user override or template default.

        Args:
            template: The template containing default NL prompt.
            user_prompt: User-provided NL prompt override.

        Returns:
            The resolved NL prompt.

        Requirements:
            - 2.2: Auto-fill template predefined configuration items
            - 2.3: Allow user customization on top of template
        """
        if user_prompt is not None:
            return user_prompt
        return template.default_nl_prompt

    def _resolve_collaboration_config(
        self,
        template: ScenarioTemplateResponse,
        request: ScenarioFromTemplateCreate,
    ) -> CollaborationConfig | None:
        """Resolve collaboration config from user override or template default.

        Args:
            template: The template containing default collaboration config.
            request: The user request with optional collaboration config override.

        Returns:
            The resolved collaboration config.

        Requirements:
            - 2.2: Auto-fill template predefined configuration items
            - 2.3: Allow user customization on top of template
        """
        if request.collaboration_config is not None:
            return request.collaboration_config
        return template.default_collaboration_config

    def _resolve_enable_collaboration(
        self,
        template: ScenarioTemplateResponse,
        request: ScenarioFromTemplateCreate,
    ) -> bool:
        """Resolve enable_collaboration from user override or template default.

        Args:
            template: The template containing default collaboration config.
            request: The user request with optional enable_collaboration override.

        Returns:
            Whether collaboration should be enabled.

        Requirements:
            - 2.2: Auto-fill template predefined configuration items
            - 2.3: Allow user customization on top of template
        """
        if request.enable_collaboration is not None:
            return request.enable_collaboration

        # If template has collaboration config with auto_create_group or send_email,
        # default to enabling collaboration
        if template.default_collaboration_config:
            config = template.default_collaboration_config
            return config.auto_create_group or config.send_email

        return False

    def validate_template_request(
        self,
        request: ScenarioFromTemplateCreate,
    ) -> list[str]:
        """Validate a template application request and return any errors.

        This method performs validation checks on the request without
        actually applying the template.

        Args:
            request: The request to validate.

        Returns:
            List of validation error messages. Empty list if valid.

        Requirements:
            - 2.2: Validate template application requests
        """
        errors: list[str] = []

        # Check template exists
        if not self.template_exists(request.template_id):
            valid_templates = ", ".join(self.get_template_ids())
            errors.append(
                f"Template '{request.template_id}' not found. "
                f"Valid templates: {valid_templates}"
            )
            return errors  # Can't continue validation without valid template

        template = self.get_template(request.template_id)
        if template is None:
            return errors

        # Validate trigger_command format if provided
        if request.trigger_command is not None:
            if not request.trigger_command.startswith("/"):
                errors.append("trigger_command must start with '/'")

        # Validate scenario type requirements
        scenario_type = template.scenario_type
        trigger_command = request.trigger_command or template.default_trigger_command
        nl_prompt = request.nl_prompt or template.default_nl_prompt

        if scenario_type == ScenarioType.COMMAND:
            if not trigger_command:
                errors.append(
                    "trigger_command is required for command type scenarios"
                )
        elif scenario_type == ScenarioType.NATURAL_LANGUAGE:
            if not nl_prompt:
                errors.append(
                    "nl_prompt is required for natural_language type scenarios"
                )
        elif scenario_type == ScenarioType.HYBRID:
            if not trigger_command and not nl_prompt:
                errors.append(
                    "At least one of trigger_command or nl_prompt is required "
                    "for hybrid type scenarios"
                )

        return errors

    def get_template_with_recommendations(
        self,
        template_id: str,
    ) -> dict[str, Any] | None:
        """Get template with resolved tool and agent recommendations.

        This method returns the template configuration along with
        the recommended tools and agents that should be associated
        with scenarios created from this template.

        Args:
            template_id: The template identifier.

        Returns:
            Dictionary with template config and recommendations, or None if not found.

        Requirements:
            - 2.5: Associate recommended tools and agents with each template
        """
        template = self.get_template(template_id)
        if template is None:
            return None

        config = self.get_template_config(template_id)
        if config is None:
            return None

        # Add recommendation details
        config["tool_recommendations"] = [
            {
                "tool_name": t.tool_name,
                "description": t.description,
                "category": t.category,
                "tool_id": t.tool_id,
            }
            for t in template.recommended_tools
        ]
        config["agent_recommendations"] = [
            {
                "agent_name": a.agent_name,
                "description": a.description,
                "agent_type": a.agent_type,
                "agent_id": a.agent_id,
            }
            for a in template.recommended_agents
        ]

        return config


# Module-level singleton instance
template_service = TemplateService()
