# Implementation Plan: 场景运维优化与应急协同

## Overview

本实现计划将场景运维优化和应急协同功能分解为可执行的编码任务。实现采用增量方式，从数据模型扩展开始，逐步构建服务层、API层和集成层。每个任务都引用具体的需求条款，确保完整覆盖所有功能需求。

## Tasks

- [ ] 1. 数据模型扩展与数据库迁移
  - [ ] 1.1 扩展 Scenario 模型，添加场景类型和应急协同字段
    - 在 `server/src/models/agent.py` 中扩展 Scenario 模型
    - 添加字段：`scenario_type`、`nl_prompt`、`template_id`、`execution_timeout`
    - 添加字段：`enable_collaboration`、`collaboration_config`
    - 添加与 KnowledgeDocument 和 NotificationChannel 的关联关系
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 4.3, 4.4, 6.1_

  - [ ] 1.2 创建 ScenarioExecution 模型
    - 在 `server/src/models/scenario.py` 中创建新文件
    - 实现 ScenarioExecution 模型，包含执行状态、参数、结果、日志字段
    - 添加与 Scenario 和 CollaborationSession 的关联关系
    - _Requirements: 5.3, 5.7, 5.8, 5.9_

  - [ ] 1.3 创建 CollaborationSession 模型
    - 在 `server/src/models/collaboration.py` 中创建新文件
    - 实现 CollaborationSession 模型，包含状态、群聊信息、进度摘要字段
    - 添加与 Scenario、ScenarioExecution 的关联关系
    - _Requirements: 6.2, 6.3, 6.4, 6.5, 7.4_

  - [ ] 1.4 创建 CollaborationMessage 和 CollaborationRecommendation 模型
    - 在 `server/src/models/collaboration.py` 中添加消息和建议模型
    - CollaborationMessage 包含来源渠道、发送者、内容、同步状态
    - CollaborationRecommendation 包含优先级、影响评估、反馈状态
    - _Requirements: 9.4, 9.5, 11.1, 11.4, 11.5_

  - [ ] 1.5 扩展 SceneTrigger 模型，支持增强触发条件
    - 在 `server/src/models/schedule.py` 中扩展 SceneTrigger 模型
    - 添加字段：`description`、`last_triggered_at`、`trigger_count`
    - 扩展 condition JSONB 结构支持趋势检测
    - _Requirements: 3.4, 3.8_

  - [ ] 1.6 创建数据库迁移脚本
    - 使用 Alembic 创建迁移脚本
    - 包含所有新表和字段的创建
    - 添加必要的索引和约束
    - _Requirements: 1.1-1.6, 3.1-3.8, 4.1-4.7, 5.1-5.11, 6.1-6.8_

- [ ] 2. Checkpoint - 确保数据模型迁移正确
  - 确保所有测试通过，如有问题请询问用户。

- [ ] 3. Schema 层实现
  - [ ] 3.1 创建场景相关 Pydantic Schema
    - 在 `server/src/schemas/scenario.py` 中创建新文件
    - 实现 ScenarioCreate、ScenarioUpdate、ScenarioResponse Schema
    - 实现 ScenarioExecutionCreate、ScenarioExecutionResponse Schema
    - 添加场景类型验证逻辑
    - _Requirements: 1.1, 1.5, 1.6, 5.1, 5.2_

  - [ ] 3.2 创建协同会话相关 Pydantic Schema
    - 在 `server/src/schemas/collaboration.py` 中创建新文件
    - 实现 CollaborationSessionCreate、CollaborationSessionResponse Schema
    - 实现 CollaborationMessageCreate、CollaborationMessageResponse Schema
    - 实现 CollaborationRecommendationResponse Schema
    - _Requirements: 6.2, 6.3, 6.4, 9.4, 11.1, 11.4_

  - [ ] 3.3 创建场景模板相关 Schema
    - 在 `server/src/schemas/scenario.py` 中添加模板 Schema
    - 实现 ScenarioTemplateResponse Schema
    - 定义内置模板的参数模式
    - _Requirements: 2.1, 2.4, 2.5_

  - [ ] 3.4 扩展触发规则 Schema
    - 在 `server/src/schemas/schedule.py` 中扩展 SceneTrigger Schema
    - 添加趋势条件配置的验证
    - 添加组合条件的验证逻辑
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 4. 场景模板服务实现
  - [ ] 4.1 创建 TemplateService 服务
    - 在 `server/src/services/template_service.py` 中创建新文件
    - 实现内置模板定义：fault_isolation、health_inspection、capacity_prediction、alert_analysis
    - 实现 get_template、list_templates 方法
    - _Requirements: 2.1, 2.4, 2.5_

  - [ ] 4.2 实现模板应用逻辑
    - 在 TemplateService 中实现 apply_template 方法
    - 支持模板配置的自动填充
    - 支持用户自定义修改
    - 记录场景的模板来源
    - _Requirements: 2.2, 2.3, 2.6_

  - [ ]* 4.3 编写 TemplateService 单元测试
    - 测试模板获取和列表功能
    - 测试模板应用和自定义修改
    - _Requirements: 2.1-2.6_

