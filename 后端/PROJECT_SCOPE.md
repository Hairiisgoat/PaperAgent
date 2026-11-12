# 项目范围校验约定（M1-05）

`app.services.project_scope.ProjectScope` 在当前业务 Session 内提供统一校验；调用方必须显式指定操作类型。它只验证项目范围和生命周期，不提供用户身份认证或多用户授权。

## 项目访问

`require_project(project_id, action=...)` 使用同一个 `ProjectRepository` 查询父项目，排除软删除项目。不存在或已删除抛出 `ProjectNotFound`（HTTP 404）。允许的操作如下：

| 状态 | 读取 | 编辑 | 归档 | 恢复 | 软删除 |
| --- | --- | --- | --- | --- | --- |
| active | 是 | 是 | 是 | 否 | 是 |
| archived | 是 | 否 | 否 | 是 | 是 |
| deleted | 404 | 404 | 404 | 404 | 404 |

不允许的状态操作抛出 `ProjectConflict`（HTTP 409）。除读取外，查询使用 `SELECT ... FOR UPDATE`；调用方须在同一 Session 和事务内完成后续操作并提交或回滚，不能在校验后先提交再修改。

## 未来子资源

未来每个项目级资源表必须映射 `id` 和 `project_id`。服务读取或修改单条资源时，可使用 `require_resource(Model, project_id, resource_id, access="read" 或 "write")`：

1. 校验父项目存在且允许读取或编辑；写入时锁定父项目。
2. 在同一查询中同时约束 `Model.id == resource_id` 和 `Model.project_id == project_id`；写入时锁定子资源。
3. 不存在或属于其他项目统一抛出 `ResourceNotFound`（HTTP 404），不暴露资源归属。

助手只处理单条资源查询。未来的列表、创建、批量更新、删除及关联操作仍须在各自 Repository 的 SQL 中显式约束 `project_id`，并在同一事务内先校验父项目；创建时由服务端设置父项目 ID，不信任客户端传入的归属。测试当前使用模拟模型与数据，没有创建子资源表。

## 错误与分页

请求校验失败为 422；已知数据库连接故障为 503；非连接类数据库错误及未预期内部错误为 500。客户端不接收 SQL、凭据或驱动详情。

项目分页按 `created_at DESC, id DESC` 排序，时间相同时 UUID 是唯一的次排序键。基于 offset 的分页在并发插入或删除时不保证跨请求的快照一致性；未来如需稳定遍历可评估游标分页。
