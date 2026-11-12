# M2-08-02 实施记录

默认 `/runs` 和 Mock 图保留；显式 `/planner-runs` 将项目输入快照、图版本和一次调用意图同排队事件持久化。Planner 在发送前提交 `invoking`，使用预分配的 call ID 和 2048 token；响应经过严格解析后以一次事务保存完整任务批次及用量。`workflow_tasks` 按 generation 与项目/线程关联；审核详情在验证 checkpoint 后读取任务。批准恢复原线程并进入 `paused`，拒绝为 `cancelled`。

`planner_generations` 和 `workflow_tasks` 及 `workflow_runs.graph_version` 由新迁移提供；迁移是向前操作，不提供破坏性回滚。不要在未审查和备份的数据库上执行迁移。模型请求超时和 `invoking` 中断结果未知时不自动重试；MySQL、SQLite 和供应商不存在跨系统事务或 exactly-once 计费。工作流级用量对账及 running 阶段崩溃后的自动修复仍需后续完善，不能视为自动恢复保证。
# Planner 超时与恢复补充

Planner 使用已持久化的 `max_tokens`（首版 2048）及可配置的 `LLM_PLANNER_READ_TIMEOUT_SECONDS`（默认 120 秒）。`call_id` 同时发送为供应商 `request_id`，只用于关联，不用于自动重试。模型调用诊断写入项目 `logs/llm-diagnostics.log`，仅包含安全元数据。

服务启动扫描 `needs_review` 时只记录人工对账提示，不调度旧调用。`invoking`、`needs_review` 和 MySQL 提交结果不确定时必须人工对账；`queued` 且调用意图为 `prepared` 才可按已有规则调度。模型成功但任务事务失败时保留 `invoking`，不可重发；任务已提交而 checkpoint 落后时只允许经过核验后复用任务，当前需人工协调，不会自动恢复 running 运行。已保存批次与 checkpoint 的 generation ID 或任务 ID 不一致会拒绝处理。

可使用只读命令核对单次运行，不触发恢复或模型调用：

```powershell
.\.venv\Scripts\python.exe -m app.services.planner_reconciliation --project-id <项目UUID> --run-id <运行UUID>
```

输出为安全状态、调用标识、已知 Token、checkpoint 状态及处理建议；未知用量为 null。账本与 checkpoint 没有跨库原子事务，且供应商费用仍需在供应商侧核对。

