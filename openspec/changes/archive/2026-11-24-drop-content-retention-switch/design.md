# Design

## Context

动机见 `proposal.md`。实现前的关键事实：

- `execution.run.execution_session_id` 的外键是 `ON DELETE SET NULL`，删会话不会带走运行记录，清理必须显式删 run。
- `insight.llm_call` / `insight.tool_call` 对 `execution.run` 没有外键，不随 run 级联。
- `execution.checkpoint_ref`、`execution.execution_event` 对 run 是 `ON DELETE CASCADE`。
- 业务表启用了强制 RLS（`app.tenant_id`），清理脚本必须逐租户在租户作用域内执行。
- 会话线程的 checkpoint 以 `thread_id = sess-{session_id}` 写入 `agent.checkpoints` / `checkpoint_blobs` / `checkpoint_writes`，这三张表没有外键。
- 追踪侧的敏感参数脱敏（`mask_sensitive`）与留存开关是两条独立路径，前者不受本变更影响。

## Goals / Non-Goals

**Goals:**

- 让「正文要不要给看」只剩一条规则：追踪按权限，会话按归属。
- 移除租户级开关的存储、API、代码引用与文档表述，避免残留字段被再次使用。
- 清空存量会话历史，使调试台不出现「有轮次、无正文」的中间态。

**Non-Goals:**

- 不引入追踪正文的保留时长、体积上限或归档策略。
- 不改动 `insight:read_content` 权限点本身，也不调整内置角色的授权。
- 不回填旧追踪正文（旧记录本就没有正文），不迁移旧会话到新结构。

## Decisions

### D1：删除字段，而不是保留字段并恒为 true

`platform.tenant.content_retention_enabled` 直接随迁移删除，`Tenant` 模型、`TenantResponse`、`InsightRetentionRequest`、`PATCH /tenants/{id}/insight-retention` 一并移除。

备选是保留列并忽略，回滚更容易；但保留一个恒真、无人读取的策略字段，会在后续改动里被误当成有效开关，且 `TenantResponse` 会持续暴露一个没有意义的布尔值。迁移的 `downgrade` 恢复列并设默认 `false`，保证结构可回退。

### D2：追踪正文恒定落库，脱敏逻辑不动

`InsightCallRecorder` 删除 `_content_retention_enabled` 与 `TenantService` 依赖：`record_llm_call` 恒写 `request_body` / `response_body`，`record_tool_call` 恒写已脱敏的 `masked_arguments` / `masked_response`。

保留 `mask_sensitive` 与 `_mask_response`，敏感字段掩码与 digest 计算不变。这样「模型看到了什么」可完整复盘，而「工具参数里的密钥」仍不落明文，两者不再被同一个开关绑死。

### D3：追踪查询保留 `insight:read_content`，会话不保留

`TraceService.get` 的 `include_content` 退化为 `actor_can_read_content`（路由层仍是 `is_admin or insight:read_content`），删除租户查询。

会话语义不同：`GET /sessions/{id}/history` 已经做了创建者校验，正文就是调用方自己产生的，再要求 `insight:read_content` 只会让 `member` 看不到自己的对话。追踪是跨会话视角，`member` 持有 `run:read` 就能读同租户他人的运行，因此保留权限点作为唯一的正文闸门。

### D4：会话历史删除 `include_content` 分支，而不是恒传 true

`SessionHistoryService._include_content` 与 `TenantService` 依赖删除，`_assemble` 去掉 `include_content` 参数，`HistoryMessage.content` 恒为原文、`length` 保留。

不采用「恒传 `True`」是因为保留一个永远为真的布尔参数会让下次改动误以为还有策略分支；同时单测里「隐藏正文」的用例需要改成「正文可见」，正好暴露测试对旧策略的依赖。

响应结构不变（`content` + `length`），前端无需适配字段。

### D5：标题无条件落库

`SessionService.record_turn` 删除租户查询，`title = session_title(values)` 恒执行。标题是会话列表的辨识信息，不构成额外的正文暴露面（它就是首轮输入的截断）。

存量会话的 `title` 已为空，且按 D6 会被整体清理，不做回填。

### D6：存量清理用独立脚本，不进迁移

新增 `backend/scripts/purge_session_history.py`：

- 默认 dry-run：按租户打印各表待删行数，不写库。
- `--yes` 才执行；可选 `--tenant <code>` 限定租户。
- 删除顺序：`agent.checkpoint_writes` → `agent.checkpoint_blobs` → `agent.checkpoints`（`thread_id like 'sess-%'`）→ `insight.tool_call` → `insight.llm_call` → `execution.run`（连带 `checkpoint_ref`、`execution_event`）→ `execution.execution_session`。
- 以平台级连接（`system_session_scope`）执行：`insight.llm_call` / `insight.tool_call` 只授予应用角色 `SELECT, INSERT`，没有 `DELETE`，用租户作用域会被权限拒绝。

不放进 alembic 的原因：迁移会在每次部署自动执行且不可逆，删数据需要人工确认与可审计的 dry-run；schema 变更（删列）仍走迁移。

### D7：前端删除占位分支并重建类型

`AgentPreviewPanel.messageText` 去掉 `length` 占位分支，只保留 `content` 与调用方传入的 fallback；`AgentSessionList` 的占位注释同步改写。`schema.d.ts` 用 `npm run gen:api` 对着运行中的后端重新生成，`TenantResponse` 少一个字段，`/tenants/{id}/insight-retention` 路径消失。

### D8：先归档两个在途变更

`playground-session-history` 与 `debug-console-session-replay` 都还在活跃状态，且各自对 `execution/session-history` 有 delta。本变更归档前必须先归档它们，否则同一能力的 requirement 会在合并时互相覆盖。

## Risks / Trade-offs

- [删列不可逆] → 迁移提供 `downgrade` 恢复列（默认 `false`），但代码已不读取；回滚需要同时回退代码版本。上线前确认没有外部调用方依赖该字段。
- [清理脚本误删] → 默认 dry-run、打印每表计数、要求显式 `--yes`；建议先在生产快照或备份后执行。
- [RLS 导致脚本删不到行] → 逐租户在 `session_scope(tenant_id)` 内执行，不使用裸连接。
- [追踪正文体积增长] → 单次调用的请求/响应正文随记录落库，长上下文会放大 `llm_call` 体积；本次不做截断，因为截断会让复盘失真，后续可按保留策略单独处理。
- [长会话回放响应变大] → `turns_limit` 默认 50、上限 200 已限制单次返回；正文可见后单轮 payload 变大属于预期。
- [`member` 仍看不到他人追踪正文] → 有意保留；若后续要放开，应作为独立的权限变更讨论，而不是恢复租户开关。

## Migration Plan

1. 运行迁移：删除 `platform.tenant.content_retention_enabled`。
2. 部署后端：追踪恒写正文、会话恒返回正文、标题恒落库、删除端点。
3. 执行 `purge_session_history.py` dry-run，核对计数后加 `--yes` 清理存量。
4. `npm run gen:api` 重新生成前端类型并验证 `typecheck` / `build`。
5. 回滚：回退代码版本 → 迁移 `downgrade` 恢复列；已清理的会话与追踪正文无法恢复。

## Open Questions

无。清理范围已确定为「全部存量会话」，不做时间窗口或保留策略。
