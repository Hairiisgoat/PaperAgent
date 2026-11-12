# PaperAgent V1 API 接口说明书

本表从当前 FastAPI OpenAPI 生成。基础地址 `/api`；完整可执行 Schema 和示例以 `/docs`、`/openapi.json` 为准。错误会使用安全提示；具体 404、409、422、503 见对应操作。项目范围隔离不代表用户鉴权。

## 常见调用示例

创建项目：`POST /api/projects`，请求体 `{"title":"课程论文","topic":"软件工程","citation_style":"APA"}`，成功返回 201 及项目响应。真实 Planner 使用 `POST /api/projects/{project_id}/planner-runs`，返回 202 及运行和线程 ID；Mock 使用 `/runs`。不要在响应不确定时自动重发。

通过 `GET /api/projects/{project_id}/workflow-runs/{run_id}/approval` 获取等待审核对象，提交 `POST` 同一路径：批准请求至少包含 `{"decision":"approve","approved_ids":["<审核对象UUID>"]}`；拒绝为 `{"decision":"reject"}`。特定阶段的继续选项及限制以交互契约 `后端/API_WORKFLOWS.md` 和 OpenAPI 为准。成功返回新运行尝试；不得假定已经完成。

事件接口为 `GET /api/projects/{project_id}/workflow-runs/{run_id}/events`，持久化事件支持 `Last-Event-ID` 续传，临时文本与心跳不保证重放。论文读取为 `GET .../draft`；文件导出为 `GET .../export/{kind}`，其中 `kind` 为 `docx`、`pdf` 或 `review-docx`，后者需要当前版本匹配的完整报告。

## POST /api/projects

Create Project

- 请求体 Schema：`[{"$ref":"#/components/schemas/ProjectCreate"}]`。
- HTTP 响应：201, 503, 422。

## GET /api/projects

List Projects

- query 参数 `page`；可选；类型 `integer`。
- query 参数 `page_size`；可选；类型 `integer`。
- query 参数 `lifecycle_status`；可选；类型 `见 Schema`。
- HTTP 响应：200, 503, 422。

## GET /api/projects/{project_id}

Get Project

- path 参数 `project_id`；必填；类型 `string`。
- HTTP 响应：200, 404, 503, 422。

## PATCH /api/projects/{project_id}

Update Project

- path 参数 `project_id`；必填；类型 `string`。
- 请求体 Schema：`[{"$ref":"#/components/schemas/ProjectUpdate"}]`。
- HTTP 响应：200, 404, 409, 503, 422。

## DELETE /api/projects/{project_id}

Delete Project

- path 参数 `project_id`；必填；类型 `string`。
- HTTP 响应：204, 404, 409, 503, 422。

## POST /api/projects/{project_id}/archive

Archive Project

- path 参数 `project_id`；必填；类型 `string`。
- HTTP 响应：200, 404, 409, 503, 422。

## POST /api/projects/{project_id}/planner-runs

Start Planner

- path 参数 `project_id`；必填；类型 `string`。
- HTTP 响应：202, 422。

## POST /api/projects/{project_id}/restore

Restore Project

- path 参数 `project_id`；必填；类型 `string`。
- HTTP 响应：200, 404, 409, 503, 422。

## POST /api/projects/{project_id}/runs

Start Mock

- path 参数 `project_id`；必填；类型 `string`。
- HTTP 响应：202, 422。

## GET /api/projects/{project_id}/workflow-runs

List Workflow Runs

- path 参数 `project_id`；必填；类型 `string`。
- HTTP 响应：200, 404, 503, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}

Get Workflow Run

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- HTTP 响应：200, 404, 503, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}/approval

Get Workflow Approval

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- HTTP 响应：200, 404, 503, 409, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/approval

Submit Workflow Approval

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- 请求体 Schema：`[{"$ref":"#/components/schemas/ApprovalSubmission"}]`。
- HTTP 响应：202, 404, 503, 409, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}/draft

