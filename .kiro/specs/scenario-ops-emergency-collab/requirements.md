# 需求文档

## 简介

本功能增强 AIOpsOS 平台的场景运维能力，并新增应急协同模块。场景运维优化支持多种场景类型（命令式、自然语言式、混合式），提供标准化运维场景模板（故障定界、健康巡检、容量预测、告警分析等），并支持灵活的触发规则配置。应急协同模块实现标准化场景触发后的自动化协作流程，包括自动创建企业微信群、发送邮件、同步群消息、接收群消息、自动分析处理进度并提供下一步建议。

## 术语表

- **Scenario（场景）**: 一组预定义的运维操作流程，可由触发器自动执行或手动触发
- **Scenario_Type（场景类型）**: 场景的执行方式分类，包括命令式、自然语言式、混合式
- **Scenario_Template（场景模板）**: 预置的标准化运维场景配置，如故障定界、健康巡检等
- **Trigger_Rule（触发规则）**: 定义场景自动执行的条件，包括告警阈值、告警类型、性能趋势等
- **Scenario_Execution（场景执行）**: 场景的一次运行实例，包含执行状态、结果、日志等
- **Emergency_Collaboration（应急协同）**: 场景触发后的协作流程，包括群聊创建、消息同步、进度分析等
- **Collaboration_Session（协同会话）**: 一次应急协同的完整生命周期，从创建到关闭
- **Progress_Analysis（进度分析）**: 对协同会话中消息和操作的智能分析，生成处理进度报告
- **Recommendation_Engine（建议引擎）**: 基于当前进度和上下文，生成下一步操作建议的组件
- **Resource_Association（资源关联）**: 场景与技能、智能体、知识库、消息渠道的关联关系
- **Execution_Result（执行结果）**: 场景执行完成后的输出，包括状态、数据、建议等

## 需求

### 需求 1: 场景类型系统

**用户故事:** 作为运维工程师，我希望能够创建不同类型的运维场景，以便根据实际需求选择最合适的执行方式。

#### 验收标准

1. THE Scenario_System SHALL 支持三种场景类型：command（命令式）、natural_language（自然语言式）、hybrid（混合式）
2. WHEN 场景类型为 command 时，THE Scenario_System SHALL 要求配置 trigger_command 字段，格式为以斜杠开头的命令字符串
3. WHEN 场景类型为 natural_language 时，THE Scenario_System SHALL 要求配置 nl_prompt 字段，用于描述场景的自然语言指令
4. WHEN 场景类型为 hybrid 时，THE Scenario_System SHALL 同时支持 trigger_command 和 nl_prompt 两种触发方式
5. THE Scenario_System SHALL 在场景创建时验证类型与必填字段的一致性
6. IF 场景类型与必填字段不匹配，THEN THE Scenario_System SHALL 返回明确的验证错误信息

### 需求 2: 标准化场景模板

**用户故事:** 作为运维工程师，我希望能够使用预置的标准化场景模板，以便快速创建常用的运维场景。

#### 验收标准

1. THE Scenario_Template_System SHALL 提供以下内置模板：fault_isolation（故障定界）、health_inspection（健康巡检）、capacity_prediction（容量预测）、alert_analysis（告警分析）
2. WHEN 用户选择模板创建场景时，THE Scenario_Template_System SHALL 自动填充模板预定义的配置项
3. THE Scenario_Template_System SHALL 允许用户在模板基础上自定义修改配置
4. THE Scenario_Template_System SHALL 为每个模板提供默认的参数模式定义（params_schema）
5. THE Scenario_Template_System SHALL 为每个模板关联推荐的工具和智能体
6. WHEN 用户基于模板创建场景时，THE Scenario_Template_System SHALL 记录场景的模板来源

### 需求 3: 增强触发条件

**用户故事:** 作为运维工程师，我希望能够配置更丰富的触发条件，以便场景能够在合适的时机自动执行。

#### 验收标准

1. THE Trigger_Rule_System SHALL 支持基于告警数量阈值的触发条件
2. THE Trigger_Rule_System SHALL 支持基于特定告警类型的触发条件
3. THE Trigger_Rule_System SHALL 支持基于告警严重级别的触发条件
4. THE Trigger_Rule_System SHALL 支持基于性能数据趋势的触发条件，包括上升趋势、下降趋势、异常波动
5. THE Trigger_Rule_System SHALL 支持组合条件，使用 AND、OR、NOT 逻辑运算符
6. WHEN 触发条件满足时，THE Trigger_Rule_System SHALL 在配置的频率限制内触发场景执行
7. THE Trigger_Rule_System SHALL 支持配置触发的时间窗口，仅在指定时间段内生效
8. THE Trigger_Rule_System SHALL 记录每次触发的时间和原因

