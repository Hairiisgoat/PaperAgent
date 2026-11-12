# M2-09 Research 接入说明

当前实现使用 Crossref 公共 REST API 的 `works` 端点检索期刊文章元数据。查询包含获批任务标题、描述及预期产出的短片段，最长 160 字；查询内容会发送给 Crossref，不调用模型。每项任务最多取得 20 条候选，按任务与文献标题、摘要的词面重合排序，最多保留 5 条；缺少明显匹配的结果仍可作为低证据候选，不能视为经过核实的相关文献。结果只保留具有 DOI 和标题的记录，按 DOI 不区分大小写去重，并记录实际查询及与任务的关联。Crossref 可能没有作者、年份、期刊或摘要；缺失时不填造。跨语言查询、中文词面匹配及相关性排序均有限，可能漏检，也可能返回无关文献。

## 版本与审核

原 `mock_v1` 和 `planner_real_v1` 保持原有行为。任务审核请求显式携带 `continue_research: true` 时，系统仅允许从 `planner_real_v1` 的任务批准进入 `planner_research_v2`，旧运行仍保留旧版本。相同请求可幂等重试，更改选择或图转换要求产生冲突。资料审核继续使用既有接口和审核账本；批准部分或全部资料后暂停在 `research_boundary`，拒绝则取消。不进入 Mock Writing 或 Review。

`GET /api/projects/{project_id}/workflow-runs/{run_id}/approval` 对真实资料返回 `sources`：每项包含 `source_id`、`title`、`authors`、可选 `year` / `journal` / `abstract`、`doi`、`url`、`task_ids` 和任务标题 `tasks`；Mock 响应仍只返回 ID。`relevance_hint` 为 `more`、`some`、`unknown`，仅表示词面证据的相对多少；`same_title_count` 是本次审核中规范化标题相同的记录数量。同标题不同 DOI 是疑似重复或不同版本，保留每条记录供人工判断。这两个提示读取时计算，不改变历史资料、审核决定或 Checkpoint。任务审核的批准请求示例：

```json
{"decision":"approve","approved_ids":["<task-uuid>"],"continue_research":true}
```

不带 `continue_research` 的原请求仍停在 Planner 边界。页面的“批准并启动资料检索”会显式发出该选项；用户仍需自行选择和提交。

## 持久化与恢复

`research_sources` 保存一个运行的来源元数据，`research_source_tasks` 保存来源与任务的多对多关系及查询文字；`(run_id, doi)` 唯一。MySQL 是来源权威记录，Checkpoint 仅保存来源 ID。任务批准需与对应运行的持久化审核决定、项目、线程和生成批次一致。来源批次在同一事务提交；如果批次已存在而节点再执行，会核对并复用，不覆盖已有结果。来源写入成功而 Checkpoint 保存失败时，可依据账本进行人工核对；运行状态为 running 时启动扫描不会盲目重放。Crossref、MySQL 和 SQLite 没有跨系统原子事务，不能保证搜索恰好执行一次。

迁移 `e93d78a61f04` 的父版本是 `d52a7c81f103`。在迁移目标数据库前须按项目既有要求完成完整备份及隔离验证；新功能在表结构不存在时拒绝接受会进入 Research 的任务审核，避免消费审核决定后才发现缺表。
