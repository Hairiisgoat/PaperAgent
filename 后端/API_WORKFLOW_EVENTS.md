# M2-05 工作流事件与 SSE

## 接口

- `POST /api/projects/{project_id}/runs` 创建独立的 Mock 工作流线程和运行尝试，返回 202：`{"project_id":"...","thread_id":"...","run_id":"...","status":"queued"}`。后台在同一进程执行 Planner 直到真实任务审核中断；不自动批准。重复 POST 创建不同线程，没有请求级去重。
- `GET /api/projects/{project_id}/workflow-runs/{run_id}/events` 验证项目和运行归属后返回 `text/event-stream`，含 `Cache-Control: no-cache, no-transform` 和 `X-Accel-Buffering: no`。不存在、已删除或跨项目返回 404，错误 UUID 或非法游标返回 422。

新线程的 SQLite 检查点使用 `LANGGRAPH_SQLITE_PATH`（环境变量优先，其次后端 .env，默认 `后端/data/langgraph-checkpoints.sqlite3`），每次执行使用受控连接并关闭。当前 `approval_required.payload_ref` 为该 run 的 UUID；M2-07-01 可通过项目范围的 `GET /workflow-runs/{run_id}/approval` 读取已核验的待审核 ID，但目前没有任务名称或描述；审核提交接口见 API_WORKFLOWS.md。

## 统一事件

所有事件包含 UUID `event_id`、UUID `run_id`、UUID `project_id`、`event_type`、可空 `stage`、可空 `agent_name`、`status`、UTC `timestamp`、类型化 `payload`。持久化事件的 `sequence` 为正整数；临时事件为 null。Pydantic 模型对额外字段拒绝，进度消息及错误消息使用白名单。六类 payload：

| event_type | payload |
| --- | --- |
| progress | `stage`、`agent_name`、`status`、白名单 `message` |
| approval_required | `checkpoint_id`、`approval_type`、`payload_ref` |
| text_delta | `chapter_id`、`delta`（最多 2048 字符）、正整数 `delta_sequence` |
| completed | `result_ref`、UTC `finished_at` |
| error | 固定 `error_code`、白名单 `safe_message`、`retryable` |
| heartbeat | UTC `timestamp` |

序列化示例：

```text
id: 12
event: progress
data: {"event_id":"11111111-1111-4111-8111-111111111111","sequence":12,"run_id":"22222222-2222-4222-8222-222222222222","project_id":"33333333-3333-4333-8333-333333333333","event_type":"progress","stage":"planning","agent_name":"Planner","status":"running","timestamp":"2026-11-04T08:00:00Z","payload":{"stage":"planning","agent_name":"Planner","status":"running","message":"Workflow running"}}

```

`progress`、`approval_required`、`completed`、`error` 持久化；`text_delta` 和 `heartbeat` 不写表、不含 SSE `id`，不能恢复重放或推进 Last-Event-ID。text_delta 的序号仅在章节流内有意义。文本生产者必须在发布前过滤敏感内容；当前只拒绝常见密钥或密码标记，无法自动识别所有秘密。当前没有真实模型文本生产者。

## 排序、续传与背压

持久化事件通过锁定对应 `workflow_runs` 行、读取当前最大序号并递增，在同一事务中插入；`(run_id, sequence)` 唯一约束提供数据库防线。状态和该状态对应的重要事件在同一 MySQL 事务提交后才广播。SSE 的 `id` 是该运行内的持久化 sequence；重连时 `Last-Event-ID` 必须是该 run 内已存在的序号（或 0），服务器补发更大的事件。

订阅先注册再读取数据库历史；通知只作为查询提示，持久化事件始终按数据库 sequence 输出，避免历史和实时的交界丢失或乱序。每个订阅队列上限 64，溢出时关闭流，客户端可从最后收到的持久化 id 重连。重复订阅互不影响；连接关闭释放订阅，不取消工作流。空闲每 3 秒发送心跳。临时 delta 不保证相对持久化事件的重放或跨生产者全序。

当前是本机单进程广播；每个订阅定期查询 MySQL（约每 250ms），仅适合低并发。多进程广播需专门设计并验证；不能依靠本进程队列跨进程投递。

## 双存储与故障

SQLite checkpoint 与 MySQL 事务不具原子性。若图可能已经写入 checkpoint 而后续状态/事件事务失败，后台仅记录固定安全日志、停止处理，不重跑节点；MySQL 可能仍显示 running，需检查对应线程的 checkpoint 与业务记录后人工或由未来对账机制处理。若之前 progress 已提交而后续状态更新失败，已提交事件仍保留，可能不是最新事实。若广播失败，已提交事件不回滚；数据库轮询或重连重放可读取它。若图开始前的执行失败，尽力以单个 MySQL 事务保存 failed 和安全 error；数据库本身不可用时只能记录安全日志。

没有用户身份认证、分布式事务、自动对账或跨进程队列；M2-07-02 的审核恢复使用同进程后台任务。本次测试使用临时 SQLite 文件和回滚隔离的 MySQL 数据；业务数据库表仍是最终内容的权威来源。

M2-07-02 的新尝试拥有独立事件序号；用户拒绝时发布状态为 `cancelled`、消息为 `Approval rejected` 的持久化 progress，不能当作 completed。继续从新 run 的 SSE 接口按 Last-Event-ID 重放。

M2-07-03 的审核决定、queued 运行和首条 progress 在同一事务提交；启动恢复产生的后续事件沿用该 run 独立 sequence 和既有 Last-Event-ID 规则。提交失败不会广播事件。

M2-07-04 起，首次运行的 queued 事件与 run 共同作为启动恢复依据；启动恢复复用原 run 的 sequence，running 尝试不自动重放。首次启动提交后若调度失败，响应可能为 503 且记录保留，调用者不得盲目重试写请求。
# M2-08-02 事件补充

真实 Planner 任务批准后，沿用 `progress` 类型及持久化 sequence，`status` 为 `paused`，`stage` 为 `planning`，消息为 `Planner approval complete; workflow paused`。该事件表示阶段暂停，不表示论文已完成。