- [ ] 5. 触发规则引擎增强
  - [ ] 5.1 扩展 TriggerRuleEngine 支持增强条件
    - 在 `server/src/services/trigger_engine.py` 中扩展现有实现
    - 添加告警数量阈值条件评估
    - 添加告警类型和严重级别条件评估
    - _Requirements: 3.1, 3.2, 3.3_

  - [ ] 5.2 实现趋势检测条件
    - 在 TriggerRuleEngine 中添加趋势检测逻辑
    - 支持上升趋势、下降趋势、异常波动检测
    - 实现基于时间窗口的趋势计算
    - _Requirements: 3.4_

  - [ ] 5.3 实现组合条件和频率限制
    - 扩展 evaluate_condition 支持 NOT 逻辑运算符
    - 实现基于 Redis 的频率限制检查
    - 实现时间窗口生效检查
    - 记录触发时间和原因
    - _Requirements: 3.5, 3.6, 3.7, 3.8_

  - [ ]* 5.4 编写触发规则引擎单元测试
    - 测试各种条件类型的评估
    - 测试组合条件和频率限制
    - _Requirements: 3.1-3.8_

- [ ] 6. Checkpoint - 确保触发规则引擎正确
  - 确保所有测试通过，如有问题请询问用户。

- [ ] 7. 场景执行引擎实现
  - [ ] 7.1 创建 ScenarioExecutionEngine 服务
    - 在 `server/src/services/scenario_execution.py` 中创建新文件
    - 实现执行记录创建和状态管理
    - 实现手动触发和自动触发入口
    - _Requirements: 5.1, 5.2, 5.3_

  - [ ] 7.2 实现场景类型执行策略
    - 实现 command 类型执行策略：解析并执行命令
    - 实现 natural_language 类型执行策略：发送给智能体处理
    - 实现 hybrid 类型执行策略：支持两种触发方式
    - _Requirements: 5.4, 5.5, 5.6_

  - [ ] 7.3 实现执行日志和结果处理
    - 实现执行过程日志记录
    - 实现执行完成状态更新（completed/failed）
    - 生成结构化执行结果
    - _Requirements: 5.7, 5.8, 5.9_

  - [ ] 7.4 实现执行超时处理
    - 实现执行超时检测和终止
    - 记录超时状态
    - 支持配置超时时间
    - _Requirements: 5.10, 5.11_

  - [ ] 7.5 实现资源加载逻辑
    - 加载场景关联的技能、智能体、知识库、消息渠道
    - 提供资源给执行引擎使用
    - _Requirements: 4.5_

  - [ ]* 7.6 编写场景执行引擎单元测试
    - 测试不同场景类型的执行
    - 测试超时处理和状态管理
    - _Requirements: 5.1-5.11_

