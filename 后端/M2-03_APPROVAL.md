# M2-03：离线审核和 SQLite 检查点

这是使用真实 LangGraph 中断与恢复的 Mock 工作流，不是可对外开放的审核 API。图的输入只接收 `project_id` 与 `current_stage`，避免新线程输入预先填入批准 ID。节点顺序：

```text
START → Planner → 任务审核 interrupt
                     ├─ reject → END
                     └─ approve → Research → 资料审核 interrupt
                                               ├─ reject → END
                                               └─ approve → Writing → Review → END
```

审核节点使用 `interrupt()` 发送 `{"type":"task_approval","task_ids":[...]}` 或 `{"type":"source_approval","source_ids":[...]}`。同一线程使用 `Command(resume=...)` 恢复：

- `{"decision":"approve","approved_ids":[...]}`：至少选择一个当前审核 ID，允许部分选择；ID 必须属于对应任务或资料集合，不允许重复。
- `{"decision":"reject"}`：拒绝并结束；也可显式传入空的 `approved_ids`。

其他结构、未知字段、非法决策、空批准列表、重复或不属于当前集合的 ID 均被拒绝；节点再次中断并在原审核信息中添加 `"error":"invalid_approval"`。调用者可以提交修正后的恢复值。错误提示不回显用户输入。

例外是 `Command(resume=None)`：当前安装的 LangGraph 将其视为没有提供恢复值，会在节点收到输入之前抛出 `UnboundLocalError`；检查点仍停在原审核点，之后可以提交有效值。调用方必须在构造 Command 前拒绝空值，未来 API 还需要将框架异常转换为安全错误。

## 生命周期示例

```python
from pathlib import Path
from langgraph.types import Command
from app.graph.approval_graph import build_approval_graph
from app.graph.checkpointer import sqlite_checkpointer

path = Path("local-checkpoints.sqlite")
config = {"configurable": {"thread_id": "unique-workflow-instance"}}
with sqlite_checkpointer(path) as saver:
    graph = build_approval_graph().compile(checkpointer=saver)
    graph.invoke({"project_id": "19f8b1b2-7dcc-44bf-92bb-dbc394c2173d", "current_stage": "planning"}, config)
    snapshot = graph.get_state(config)  # next == ("task_approval",)
    tasks = snapshot.values["tasks"]

# 进程可在此退出；下次使用同一个文件和同一个 thread_id。
with sqlite_checkpointer(path) as saver:
    graph = build_approval_graph().compile(checkpointer=saver)
    graph.invoke(Command(resume={"decision": "approve", "approved_ids": tasks[:1]}), config)
    snapshot = graph.get_state(config)  # next == ("source_approval",)
    sources = snapshot.values["source_ids"]
    graph.invoke(Command(resume={"decision": "approve", "approved_ids": sources}), config)
    final = graph.get_state(config)  # next == (), current_stage == "review"
```

`sqlite_checkpointer(Path(...))` 使用已安装的 `SqliteSaver.from_conn_string`，退出上下文即关闭连接，不创建全局单例；传入的父目录必须存在。测试使用临时目录中的文件，运行时 SQLite 文件由现有 `.gitignore` 排除。文件路径的权限、备份、并发和部署管理需由实际运行环境解决。

`thread_id` 标识同一工作流实例，跨暂停及进程重启保持不变；新工作流实例使用新的 thread_id，不能在已有线程直接调用初始输入来冒充新实例。它与每次执行尝试的 run_id 不同；M2-04 已实现运行记录，M2-07-01 的审核详情接口从经过项目范围验证的运行记录获取 thread_id，而不是接受客户端指定线程。错误 thread_id 不会读取其他线程；当前底层 LangGraph 对不存在的线程尝试恢复会抛出异常，恢复 API 在执行前查询并将不一致情况转换成安全错误。

中断所在节点恢复时会从开头重放，只有计算 payload 和校验，没有外部副作用。M2-02 模拟任务及资料 ID 是确定性的，测试也检查恢复时 Planner 和 Research 不会重新执行。无效输入将被 LangGraph 保存为恢复历史的一部分，因此调用方不应在 resume 中传密钥、论文正文或任意自由文本。

## 当前边界

本图只验证 Mock 数据中的 ID 成员关系；没有用户身份认证、完整业务审核记录或业务数据库与 checkpoint 的原子性。M2-07-03 已持久化流程恢复所需的审核决定。M2-07-01 增加审核详情 GET；M2-07-02 增加同路径 POST，经范围校验后创建新的运行尝试，在进程内后台恢复原线程。真实审核接入时必须先验证用户、项目、线程和当前审核项，限制并验证 resume 的完整结构，将业务决策持久化，并控制重放、重复提交和并发恢复。不能把任意客户端传入的 LangGraph `Command`、`update` 或 `goto` 直接交给该图。现阶段没有 Review 后的人工审核节点，Review 完成不等于用户接受论文。
