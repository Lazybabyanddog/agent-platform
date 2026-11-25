# Tasks

## 1. 数据模型与 API 收缩

- [x] 1.1 新增迁移 `0012_drop_content_retention.py` 删除 `platform.tenant.content_retention_enabled`，`downgrade` 恢复列并设默认 `false`；验证 `alembic upgrade head` 与 `alembic downgrade -1` 均成功
- [x] 1.2 删除 `platform` 侧引用：`Tenant` 模型字段、`TenantResponse.content_retention_enabled`、`InsightRetentionRequest`、`PATCH /tenants/{tenant_id}/insight-retention` 路由、`TenantService.set_content_retention` 与 `content_retention_enabled`；验证 `grep content_retention backend/src` 无结果，且应用启动后 `/openapi.json` 不含该路径
- [x] 1.3 更新 `tests/integration/test_tenant_*` 中涉及该字段的断言（若存在）；验证 `pytest tests/integration -k tenant` 通过

## 2. 追踪正文恒定落库与按权限查看

- [x] 2.1 `InsightCallRecorder` 删除 `_content_retention_enabled`、`_retention` 字段与 `TenantService` 依赖，`record_llm_call` / `record_tool_call` 恒写 `request_body` / `response_body`，保留 `mask_sensitive` 与 `_mask_response`；验证一次预览运行后 `insight.llm_call` 的 `request_body` 非空且工具敏感参数仍为掩码
- [x] 2.2 `TraceService.get` 的 `include_content` 只取 `actor_can_read_content`，删除 `TenantService` 依赖与租户查询；验证 admin 请求返回正文，`member` 请求只返回摘要
- [x] 2.3 重写 `tests/integration/test_insight_retention.py` 为「正文恒落库 + 权限隔离」两个用例（admin 见正文、member 见摘要、敏感参数恒掩码）；验证该文件通过
- [x] 2.4 更新 `docs/` 下追踪相关说明中关于「默认不保存正文」的表述；验证文档描述与实际接口一致（引用新的权限条件，不再提租户开关）

## 3. 会话回放按归属返回正文

- [x] 3.1 `SessionHistoryService` 删除 `_include_content` 与 `TenantService` 依赖，`_assemble` 去掉 `include_content` 参数，`HistoryMessage.content` 恒为原文；验证 `pytest tests/unit/test_session_history_adapter.py` 中「正文可见」用例通过
- [x] 3.2 `SessionService.record_turn` 删除租户查询，`title = session_title(values)` 恒执行；验证新建会话后 `execution.execution_session.title` 非空
- [x] 3.3 改写 `tests/integration/test_session_history.py`：删除 `_enable_retention` 辅助与 `test_history_returns_summary_when_retention_disabled` 等依赖开关的用例，新增「member 回放自己的会话可见正文」「他人会话仍 404」；验证该文件通过
- [x] 3.4 改写 `tests/e2e/test_playground_session_history_flow.py`，去掉 `_enable_retention` 调用，断言刷新后气泡显示原文；验证 `pytest tests/e2e -k session` 通过
- [x] 3.5 更新 `docs/session-history.md` 的「留存与权限」章节与链路图注释，改为「正文按归属返回」；验证文档中不再出现 `content_retention_enabled`

## 4. 前端

- [x] 4.1 `AgentPreviewPanel.messageText` 删除 `length` 占位分支，`AgentSessionList` 占位注释改写；验证 `npm run typecheck` 通过
- [x] 4.2 `npm run gen:api` 重新生成 `schema.d.ts`，确认 `/tenants/{tenant_id}/insight-retention` 与 `content_retention_enabled` 消失；验证 `npm run build` 通过
- [ ] 4.3 浏览器实测：新建会话两轮后刷新页面显示原文气泡，会话列表显示首轮标题；验证无控制台报错

## 5. 存量会话清理

- [x] 5.1 新增 `backend/scripts/purge_session_history.py`：默认 dry-run 打印各表计数，`--yes` 执行，支持 `--tenant <code>`，按 `checkpoint_writes → checkpoint_blobs → checkpoints → tool_call → llm_call → run → execution_session` 顺序在租户作用域内删除；验证 dry-run 输出计数且不写库
- [x] 5.2 在本地库执行 dry-run 核对计数，再加 `--yes` 清理；验证刷新调试台后会话列表为空、`select count(*) from execution.run` 为 0

## 6. 集成验证

- [x] 6.1 运行 `ruff check`、`pytest tests/unit tests/integration tests/e2e` 全绿；验证无残留留存相关断言
- [ ] 6.2 端到端走查：登录 → 新建会话 → 两轮对话 → 刷新回放显示正文 → 会话列表显示标题 → 追踪详情显示 prompt 正文；验证各步骤符合规格场景

## Workflow follow-up

- 先归档 `playground-session-history` 与 `debug-console-session-replay`，再归档本变更，避免同一能力的 delta 覆盖。
- 归档前确认迁移 `0012` 与清理脚本已在目标环境执行。
