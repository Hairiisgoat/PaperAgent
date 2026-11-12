# M2-04 工作流运行记录和只读接口

目前提供 Mock 启动、状态查询、审核详情读取与审核提交接口；审核提交在后台恢复现有 Mock 图。

## ID 和事务

- 新工作流使用 `WorkflowRunService.create(project_id)`：校验并锁定 active 项目，生成独立 UUID 的 `thread_id`、`run_id`，创建 `queued` 第一次尝试。
- 恢复使用 `create(project_id, resume_thread_id=thread_id)`：在同一项目范围内锁定并读取最新尝试，仅允许从 `waiting_human`、`paused` 或 `retryable=true` 的 `failed` 继续。复用线程，生成新的 run UUID 和递增 `attempt_no`。旧尝试不可转换为 completed；最新尝试按 attempt_no 排序。
- 同一项目的创建/恢复由父项目行锁串行化；`(thread_id, attempt_no)` 唯一，`(project_id, thread_id, attempt_no)` 索引用于范围查询。新线程仅由服务生成 UUID、首次编号为 1，恢复只接受本项目已有线程。因此通过本服务不能跨项目复用线程；数据库本身没有单独的线程归属表，不能防止有直接写权限的代码手工插入同线程、不同编号、不同项目的记录。禁止绕过服务直接写入。
- 运行入口必须在调用图前后显式使用 `transition` 记录状态；当前 Mock 启动与审核恢复使用进程内后台任务；审核恢复意图持久化并在服务启动时扫描，没有分布式队列或自动对账。调用方在同一 Session 内负责提交或回滚，不能在项目范围校验后提前释放锁。
- `runnable_config_for_attempt(run)` 将应用层 `run_id`（UUID）显式设为当前 LangChain `RunnableConfig.run_id`（UUID），并附加 `paperagent_run_id`、`paperagent_project_id` 字符串元数据；`configurable.thread_id` 为线程 UUID 字符串。每次执行或恢复都必须创建新尝试和新配置，不能沿用旧 run_id。测试验证已安装版本的根调用回调确实收到同一 UUID；下游回调可有各自运行 ID。

## 状态和转换

每条记录是一段执行尝试，`waiting_human` 表示该尝试已结束并等待用户，下一次恢复是新记录。所有终止状态均记录 `finished_at`；`running` 记录 `started_at`。

| 状态 | 含义 | 允许下一状态 |
| --- | --- | --- |
| queued | 新尝试已创建，尚未开始 | running、cancelled |
| running | 尝试正在执行 | waiting_human、paused、failed、completed、cancelled |
| waiting_human | 因人工审核中断，该次尝试结束 | 无；恢复创建新尝试 |
| paused | 非人工审核的暂停，该次尝试结束 | 无；恢复创建新尝试 |
| failed | 失败，该次尝试结束 | 无；仅 retryable 时可新建重试尝试 |
| completed | 该次流程完成 | 无 |
| cancelled | 该次尝试取消 | 无 |

重复提交完全相同的状态更新为幂等；同状态但提供不同阶段或错误信息时返回冲突。阶段可省略，省略时保留现值；新恢复尝试初始继承前一尝试的阶段。只有 failed 能携带受限的错误代码和布尔 retryable，异常原文不得写入。暂停、取消只记录明确的调用方决策，不会自行暂停或取消正在执行的图。

## 项目范围内只读 API

`GET /api/projects/{project_id}/workflow-runs` 返回该项目最近最多 50 条运行尝试，按创建时间及运行 ID 倒序；已删除或不存在的项目返回 404。它用于在新浏览器会话中发现已有运行，不代表用户身份认证或授权。前端可选择已完成的写作运行进入论文工作台。

`GET /api/projects/{project_id}/workflows/{thread_id}` 返回按 attempt_no 排序的最新记录：

```json
{
  "project_id": "11111111-1111-4111-8111-111111111111",
  "thread_id": "22222222-2222-4222-8222-222222222222",
  "latest_run_id": "33333333-3333-4333-8333-333333333333",
  "status": "waiting_human",
  "current_stage": "planning",
  "updated_at": "2026-11-04T08:00:00Z",
  "error_code": null,
  "retryable": null
}
```

`GET /api/projects/{project_id}/workflow-runs/{run_id}` 返回单次记录，包括上述身份/状态字段（使用 `run_id` 而非 `latest_run_id`）、`attempt_no`、`started_at`、`finished_at`、`created_at` 和 `updated_at`。时间为 UTC；可空时间、错误代码及阶段在尚无值时返回 null。响应不包含 SQLite 路径或 checkpoint 内容。

项目和线程/运行 ID 同时用于查询；不存在、软删除或跨项目均为 404，UUID 格式错误为 422，已知数据库连接故障为 503，未预期内部故障为 500。归档项目允许只读查询。当前无用户认证，上述项目范围校验不等于用户授权。

### 读取当前人工审核

