# Proposal

## Why

租户级「正文留存」开关（`content_retention_enabled`）把两种完全不同的语义合并在一个开关上：

- 追踪侧它确实是落库闸门（`insight.llm_call.request_body/response_body`），默认关闭使追踪表里没有任何正文，排障只能看摘要。
- 会话回放侧它只是一道展示闸门：会话正文本来就在 LangGraph checkpoint 与 `execution.run.input` 里，开关关着只是不返回。

结果是调试台最核心的用例——回看自己刚聊过的会话——被自己的服务端挡住：会话正文一律变成「（正文未留存，原始长度 N）」，会话标题也不落库，列表只剩 `DEV · N 轮 · 相对时间`。而 `member` 角色没有 `insight:read_content`，即使租户开了留存也永远看不到自己会话的正文。这个开关没有保护任何数据，只损失了可用性。

## What Changes

- **BREAKING** 删除租户字段 `platform.tenant.content_retention_enabled`，以及 `PATCH /api/v1/tenants/{tenant_id}/insight-retention` 端点与 `InsightRetentionRequest`。
- 追踪记录不再受策略开关控制：`record_llm_call` 恒写 `request_body` / `response_body`；工具调用的敏感参数仍按 schema 脱敏，`mask_sensitive` 行为不变。
- 追踪查询不再校验租户开关，保留 `insight:read_content` 权限（默认仅 `tenant_admin` 持有），避免普通成员读到同租户他人的完整提示词。
- 会话历史回放直接返回正文，不再要求 `insight:read_content`，仅按归属判定（他人会话与不存在会话统一 404）；会话标题无条件落库。
- 前端删除「正文未留存，原始长度 N」占位分支与相关注释，按运行中的后端重新生成 `schema.d.ts`。
- 存量数据一次性清理：提供可 dry-run 的清理脚本，删除会话元数据、运行记录、会话线程 checkpoint、追踪记录，避免旧数据继续以「无正文」形态出现。
- 同步 `docs/session-history.md` 与 `insight/execution-trace` 规格中关于留存的表述。

## Capabilities

### New Capabilities

- `execution/session-history`: 会话回放的轮次来源、正文可见性与归属边界。主 `openspec/specs/` 下尚无此能力，本次补齐「正文按归属返回」的要求。

### Modified Capabilities

- `insight/execution-trace`: 「敏感内容不进入普通日志」改为「正文随追踪落库、按权限查看」，去掉租户开关这一层。

## Impact

- 数据库：新增迁移删除 `platform.tenant.content_retention_enabled`；清理脚本触及 `execution.execution_session`、`execution.run`、`execution.checkpoint_ref`、`agent.checkpoints`、`agent.checkpoint_blobs`、`agent.checkpoint_writes`、`insight.llm_call`、`insight.tool_call`。
- API：移除 `PATCH /tenants/{id}/insight-retention`；`TenantResponse` 去掉一个字段；`GET /sessions/{id}/history` 在有正文时返回 `content`（结构不变）。
- 代码：`platform`（tables/service/schemas/routes）、`insight`（recorder/trace_service/routes）、`execution`（session_history_service/session_service）、`frontend`（`AgentPreviewPanel`、`schema.d.ts`）。
- 测试：`tests/integration/test_insight_retention.py` 需按新口径重写，`tests/integration/test_session_history.py` 与两个 e2e 中依赖开关的用例改为不依赖开关。
- 顺序约束：`playground-session-history`、`debug-console-session-replay` 仍在活跃变更中且各自对 `execution/session-history` 有 delta，本变更归档前需先归档它们，否则同一能力的 delta 会互相覆盖。
- 不回填：清理后历史会话不可恢复，追踪正文也不做回填（旧记录本就没有正文）。