Get Draft

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- HTTP 响应：200, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/draft/chapters/{chapter_id}/revisions

Revise Chapter

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- path 参数 `chapter_id`；必填；类型 `string`。
- 请求体 Schema：`[{"$ref":"#/components/schemas/RevisionSubmission"}]`。
- HTTP 响应：202, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}/draft/chapters/{chapter_id}/revisions/{revision_id}

Get Revision

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- path 参数 `chapter_id`；必填；类型 `string`。
- path 参数 `revision_id`；必填；类型 `string`。
- HTTP 响应：200, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}/draft/chapters/{chapter_id}/versions

List Chapter Versions

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- path 参数 `chapter_id`；必填；类型 `string`。
- HTTP 响应：200, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/draft/chapters/{chapter_id}/versions/{version_id}/restore

Restore Chapter Version

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- path 参数 `chapter_id`；必填；类型 `string`。
- path 参数 `version_id`；必填；类型 `string`。
- 请求体 Schema：`[{"$ref":"#/components/schemas/RestoreSubmission"}]`。
- HTTP 响应：200, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}/events

Stream Events

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- header 参数 `Last-Event-ID`；可选；类型 `见 Schema`。
- HTTP 响应：200, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}/export/{kind}

Export Document

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- path 参数 `kind`；必填；类型 `string`。
- HTTP 响应：200, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/reviews

Start Review

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- 请求体 Schema：`[{"$ref":"#/components/schemas/ReviewRequest"}]`。
- HTTP 响应：202, 422。

## GET /api/projects/{project_id}/workflow-runs/{run_id}/reviews

List Reviews

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- HTTP 响应：200, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/reviews/assemble

Assemble

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- 请求体 Schema：`[{"$ref":"#/components/schemas/AssembleRequest"}]`。
- HTTP 响应：201, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/reviews/{report_id}/retry

Retry

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- path 参数 `report_id`；必填；类型 `string`。
- HTTP 响应：202, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/segmented-retry

Retry Segmented Writing

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- HTTP 响应：202, 404, 503, 409, 422。

## POST /api/projects/{project_id}/workflow-runs/{run_id}/writing-retry

Retry Writing

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `run_id`；必填；类型 `string`。
- HTTP 响应：202, 404, 503, 409, 422。

## GET /api/projects/{project_id}/workflows/{thread_id}

Get Workflow

- path 参数 `project_id`；必填；类型 `string`。
- path 参数 `thread_id`；必填；类型 `string`。
- HTTP 响应：200, 404, 503, 422。

## GET /health

Health Check

- HTTP 响应：200。

## Schema 字段索引