### 需求 4: 场景资源关联

**用户故事:** 作为运维工程师，我希望能够为场景关联各种资源，以便场景执行时能够调用所需的能力。

#### 验收标准

1. THE Scenario_System SHALL 支持场景与技能（Skill）的多对多关联
2. THE Scenario_System SHALL 支持场景与智能体（Agent）的多对多关联
3. THE Scenario_System SHALL 支持场景与知识库文档（KnowledgeDocument）的多对多关联
4. THE Scenario_System SHALL 支持场景与消息渠道（NotificationChannel）的多对多关联
5. WHEN 场景执行时，THE Scenario_System SHALL 加载所有关联的资源供执行引擎使用
6. THE Scenario_System SHALL 在删除资源时自动解除与场景的关联关系
7. THE Scenario_System SHALL 提供 API 查询场景的所有关联资源

### 需求 5: 场景执行引擎

**用户故事:** 作为运维工程师，我希望场景能够可靠地执行并生成详细的执行结果，以便我了解执行情况。

#### 验收标准

1. THE Scenario_Execution_Engine SHALL 支持手动触发场景执行
2. THE Scenario_Execution_Engine SHALL 支持自动触发场景执行（通过触发规则或调度）
3. WHEN 场景开始执行时，THE Scenario_Execution_Engine SHALL 创建执行记录，状态为 running
4. THE Scenario_Execution_Engine SHALL 根据场景类型选择对应的执行策略
5. WHEN 场景类型为 command 时，THE Scenario_Execution_Engine SHALL 解析并执行配置的命令
6. WHEN 场景类型为 natural_language 时，THE Scenario_Execution_Engine SHALL 将 nl_prompt 发送给关联的智能体处理
7. THE Scenario_Execution_Engine SHALL 在执行过程中记录详细的执行日志
8. WHEN 场景执行完成时，THE Scenario_Execution_Engine SHALL 更新执行记录状态为 completed 或 failed
9. THE Scenario_Execution_Engine SHALL 生成结构化的执行结果，包括输出数据、建议、耗时等
10. IF 场景执行超时，THEN THE Scenario_Execution_Engine SHALL 终止执行并记录超时状态
11. THE Scenario_Execution_Engine SHALL 支持配置执行超时时间，默认为 300 秒

### 需求 6: 应急协同工作流

**用户故事:** 作为运维工程师，我希望在场景触发时能够自动启动应急协同流程，以便快速组织相关人员处理问题。

#### 验收标准

1. THE Emergency_Collaboration_System SHALL 支持场景配置是否启用应急协同
2. WHEN 启用应急协同的场景被触发时，THE Emergency_Collaboration_System SHALL 自动创建协同会话
3. THE Emergency_Collaboration_System SHALL 为协同会话生成唯一标识符
4. THE Emergency_Collaboration_System SHALL 记录协同会话的创建时间、触发场景、触发原因
5. THE Emergency_Collaboration_System SHALL 支持协同会话的状态流转：created、active、resolved、closed
6. WHEN 协同会话创建时，THE Emergency_Collaboration_System SHALL 根据场景配置执行初始化动作
7. THE Emergency_Collaboration_System SHALL 支持手动关闭协同会话
8. WHEN 协同会话关闭时，THE Emergency_Collaboration_System SHALL 生成协同总结报告

### 需求 7: 群聊管理

**用户故事:** 作为运维工程师，我希望应急协同能够自动创建和管理企业微信群，以便快速建立沟通渠道。

#### 验收标准

1. THE Group_Chat_Manager SHALL 支持通过企业微信应用 API 自动创建群聊
2. WHEN 创建群聊时，THE Group_Chat_Manager SHALL 根据场景配置添加指定的群成员
3. THE Group_Chat_Manager SHALL 支持配置群聊名称模板，支持变量替换（场景名、时间、告警标题等）
4. WHEN 群聊创建成功时，THE Group_Chat_Manager SHALL 记录群聊 ID 与协同会话的关联
5. THE Group_Chat_Manager SHALL 支持向群聊发送文本消息和 Markdown 消息
6. THE Group_Chat_Manager SHALL 支持从群聊接收消息并同步到协同会话
7. WHEN 接收到群聊消息时，THE Group_Chat_Manager SHALL 解析消息内容并存储到消息记录
8. THE Group_Chat_Manager SHALL 支持查询群聊的基本信息
9. IF 群聊创建失败，THEN THE Group_Chat_Manager SHALL 记录错误信息并通知相关人员