- [ ] 8. 应急协同核心服务实现
  - [ ] 8.1 创建 CollaborationService 服务
    - 在 `server/src/services/collaboration_service.py` 中创建新文件
    - 实现协同会话创建逻辑
    - 生成唯一标识符，记录创建时间、触发场景、触发原因
    - _Requirements: 6.2, 6.3, 6.4_

  - [ ] 8.2 实现协同会话状态管理
    - 实现状态流转：created → active → resolved → closed
    - 实现手动关闭协同会话
    - 生成协同总结报告
    - _Requirements: 6.5, 6.7, 6.8_

  - [ ] 8.3 实现协同初始化动作
    - 根据场景配置执行初始化动作
    - 集成群聊创建和邮件发送
    - _Requirements: 6.6_

  - [ ]* 8.4 编写 CollaborationService 单元测试
    - 测试会话创建和状态流转
    - 测试初始化动作执行
    - _Requirements: 6.1-6.8_

- [ ] 9. Checkpoint - 确保核心服务正确
  - 确保所有测试通过，如有问题请询问用户。

- [ ] 10. 群聊管理集成
  - [ ] 10.1 创建 GroupChatManager 服务
    - 在 `server/src/services/group_chat_manager.py` 中创建新文件
    - 实现企业微信应用 API 集成
    - 实现群聊创建功能
    - _Requirements: 7.1_

  - [ ] 10.2 实现群聊配置和成员管理
    - 根据场景配置添加群成员
    - 支持群聊名称模板和变量替换
    - 记录群聊 ID 与协同会话关联
    - _Requirements: 7.2, 7.3, 7.4_

  - [ ] 10.3 实现群聊消息收发
    - 实现发送文本消息和 Markdown 消息
    - 实现接收群聊消息并同步到协同会话
    - 解析消息内容并存储到消息记录
    - _Requirements: 7.5, 7.6, 7.7_

  - [ ] 10.4 实现群聊查询和错误处理
    - 实现查询群聊基本信息
    - 实现创建失败的错误记录和通知
    - _Requirements: 7.8, 7.9_

  - [ ]* 10.5 编写 GroupChatManager 单元测试
    - 测试群聊创建和消息收发
    - 测试错误处理逻辑
    - _Requirements: 7.1-7.9_

- [ ] 11. 邮件通知服务实现
  - [ ] 11.1 创建 EmailNotificationService 服务
    - 在 `server/src/services/email_notification.py` 中创建新文件
    - 实现 SMTP 邮件发送功能
    - 支持协同会话创建时自动发送邮件
    - _Requirements: 8.1_

  - [ ] 11.2 实现邮件配置和模板
    - 支持配置收件人列表（场景配置和用户组）
    - 实现邮件模板（主题和正文）
    - 支持模板变量替换
    - _Requirements: 8.2, 8.3, 8.4_

  - [ ] 11.3 实现状态更新邮件和重试机制
    - 协同会话状态变更时发送更新邮件
    - 记录邮件发送状态和时间
    - 实现发送失败的错误记录和重试
    - _Requirements: 8.5, 8.6, 8.7_

  - [ ]* 11.4 编写 EmailNotificationService 单元测试
    - 测试邮件发送和模板渲染
    - 测试重试机制
    - _Requirements: 8.1-8.7_

- [ ] 12. 消息同步服务实现
  - [ ] 12.1 创建 MessageSyncService 服务
    - 在 `server/src/services/message_sync.py` 中创建新文件
    - 实现协同会话消息到群聊的同步
    - 实现群聊消息到协同会话的同步
    - _Requirements: 9.1, 9.2_

  - [ ] 12.2 实现同步配置和格式转换
    - 支持配置同步方向（单向或双向）
    - 记录来源渠道和原始消息 ID
    - 实现消息格式转换适配不同渠道
    - _Requirements: 9.3, 9.4, 9.5_

  - [ ] 12.3 实现同步失败处理和去重
    - 记录同步失败原因并支持手动重试
    - 通过消息 ID 去重避免重复同步
    - _Requirements: 9.6, 9.7_

  - [ ]* 12.4 编写 MessageSyncService 单元测试
    - 测试消息同步和格式转换
    - 测试去重和重试逻辑
    - _Requirements: 9.1-9.7_

- [ ] 13. Checkpoint - 确保集成服务正确
  - 确保所有测试通过，如有问题请询问用户。

