# PaperProject API 契约（M1-04）

本接口不实现身份认证；请求中不允许设置 `user_id`。所有时间响应为 UTC。

## 创建

`POST /api/projects`，成功返回 201 和项目对象。

```json
{"title":"课程论文","topic":"人工智能的应用","paper_type":"course_paper","citation_style":"GB_T_7714","requirements":{"word_count":3000}}
```

`title`、`topic`、`citation_style` 必填；`paper_type` 默认 `course_paper`，接受 `course_paper`、`thesis`、`literature_review`、`research_paper`。当前工作流只规划课程论文，接受类型并不意味着其他工作流已实现。`requirements` 是 JSON 对象或 null，可省略。引用格式为非空且不超过 40 字符的字符串；GB_T_7714、APA、IEEE 是现有候选，不表示最终限定列表。

## 查询

- `GET /api/projects?page=1&page_size=20&lifecycle_status=active`：参数 `page` 从 1 开始，`page_size` 为 1–100；状态可省略或为 `active`、`archived`。默认包含未删除的两种状态，按创建时间、ID 降序排序。
- 返回 `{"items":[项目对象],"total":1,"page":1,"page_size":20}`。已软删除项目不通过普通列表返回。
- `GET /api/projects/{project_id}` 返回项目对象；不存在或已删除返回 404。路径 UUID 无效返回 422。

项目对象包含 `id`、`user_id`、`title`、`paper_type`、`topic`、`requirements`、`citation_style`、`lifecycle_status`、`progress_status`、`created_at`、`updated_at`、`archived_at`、`deleted_at`。

## 修改及状态变更

- `PATCH /api/projects/{project_id}` 可修改 `title`、`topic`、`paper_type`、`citation_style`、`requirements`；至少提供一项。仅 `requirements` 可以显式为 null。未知字段、身份和状态字段会返回 422。
- `POST /api/projects/{project_id}/archive`：active → archived；`POST /api/projects/{project_id}/restore`：archived → active。成功返回更新后的项目对象。
- 归档项目不可 PATCH；不符合状态转换返回 409。恢复时清空 `archived_at`。
- `DELETE /api/projects/{project_id}` 对未删除项目执行软删除，仅更新 `lifecycle_status`、`deleted_at`；成功返回 204 空响应。删除后普通详情与恢复返回 404。

业务错误示例：`{"detail":"Project not found"}`（404）、`{"detail":"Only archived projects can be restored"}`（409）、`{"detail":"Database unavailable"}`（503）。FastAPI 请求验证错误使用 422 和标准验证错误格式。非连接类数据库错误及未预期内部错误返回 500，不暴露内部详情。完整机器可读契约为运行服务后的 `/openapi.json`，交互文档为 `/docs`。