`GET /api/projects/{project_id}/workflow-runs/{run_id}/approval` 只读取该线程**最新尝试**的待审核信息，客户端不提供 thread_id：

```json
{
  "project_id": "11111111-1111-4111-8111-111111111111",
  "run_id": "22222222-2222-4222-8222-222222222222",
  "thread_id": "33333333-3333-4333-8333-333333333333",
  "approval_type": "task_approval",
  "checkpoint_id": "44444444-4444-4444-8444-444444444444",
  "current_stage": "planning",
  "item_ids": ["55555555-5555-4555-8555-555555555555"]
}
```

`approval_type` 为 `task_approval` 或 `source_approval`，`item_ids` 分别是任务或来源的 UUID 列表。当前 Mock 中断只产生 ID，没有名称或描述，接口不虚构展示信息；后续应在业务表可用后按项目和 ID 查询展示数据。

服务先检查项目和运行，再核对当前尝试、最新持久化审核事件、SQLite 最新 checkpoint 的线程/项目/运行元数据及 checkpoint ID，并检查中断节点、类型、阶段和非空唯一 ID 列表。读取使用只读 SQLite 连接，不推进 checkpoint。不存在、已删除和跨项目返回 404；非 waiting_human、历史尝试、缺失或不一致的事件/checkpoint、中断无效返回 409；SQLite 不可读或损坏及数据库不可用返回安全 503。路径和内部异常不返回客户端。

服务对项目、运行和事件使用短事务锁，与现有服务的项目锁顺序一致，防止服务层并发创建下一尝试时读取过期结果。SQLite 与 MySQL 仍不具备跨库原子性；绕过服务直接修改 checkpoint 或数据库时，必须进行对账。只读接口本身不提交决策；系统尚无用户级认证。

## 跨存储一致性

SQLite 保存 State、中断和恢复位置；MySQL 只保存执行尝试状态和安全错误分类。二者没有原子事务：

- SQLite checkpoint 已保存、MySQL 更新失败：查询可能显示旧状态或 running，而图已暂停或结束。
- MySQL 状态已更新、SQLite 保存失败：查询可能显示 waiting_human 或 completed，但相应 checkpoint 不存在或未推进。

执行入口必须明确提交点，对失败保留安全记录并在恢复前核对线程、最新 checkpoint 和业务记录。服务本身不解决双写一致性、分布式事务、异常重试或恢复对账；在这些机制实现前，不应把单一存储的状态视为跨存储原子事实。


## 提交人工审核并恢复 Mock 图（M2-07-02）

`POST /api/projects/{project_id}/workflow-runs/{run_id}/approval` 对线程最新的 `waiting_human` 尝试提交一次决定：

```json
{"decision":"approve","approved_ids":["55555555-5555-4555-8555-555555555555"]}
```

或 `{"decision":"reject"}`。批准必须提供非空、唯一、属于当前中断的 UUID；拒绝不允许附带 approved_ids。未知字段和非法格式返回 422，审核项不属于当前集合、旧尝试或状态/checkpoint 不匹配返回 409，项目或运行不在范围内返回 404，存储或调度不可用返回 503。归档项目不能提交。

接受时返回 202，例如：

```json
{"project_id":"11111111-1111-4111-8111-111111111111","previous_run_id":"22222222-2222-4222-8222-222222222222","run_id":"66666666-6666-4666-8666-666666666666","thread_id":"33333333-3333-4333-8333-333333333333","attempt_no":2,"status":"queued"}
```

返回后通过新 run_id 查询状态或订阅事件。项目行、旧 run 行及最新尝试在同一事务中锁定，核对事件与 checkpoint 后创建 queued 新尝试和持久化审核决定，旧 waiting_human 不变。相同决定和相同 ID 集合重试返回原 202 结果（即使新 run 已结束）；不同决定或不同 ID 集合返回 409。previous_run_id 主键和新 run_id 唯一约束为第二道防线。后台先以数据库行锁将 queued 转 running，重复任务不会重复执行，再次验证旧 checkpoint 后用原 thread_id 和新 run_id 的配置执行 `Command(resume=...)`。任务批准后进入资料审核，资料批准后可进入 Review 并 completed；拒绝走图的结束分支并标记 cancelled，发出 progress 事件 `Approval rejected`。

SQLite 与 MySQL 不是同一事务。图调用后失败时不自动重放，尽力保存 failed、非重试错误及固定安全日志，需要对账 checkpoint 和运行记录。通知失败不会重新运行图。调度注册发生在提交前，注册失败回滚。提交后进程退出可能留下 queued；服务启动时扫描已持久化决定，使用受控后台线程执行，具体过程仍需核对运行关系和 checkpoint。后台开始后若项目被归档或数据库不可用，状态更新也可能需要对账。SQLite 被绕过服务直接修改、多个应用进程或外部恢复程序时，不承诺跨进程执行互斥。


## M2-07-03 持久化和启动恢复

