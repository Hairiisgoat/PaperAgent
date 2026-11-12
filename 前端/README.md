# 前端基础设施

当前提供项目列表、创建、详情、编辑以及归档、恢复和软删除交互。

## 工作流启动与监控

项目详情允许 active 项目启动 Mock 工作流或显式启动真实 Planner；提交期间禁止重复点击，网络中断或超时不会自动重发 POST。成功后打开 `/projects/:projectId/runs/:runId`。该页面查询服务端状态并通过 SSE 展示事件；等待审核时可按实际任务或资料决定批准或拒绝。论文工作台支持阅读、引用详情、章节版本和审查。

项目详情通过项目范围运行列表查询最近最多 50 次运行，并为列表中已完成的写作提供入口；sessionStorage 仅辅助记录当前会话最近运行。监控页按项目与 run ID 保存最近 100 条持久化事件及其游标，刷新后发送 Last-Event-ID；临时文本与心跳不持久化，也不保证重放。存储不可用时将从头读取事件。连接中断会有限次退避重连；4xx 停止自动重连，无效游标可由用户选择从头加载。离开页面只关闭订阅，不取消后端运行。

`node --test tests/workflow-monitor.browser.mjs` 使用已有 Playwright 和 Chrome、Mock HTTP/SSE 校验启动、流解析、断线、刷新和窄屏；所有写操作被拦截。`tests/workflow-isolated-e2e.browser.mjs` 默认跳过，显式设置 `PAPERAGENT_ISOLATED_E2E=1` 后在临时 SQLite 业务库与临时 SQLite Checkpointer 上运行真实 FastAPI、Vite 和浏览器，进程内禁止 MySQL 连接，不写入开发 MySQL。两项测试的 Playwright 和 Chrome 路径配置与下方浏览器测试相同。

## 开发与代理

在本目录执行 `npm ci` 恢复锁定依赖，`npm run dev` 启动开发服务器，`npm run build` 执行类型检查及生产构建。开发服务器将 `/api` 代理到 `http://127.0.0.1:8000`；复制 `.env.example` 为本地 `.env` 可设置 `VITE_API_PROXY_TARGET`，重启 Vite 后生效。该变量仅供开发代理选择目标，`VITE_` 变量不适合保存密码、API Key 或其他秘密。生产环境的 API 转发需要单独配置。History 路由在生产环境需要服务器回退至 `index.html`，且不得将 `/api` 请求回退到前端页面。

## HTTP

`src/services/http.ts` 提供 `http`，基础路径 `/api`，超时 15 秒，不实现自动重试。普通请求可传 Axios `signal`（AbortController）取消。错误均以 `HttpError` 拒绝，`kind` 为 `cancelled`、`timeout`、`network`、`http` 或 `unknown`，`status` 仅在有 HTTP 响应时存在；`message` 是固定安全提示。取消也会拒绝 Promise，应由调用方根据 `kind` 判断。错误对象不包含原始响应、URL、请求配置或驱动详情；创建项目的 422 字段错误仅保留白名单字段和固定提示，不保留原始输入或服务端消息。

## 图标规范

从 `@element-plus/icons-vue` 按需具名导入需要的图标，例如 `import { Edit } from '@element-plus/icons-vue'`；不要全局注册整个图标包。操作图标应配合可见文字或无障碍名称。Element Plus 组件和对应样式均通过显式导入按需使用，不全局注册。

## 项目列表验证

浏览器测试使用模拟响应覆盖加载、分页、筛选、取消和错误；可通过 `PAPERAGENT_REAL_BACKEND=1` 增加只读真实后端检查。测试不包含项目写请求。运行测试需要已有的 Playwright 和 Chromium/Chrome，本项目没有为此新增依赖；可用 `PAPERAGENT_PLAYWRIGHT_ENTRY` 指向已有 Playwright 的 `index.mjs`，用 `PAPERAGENT_CHROME_PATH` 指向已有浏览器，再执行 `node --test tests/project-list.browser.mjs`。默认前端地址为 `http://127.0.0.1:15173`，可通过 `PAPERAGENT_FRONTEND_URL` 调整；测试前需启动 Vite，真实后端检查还需启动 FastAPI。
## 创建与详情验证

`node --test tests/project-create-detail.browser.mjs` 使用浏览器拦截模拟创建与详情请求；测试 POST 不会访问真实后端。可设置 `PAPERAGENT_REAL_BACKEND=1` 增加只读 404 联调。Playwright 和浏览器路径的设置与项目列表验证相同。创建表单不显示或发送 `requirements`；详情以只读方式显示该 JSON 配置。
## 编辑与生命周期验证

`node --test tests/project-edit-lifecycle.browser.mjs` 通过浏览器拦截模拟 PATCH、归档、恢复和删除；所有写请求均被拦截，不访问真实后端。Playwright 和浏览器路径与项目列表验证相同。失败且结果不确定时，页面只自动重新读取，不自动重复写入；编辑草稿在当前页面保留，确认读取最新状态后才可继续。

## 真实端到端验收

涉及现有论文的 `workspace-entry-real.browser.mjs`、`workspace-real.browser.mjs`、`workspace-write-mocked.browser.mjs` 和 `export-real.browser.mjs` 默认不运行；显式启用时需自行设置 `PAPERAGENT_TEST_PROJECT_ID`、`PAPERAGENT_TEST_RUN_ID`，章节修改模拟测试还需 `PAPERAGENT_TEST_CHAPTER_ID`。测试用例对目标论文的章节数量、历史版本和审查状态有前置条件；其中修改和恢复请求由浏览器拦截，不会写入实际数据库。请勿将真实项目 ID 写回测试文件。

`tests/project-real-e2e.browser.mjs` 是独立的真实写入测试，默认跳过。仅在获得明确授权后运行：先确认 `http://127.0.0.1:15173` 的 Vite 代理指向 `http://127.0.0.1:8000`，且 FastAPI 使用本机 `paper_agent` 和 `paper_agent_app`。测试会核验数据库和专用账号，再创建一条唯一标识项目，完成浏览器闭环后通过 API 软删除；**数据库中会永久保留一条软删除记录**。不要将此测试加入默认测试或自动重试。

设置已有 Playwright 和 Chrome 的路径后，在 PowerShell 中执行：

```powershell
$env:PAPERAGENT_REAL_WRITE_E2E = '1'
node --test tests/project-real-e2e.browser.mjs
Remove-Item Env:PAPERAGENT_REAL_WRITE_E2E
```

创建结果不确定时，脚本按唯一标题分页查找；写入结果不确定时不会自动重试。若清理或查找失败，应根据输出中的标题、UUID 以及服务端状态人工核实，不要物理删除记录。


## 人工审核

运行等待人工审核时，页面读取审核详情；真实任务和资料显示可用内容，Mock 对象显示 ID。批准可选择部分或全部；拒绝需要确认。提交前在 sessionStorage 保留决定，超时、网络中断、503 或未知结果会锁定选择，只允许手动重试相同决定；应先核对服务端状态。成功后进入返回的新 run，事件游标按 run 隔离。会话存储不可用时不提交，以免丢失不确定结果的重试信息。

`PAPERAGENT_ISOLATED_E2E=1 node --test tests/approval-isolated.browser.mjs` 启动临时 SQLite 业务库、临时 Checkpointer、真实 FastAPI/Vite 和本机 Chrome，禁止 MySQL 连接；测试包含任务与资料审核、刷新和窄屏。