- [ ] 14. 进度分析服务实现
  - [ ] 14.1 创建 ProgressAnalyzer 服务
    - 在 `server/src/services/progress_analyzer.py` 中创建新文件
    - 实现定期分析协同会话消息和操作记录
    - 集成 LLM 服务进行智能分析
    - _Requirements: 10.1_

  - [ ] 14.2 实现关键事件识别和进度摘要
    - 识别关键事件：问题确认、方案讨论、操作执行、结果验证
    - 生成进度摘要：已完成步骤、当前阶段、待处理事项
    - 计算处理时长和各阶段耗时
    - _Requirements: 10.2, 10.3, 10.4_

  - [ ] 14.3 实现分析触发和结果存储
    - 支持手动触发进度分析
    - 支持配置自动分析时间间隔
    - 更新协同会话进度状态
    - 存储分析结果到协同会话记录
    - _Requirements: 10.5, 10.6, 10.7, 10.8_

  - [ ]* 14.4 编写 ProgressAnalyzer 单元测试
    - 测试进度分析和事件识别
    - 测试自动分析调度
    - _Requirements: 10.1-10.8_

- [ ] 15. 建议引擎实现
  - [ ] 15.1 创建 RecommendationEngine 服务
    - 在 `server/src/services/recommendation_engine.py` 中创建新文件
    - 基于进度分析结果生成下一步操作建议
    - 集成 LLM 服务进行智能建议生成
    - _Requirements: 11.1_

  - [ ] 15.2 实现建议生成逻辑
    - 结合关联知识库文档提供参考信息
    - 根据场景类型和模板提供针对性建议
    - 为每条建议提供优先级和预估影响
    - _Requirements: 11.2, 11.3, 11.4_

  - [ ] 15.3 实现建议反馈和学习
    - 支持用户对建议进行反馈（采纳、忽略、修改）
    - 记录采纳操作并更新进度
    - 支持将建议发送到群聊
    - 学习历史反馈数据优化建议质量
    - _Requirements: 11.5, 11.6, 11.7, 11.8_

  - [ ]* 15.4 编写 RecommendationEngine 单元测试
    - 测试建议生成和反馈处理
    - 测试知识库集成
    - _Requirements: 11.1-11.8_

- [ ] 16. Checkpoint - 确保分析和建议服务正确
  - 确保所有测试通过，如有问题请询问用户。

- [ ] 17. API 层实现 - 场景管理
  - [ ] 17.1 创建场景 CRUD API
    - 在 `server/src/api/control/scenario.py` 中创建新文件
    - 实现场景创建、更新、删除、查询 API
    - 添加场景类型验证
    - _Requirements: 1.1-1.6_

  - [ ] 17.2 创建场景模板 API
    - 实现模板列表查询 API
    - 实现基于模板创建场景 API
    - _Requirements: 2.1-2.6_

  - [ ] 17.3 创建场景资源关联 API
    - 实现场景与技能、智能体、知识库、消息渠道的关联 API
    - 实现查询场景所有关联资源 API
    - _Requirements: 4.1-4.7_

  - [ ] 17.4 创建场景执行 API
    - 实现手动触发场景执行 API
    - 实现查询执行记录 API
    - _Requirements: 5.1, 5.2_

  - [ ]* 17.5 编写场景 API 集成测试
    - 测试场景 CRUD 操作
    - 测试模板应用和资源关联
    - _Requirements: 1.1-1.6, 2.1-2.6, 4.1-4.7, 5.1-5.2_

- [ ] 18. API 层实现 - 触发规则管理
  - [ ] 18.1 扩展触发规则 API
    - 在 `server/src/api/control/schedule.py` 中扩展现有 API
    - 支持增强触发条件的创建和更新
    - 支持趋势条件配置
    - _Requirements: 3.1-3.8_

  - [ ]* 18.2 编写触发规则 API 集成测试
    - 测试增强触发条件的 CRUD
    - _Requirements: 3.1-3.8_