- **ApprovalSource**：`source_id`（必填）、`title`（必填）、`authors`（必填）、`year`（必填）、`journal`（必填）、`doi`（必填）、`url`（必填）、`abstract`（必填）、`task_ids`（必填）、`tasks`（必填）、`relevance_hint`、`same_title_count`。
- **ApprovalSourceTask**：`task_id`（必填）、`title`（必填）。
- **ApprovalSubmission**：`decision`（必填）、`approved_ids`、`continue_research`、`continue_writing`、`segmented_writing`。
- **ApprovalSubmissionResponse**：`project_id`（必填）、`previous_run_id`（必填）、`run_id`（必填）、`thread_id`（必填）、`attempt_no`（必填）、`status`。
- **ApprovalTask**：`task_id`（必填）、`title`（必填）、`description`（必填）、`expected_output`（必填）。
- **AssembleRequest**：`report_id`（必填）。
- **ChapterVersionResponse**：`version_id`（必填）、`chapter_id`（必填）、`version_no`（必填）、`kind`（必填）、`parent_version_id`（必填）、`created_at`（必填）、`paragraphs`（必填）。
- **DraftChapterResponse**：`chapter_id`（必填）、`position`（必填）、`title`（必填）、`objective`（必填）、`status`（必填）、`paragraphs`（必填）。
- **DraftCitation**：`source_id`（必填）、`title`（必填）、`authors`（必填）、`year`（必填）、`doi`（必填）、`url`（必填）、`evidence`（必填）。
- **DraftParagraphResponse**：`text`（必填）、`citations`（必填）。
- **DraftResponse**：`project_id`（必填）、`run_id`（必填）、`draft_id`（必填）、`status`（必填）、`chapters`（必填）、`supports_versions`。
- **ErrorResponse**：`detail`（必填）。
- **HTTPValidationError**：`detail`。
- **ProjectCreate**：`title`（必填）、`topic`（必填）、`citation_style`（必填）、`paper_type`、`requirements`。
- **ProjectPage**：`items`（必填）、`total`（必填）、`page`（必填）、`page_size`（必填）。
- **ProjectResponse**：`id`（必填）、`user_id`（必填）、`title`（必填）、`paper_type`（必填）、`topic`（必填）、`requirements`（必填）、`citation_style`（必填）、`lifecycle_status`（必填）、`progress_status`（必填）、`created_at`（必填）、`updated_at`（必填）、`archived_at`（必填）、`deleted_at`（必填）。
- **ProjectUpdate**：`title`、`topic`、`citation_style`、`paper_type`、`requirements`。
- **ReportResponse**：`report_id`（必填）、`project_id`（必填）、`run_id`（必填）、`status`（必填）、`created_at`（必填）、`context_versions`（必填）、`context_stale`（必填）、`units`（必填）。
- **RestoreSubmission**：`request_id`（必填）。
- **ReuseProvenance**：`report_id`（必填）、`unit_id`（必填）、`call_id`（必填）。
- **ReviewIssue**：`issue_type`（必填）、`severity`（必填）、`chapter_id`（必填）、`version_id`（必填）、`paragraph_index`（必填）、`description`（必填）、`rationale`（必填）、`suggestion`（必填）、`source_ids`（必填）。
- **ReviewOutput**：`issues`（必填）、`summary`（必填）。
- **ReviewRequest**：`report_id`（必填）、`chapter_id`。
- **RevisionResponse**：`revision_id`（必填）、`chapter_id`（必填）、`base_version_id`（必填）、`result_version_id`（必填）、`mode`（必填）、`status`（必填）、`error_code`（必填）、`created_at`（必填）。
- **RevisionSubmission**：`revision_id`（必填）、`base_version_id`（必填）、`mode`（必填）、`instruction`（必填）。
- **StartRunResponse**：`project_id`（必填）、`thread_id`（必填）、`run_id`（必填）、`status`（必填）。
- **UnitResponse**：`chapter_id`（必填）、`chapter_title`（必填）、`version_id`（必填）、`version_no`（必填）、`stale`（必填）、`status`（必填）、`error_code`（必填）、`result`（必填）、`reused_from`。
- **ValidationError**：`loc`（必填）、`msg`（必填）、`type`（必填）、`input`、`ctx`。
- **WorkflowApprovalResponse**：`project_id`（必填）、`run_id`（必填）、`thread_id`（必填）、`approval_type`（必填）、`checkpoint_id`（必填）、`current_stage`（必填）、`item_ids`（必填）、`items`、`sources`。
- **WorkflowRunResponse**：`run_id`（必填）、`thread_id`（必填）、`project_id`（必填）、`attempt_no`（必填）、`status`（必填）、`current_stage`（必填）、`started_at`（必填）、`finished_at`（必填）、`created_at`（必填）、`updated_at`（必填）、`error_code`（必填）、`retryable`（必填）。
- **WorkflowStatusResponse**：`project_id`（必填）、`thread_id`（必填）、`latest_run_id`（必填）、`status`（必填）、`current_stage`（必填）、`updated_at`（必填）、`error_code`（必填）、`retryable`（必填）。