### 需求 8: 邮件通知

**用户故事:** 作为运维工程师，我希望应急协同能够自动发送邮件通知，以便相关人员及时了解情况。

#### 验收标准

1. THE Email_Notification_System SHALL 支持在协同会话创建时自动发送邮件通知
2. THE Email_Notification_System SHALL 支持配置邮件收件人列表，支持从场景配置和用户组获取
3. THE Email_Notification_System SHALL 支持配置邮件模板，包括主题和正文模板
4. THE Email_Notification_System SHALL 支持模板变量替换，包括场景信息、告警信息、协同会话信息
5. WHEN 协同会话状态变更时，THE Email_Notification_System SHALL 根据配置发送状态更新邮件
6. THE Email_Notification_System SHALL 记录邮件发送状态和时间
7. IF 邮件发送失败，THEN THE Email_Notification_System SHALL 记录错误并支持重试

### 需求 9: 消息同步

**用户故事:** 作为运维工程师，我希望协同会话中的消息能够在多个渠道间同步，以便所有参与者都能获取最新信息。

#### 验收标准

1. THE Message_Sync_System SHALL 支持将协同会话消息同步到关联的群聊
2. THE Message_Sync_System SHALL 支持将群聊消息同步到协同会话记录
3. THE Message_Sync_System SHALL 支持配置消息同步的方向：单向或双向
4. THE Message_Sync_System SHALL 为每条同步消息记录来源渠道和原始消息 ID
5. THE Message_Sync_System SHALL 支持消息格式转换，适配不同渠道的消息格式要求
6. WHEN 消息同步失败时，THE Message_Sync_System SHALL 记录失败原因并支持手动重试
7. THE Message_Sync_System SHALL 避免消息重复同步，通过消息 ID 去重

### 需求 10: 进度分析

**用户故事:** 作为运维工程师，我希望系统能够自动分析协同会话的处理进度，以便我了解当前状态。

#### 验收标准

1. THE Progress_Analysis_System SHALL 定期分析协同会话中的消息和操作记录
2. THE Progress_Analysis_System SHALL 识别关键事件，包括问题确认、方案讨论、操作执行、结果验证
3. THE Progress_Analysis_System SHALL 生成进度摘要，包括已完成步骤、当前阶段、待处理事项
4. THE Progress_Analysis_System SHALL 计算处理时长和各阶段耗时
5. THE Progress_Analysis_System SHALL 支持手动触发进度分析
6. THE Progress_Analysis_System SHALL 支持配置自动分析的时间间隔
7. WHEN 进度分析完成时，THE Progress_Analysis_System SHALL 更新协同会话的进度状态
8. THE Progress_Analysis_System SHALL 将分析结果存储到协同会话记录中

### 需求 11: 下一步建议

**用户故事:** 作为运维工程师，我希望系统能够根据当前进度提供下一步操作建议，以便我快速决策。

#### 验收标准

1. THE Recommendation_Engine SHALL 基于进度分析结果生成下一步操作建议
2. THE Recommendation_Engine SHALL 结合关联的知识库文档提供相关参考信息
3. THE Recommendation_Engine SHALL 根据场景类型和模板提供针对性建议
4. THE Recommendation_Engine SHALL 为每条建议提供优先级和预估影响
5. THE Recommendation_Engine SHALL 支持用户对建议进行反馈（采纳、忽略、修改）
6. WHEN 用户采纳建议时，THE Recommendation_Engine SHALL 记录采纳操作并更新进度
7. THE Recommendation_Engine SHALL 支持将建议发送到群聊供团队讨论
8. THE Recommendation_Engine SHALL 学习历史反馈数据优化建议质量

### 需求 12: 协同会话查询与管理

**用户故事:** 作为运维工程师，我希望能够查询和管理所有协同会话，以便跟踪历史记录和当前状态。

#### 验收标准

1. THE Collaboration_Management_System SHALL 提供协同会话列表查询 API，支持分页
2. THE Collaboration_Management_System SHALL 支持按状态、时间范围、场景筛选协同会话
3. THE Collaboration_Management_System SHALL 提供协同会话详情查询，包括消息记录、进度分析、建议历史
4. THE Collaboration_Management_System SHALL 支持导出协同会话报告
5. THE Collaboration_Management_System SHALL 支持手动更新协同会话状态
6. THE Collaboration_Management_System SHALL 记录所有状态变更的操作日志
7. THE Collaboration_Management_System SHALL 支持按关键词搜索协同会话消息内容