- [ ] 19. API 层实现 - 协同会话管理
  - [ ] 19.1 创建协同会话 API
    - 在 `server/src/api/control/collaboration.py` 中创建新文件
    - 实现协同会话列表查询 API（支持分页）
    - 实现按状态、时间范围、场景筛选
    - _Requirements: 12.1, 12.2_

  - [ ] 19.2 创建协同会话详情和管理 API
    - 实现协同会话详情查询（消息记录、进度分析、建议历史）
    - 实现导出协同会话报告 API
    - 实现手动更新协同会话状态 API
    - 记录状态变更操作日志
    - _Requirements: 12.3, 12.4, 12.5, 12.6_

  - [ ] 19.3 创建协同会话搜索 API
    - 实现按关键词搜索协同会话消息内容
    - _Requirements: 12.7_

  - [ ] 19.4 创建进度分析和建议 API
    - 实现手动触发进度分析 API
    - 实现获取建议列表 API
    - 实现建议反馈 API
    - _Requirements: 10.5, 11.5, 11.6_

  - [ ]* 19.5 编写协同会话 API 集成测试
    - 测试会话查询和管理
    - 测试进度分析和建议功能
    - _Requirements: 12.1-12.7, 10.5, 11.5-11.6_

- [ ] 20. Checkpoint - 确保 API 层正确
  - 确保所有测试通过，如有问题请询问用户。

- [ ] 21. 集成与端到端测试
  - [ ] 21.1 实现场景执行与协同会话集成
    - 在 ScenarioExecutionEngine 中集成 CollaborationService
    - 启用应急协同的场景触发时自动创建协同会话
    - _Requirements: 6.1, 6.2_

  - [ ] 21.2 实现消息同步集成
    - 集成 GroupChatManager 和 MessageSyncService
    - 实现群聊消息的实时同步
    - _Requirements: 9.1, 9.2_

  - [ ] 21.3 实现进度分析和建议集成
    - 集成 ProgressAnalyzer 和 RecommendationEngine
    - 实现分析完成后自动生成建议
    - _Requirements: 10.7, 11.1_

  - [ ]* 21.4 编写端到端集成测试
    - 测试完整的场景触发到协同会话流程
    - 测试消息同步和进度分析流程
    - _Requirements: 全部需求_

- [ ] 22. 最终 Checkpoint - 确保所有功能正确
  - 确保所有测试通过，如有问题请询问用户。

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- The implementation follows the existing codebase patterns (FastAPI + SQLAlchemy + Pydantic)
- External integrations (企业微信 API, SMTP, LLM) should be implemented with proper error handling and retry mechanisms
- All new services should follow the existing service patterns in `server/src/services/`
- Database migrations should be created using Alembic following existing migration patterns

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4", "1.5"] },
    { "id": 1, "tasks": ["1.6"] },
    { "id": 2, "tasks": ["3.1", "3.2", "3.3", "3.4"] },
    { "id": 3, "tasks": ["4.1", "5.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "5.2"] },
    { "id": 5, "tasks": ["5.3", "5.4"] },
    { "id": 6, "tasks": ["7.1"] },
    { "id": 7, "tasks": ["7.2", "7.3"] },
    { "id": 8, "tasks": ["7.4", "7.5", "7.6"] },
    { "id": 9, "tasks": ["8.1"] },
    { "id": 10, "tasks": ["8.2", "8.3", "8.4"] },
    { "id": 11, "tasks": ["10.1", "11.1"] },
    { "id": 12, "tasks": ["10.2", "10.3", "11.2"] },
    { "id": 13, "tasks": ["10.4", "10.5", "11.3", "11.4"] },
    { "id": 14, "tasks": ["12.1"] },
    { "id": 15, "tasks": ["12.2", "12.3", "12.4"] },
    { "id": 16, "tasks": ["14.1"] },
    { "id": 17, "tasks": ["14.2", "14.3", "14.4"] },
    { "id": 18, "tasks": ["15.1"] },
    { "id": 19, "tasks": ["15.2", "15.3", "15.4"] },
    { "id": 20, "tasks": ["17.1", "17.2"] },
    { "id": 21, "tasks": ["17.3", "17.4", "17.5"] },
    { "id": 22, "tasks": ["18.1", "18.2"] },
    { "id": 23, "tasks": ["19.1", "19.2"] },
    { "id": 24, "tasks": ["19.3", "19.4", "19.5"] },
    { "id": 25, "tasks": ["21.1"] },
    { "id": 26, "tasks": ["21.2", "21.3"] },
    { "id": 27, "tasks": ["21.4"] }
  ]
}
```
