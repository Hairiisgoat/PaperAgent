# M2-01：PaperAgentState 契约

`app.graph.state.PaperAgentState` 使用标准库 `TypedDict` 描述可序列化的状态快照。它适合 LangGraph 以字典进行节点间传递，能通过静态类型工具识别字段，不引入额外依赖。它是类型声明，不是运行时输入验证器；节点、API 和持久化边界必须在后续实现中验证数据。

## 字段

所有业务 ID 均约定为小写、带连字符的 UUID 字符串；实体是否存在、属于项目以及是否仍可使用，均由业务层在访问时核验。表中的“可省略”表示对应信息尚未产生或尚未载入；空集合表示已知结果为空。引用字段的 `null` 表示明确没有引用或清除已有引用。

| 字段 | 类型 | 必填 / 可空 | 用途 |
| --- | --- | --- | --- |
| `project_id` | `str` | 必填、不可空 | 业务项目 ID；所有节点操作须按项目隔离。 |
| `current_stage` | `WorkflowStage` | 必填、不可空 | `planning`、`research`、`outline`、`writing`、`review`、`export`。 |
| `tasks` | `list[str]` | 可省略、不可空 | 当前规划任务 ID 列表，按任务顺序排列；不是任务正文。 |
| `approved_task_ids` | `list[str]` | 可省略、不可空 | 用户批准进入 Research 的任务 ID。 |
| `source_ids` | `list[str]` | 可省略、不可空 | 当前检索来源 ID。 |
| `approved_source_ids` | `list[str]` | 可省略、不可空 | 用户批准可用于写作的来源 ID。 |
| `outline_id` | `str \| None` | 可省略、可空 | 当前大纲记录 ID。 |
| `current_chapter_id` | `str \| None` | 可省略、可空 | 当前章节 ID。 |
| `review_id` | `str \| None` | 可省略、可空 | 当前审查记录 ID。 |
| `user_decision` | `str \| None` | 可省略、可空 | 当前审核的简短机器决策代码，不包含用户自由文本；各审核节点可用代码及清除时机待对应任务确定。 |
| `retry_count` | `int` | 可省略、不可空 | 当前逻辑操作已发生的重试次数，非负；切换操作时由后续编排明确重置。 |
| `budget_usage` | `BudgetUsage` | 可省略、不可空 | 已知工作流快照累计 `prompt_tokens`、`completion_tokens`，两者均为非负整数；不是账单或限额。 |
| `last_error` | `SafeErrorSummary \| None` | 可省略、可空 | 最近错误的固定分类代码和是否可重试；不保存异常文本。 |

`SafeErrorSummary.code` 仅使用 `timeout`、`provider_unavailable`、`invalid_output`、`database_unavailable`、`budget_exceeded`、`internal_error`、`other`；`retryable` 为布尔值。后续实现必须从异常映射到这些固定代码，不能把原始异常、SQL、密钥、用户输入、Prompt 或模型响应写入错误摘要。分类与重试策略仍需在实际节点中评审。

这里不定义默认状态实例，不在类型中放可变默认值，也不预设 reducer。后续状态更新默认替换对应字段；如果节点并行写入同一字段，必须先单独设计 reducer 或合并策略，不能假设列表会自动追加。

## 线程、运行与恢复

- `thread_id` 是工作流实例的 checkpoint 标识；一次暂停和后续恢复使用同一值，由 LangGraph 配置 `configurable.thread_id` 提供。它不放在 `PaperAgentState` 中。
- `run_id` 标识一次独立执行或恢复尝试，每次尝试生成新的 ID。由执行入口作为运行元数据传递、用于日志和追踪，不放在 `PaperAgentState` 中。安装版本的 `RunnableConfig` 也有自己的 `run_id` 字段；未来接入时需明确与应用追踪 ID 的映射和 UUID 类型，不能假设二者自动等同。
- 恢复必须使用对应线程标识，并先验证项目归属、生命周期和待处理审核；用户决策的权威记录先落业务库，再恢复工作流。这里只描述要求，不实现 interrupt 或 resume。

## 数据边界与后续工作

State 只包含流程需要的引用、少量控制值和安全分类。任务、来源、证据、章节、版本、引用、审查和用户决策的权威数据由业务库保存；完整正文、文档、检索材料、Prompt 和模型原始响应不能放入长期状态。checkpoint 与业务库之间不是原子事务；后续节点必须设计提交顺序、幂等与恢复对账。

类型声明不会验证 UUID 格式、项目归属、数量上限、非负数字、批准集合或决策代码，更不能阻止运行时把大文本或敏感信息塞入字符串。后续入口及节点必须验证并限制这些数据；尚无任务、来源和章节等业务表可供本任务检验。Token 使用量仅用于轻量流程判断，成本、币种、估算方式与项目预算权威记录需要后续确定；不得将 Token 数量当作账单。审批决策类型、并行更新策略及版本兼容迁移也留待对应任务确定。