新增 `workflow_approval_decisions`：previous_run_id（主键）、run_id（唯一）、project_id、thread_id、checkpoint_id、approval_type、decision、approved_ids、execution_status、submitted_at。该行与新运行及 queued 事件在同一个 MySQL 事务提交；拒绝的 approved_ids 为 []。状态 queued 表示等待调度或执行，running 与 run 的 running 同事务提交，finished 与中断/完成/取消事件同事务提交，needs_review 表示安全失败、需要对账。事件只在提交后广播。

FastAPI lifespan 启动时扫描 queued 和 running 决定。queued 与其 run 的项目、线程、状态先核对后提交到本进程单线程执行器；实际任务通过项目与运行锁领取，并重新核对旧 run、最新尝试、持久化审核事件、原 checkpoint、审核类型和批准集合，然后才执行 Command。重复扫描由本进程待执行集合去重，DB 行锁和状态检查提供第二层保护。调度失败保留 queued 并记录安全日志，下次启动可再扫描。

running 状态不会自动重新执行：即使 checkpoint 未变化，也不能证明图或外部操作未开始；checkpoint 变化、缺失或不可读也只记录固定诊断分类并保留 running，供人工对账。记录或 checkpoint 不一致、执行失败时不伪造成功；能安全更新时标记非重试 failed / needs_review。MySQL 和 SQLite 无原子事务，进程在调用图后退出仍可能造成 running 与 checkpoint 状态不一致。

M2-07-04 起也恢复符合严格校验的首次 Mock 启动 queued 记录。启动扫描或数据库不可用时记录日志，不自动周期重试；恢复需重启服务或由后续运维机制触发扫描。没有多进程协调、分布式任务队列、用户认证或自动对账。归档/删除项目、人工改写数据或数据库不可用可能使任务不能执行，需要人工处理。审核记录是流程决定，不等于完整的业务审核记录。


## M2-07-04 首次启动恢复

首次运行的持久意图由 `workflow_runs` 的 project_id、thread_id、run_id、attempt_no=1、current_stage=planning、status=queued 和同事务的唯一首条 queued progress 事件共同表示，不新增表或字段。初始图输入固定为项目 ID 和 planning 阶段；未来增加输入时必须明确其持久化和版本兼容方案。

启动扫描将首次 queued 尝试交给原有单进程调度器。执行者在项目行与 run 行锁内检查项目 active、运行范围、最新尝试、无审核恢复记录、首条事件一致以及该线程没有已有 checkpoint；SQLite 文件不存在表示无 checkpoint，已有文件不可读或线程已有 checkpoint 时安全停止并记录固定诊断。只有将 running 状态和事件成功提交后才调用图，沿用原 run_id、thread_id。重复调度通过 run 状态锁阻止重复执行。

首次 running 记录启动时只记录需要人工对账的固定提示，不能因 checkpoint 缺失就假设图没有开始。MySQL 和 SQLite 不具原子事务；调用图后异常或进程退出可能留下 running。进程内调度失败会保留已提交 queued，供下次启动扫描；如启动接口在提交后调度失败返回 503，客户端不知道 run_id，**不得盲目自动重试 POST**，以免创建另一线程。启动扫描只运行一次，数据库不可用或项目归档、删除时需要后续重启或人工处理。只支持单进程，不能依赖此机制处理多进程或外部进程直接修改 checkpoint。
# M2-08-02 实施补充

`POST /api/projects/{project_id}/runs` 继续启动 Mock。`POST /api/projects/{project_id}/planner-runs` 显式启动真实 Planner，返回 202 和现有 `project_id`、`thread_id`、`run_id`、`status` 格式；此操作会调用模型并可能产生费用，调用者不可对结果未知的请求自动重试。只支持 active 的课程论文，非空 requirements 或超长 topic 返回 422。模型配置不可用返回 503。

真实 Planner 的 `GET /api/projects/{project_id}/workflow-runs/{run_id}/approval` 保留原有 `item_ids`，并额外返回 `items`，每项含 `task_id`、`title`、`description`、`expected_output`。Mock 响应不增加 `items`。审核 POST 保持原协议和幂等语义：批准后新 run 在 Planner 边界暂停（`paused`），拒绝为 `cancelled`；不执行 Research、Writing 或 Review。

## M2-09 资料审核扩展

原审核请求保持原有默认行为。对于 `planner_real_v1` 的任务批准，可显式增加 `"continue_research": true`，在新 run 中选择 `planner_research_v2` 并检索所选任务；旧 run 的版本不变。不可对拒绝请求或其他图使用该选项，同一决定的重试必须保留该选项。新表未部署时接口拒绝提交，不消费审核决定。真实资料审核 GET 追加 `sources`，字段及安全边界见 [M2-09_RESEARCH.md](M2-09_RESEARCH.md)。资料审核可部分批准、全部批准或拒绝；批准后暂停，拒绝后取消，不运行 Writing 或 Review。仅支持单进程调度；崩溃后可能需要人工对账。

