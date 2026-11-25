# Tasks

## 1. 会话元数据与迁移

- [x] 1.1 在 `execution_session` 增补 `title`（`String(120)` 可空）、`turn_count`（`Integer` 非空、`server_default 0`）、`created_by`（`Uuid` 可空）与索引 `(tenant_id, agent_id, kind, created_by, last_active_at desc)`；验证：新增 alembic 迁移可 `upgrade head` 与 `downgrade -1` 往返，`tests/integration/test_agent_console_schema.py` 补充列与索引断言。
- [x] 1.2 同步 ORM 定义与仓储查询，使列表可按 `created_by`、`env`、过期与否过滤；验证：`ExecutionSessionRepository.list_for_agent` 单测断言过滤条件生效且分页总数与行数一致。
- [x] 1.3 新增 `SessionService.record_turn(...)`：首轮写入标题（取输入中最长可读字符串、去换行、截断）、每轮成功受理后递增 `turn_count`、同步 `last_active_at`，正文留存关闭时不写标题；验证：单测覆盖首轮写标题、第二轮不覆盖标题、失败受理不递增、留存关闭标题为空四种情况。

## 2. 会话归属与会话建立

- [x] 2.1 在 `resolve_preview` / `resolve_invoke` 创建会话行时写入 `created_by = actor.actor_id`；验证：集成测试断言新建会话的 `created_by` 等于调用者，且复用既有会话时不被改写。
- [x] 2.2 `preview_service.preview` 在拿到执行结果后按 `result.status` 调用 `record_turn`，并在 `SESSION_BUSY`、引用不可用等未产生 run 的路径上跳过；验证：集成测试断言成功一轮后 `turn_count == 1`、被并发拒绝后仍为 1。
- [x] 2.3 会话过期不再在读取路径上删除记录：核对 `resolve_preview` 的过期分支只在同标识再次受理时回收，列表与历史接口对过期行只读；验证：集成测试把一个会话的 `expires_at` 改到过去，断言列表仍返回该行且 `expired=true`，历史可读，发送返回既有的新会话语义。

## 3. 会话列表接口扩展

- [x] 3.1 扩展 `GET /api/v1/sessions`：新增可选 `env`、`include_expired`（默认 true）、`mine_only`（默认 true），响应新增 `title`、`turn_count`、`owned`、`expired`、`preview_config_hash`，保持旧字段与默认分页行为不变；验证：集成测试断言旧查询参数组合返回结构向后兼容，`mine_only=false` 时可见同租户其他用户会话。
- [x] 3.2 归属过滤在 SQL 层完成（`created_by = actor_id OR created_by IS NULL`），`env` 与过期条件同样下推数据库；验证：单测断言生成的查询条件包含上述谓词，且列表不触发对 checkpoint 的任何读取。
- [x] 3.3 更新 OpenAPI 描述与 `docs/` 中的会话查询说明（新增字段、默认 `mine_only` 语义、无归属会话的可见规则）；验证：`/docs` 中的描述与实际响应字段一致，示例请求可直接执行。

## 4. 会话历史读取

- [x] 4.1 新增 `session_history_service`，按会话行 `thread_id`（`sess-{row.id}`）从 `PostgresCheckpointStore` 读取最新 checkpoint 的 `messages` 通道；验证：集成测试构造两轮预览对话后断言读到 2 条 human 与对应 ai 消息，且不产生新 run。
- [x] 4.2 实现消息归一化 adapter：丢弃 system、按 human 切段、`tool_calls` 与 `tool` 消息按 `tool_call_id` 配对、取段内最后一条无 `tool_calls` 的 ai 作为回答；验证：单测用固定消息序列覆盖「单轮含工具」「工具失败」「多轮」「AI 无回答」四种形态。
- [x] 4.3 轮次与 run 元数据对齐：按 `started_at` 升序取会话内 run，按 D2 规则处理数量不一致，工具状态与耗时按 `run_id IN (...)` 批量取 `tool_call` 记录；验证：单测覆盖 run 多于段、段多于 run 两种错位，断言不抛异常且缺元数据的轮次仍返回内容。
- [x] 4.4 新增 `GET /api/v1/sessions/{session_id}/history?turns_limit=50`：返回 `session` 与 `turns[]`（`index`、`user`、`assistant`、`tools[]`、`run_id`、`trace_id`、`status`、`error`、`started_at`），超限返回最近 N 轮并置 `truncated=true`；验证：集成测试断言顺序、`turns_limit` 截断标记与响应中不含 `system` 角色。
- [x] 4.5 权限与留存：接口要求 `AGENT_READ`，访问他人会话与不存在会话统一返回 404；正文留存关闭时 `content` 为空、只给 `length`，工具条目只保留名称/状态/耗时；验证：集成测试分别以待读权限账号、无 `AGENT_READ` 账号、关闭留存的租户各断言一次响应结构。

## 5. 调试台会话列表

- [x] 5.1 新增 `AgentSessionList.vue`：展示标题（无标题时用环境 · 轮数 · 相对时间占位）、轮数、最近活动时间、过期标记与当前选中态，支持分页加载、空态新建入口、失败重试；验证：`cd frontend && npm run build` 通过，本地验证三种状态渲染与分页不重复。
- [x] 5.2 `PlaygroundView` 改为双栏并持有会话列表与选中会话状态，切换 Agent 或环境时重新加载列表；验证：本地验证切换 Agent 后列表只含该 Agent 的会话，选中项随列表刷新保持或回落到最新会话。
- [x] 5.3 `AgentPreviewPanel` 受控化：新增 `sessionKey`、`historyTurns`、`expired` props 与 `turn-completed`、`request-new-session` 事件，未传 `historyTurns` 时保持现有自持行为，`AgentDetailView` 用法与表现不变；验证：`npm run build` 通过，本地验证详情页预览与调试台两条路径都可连续对话。

## 6. 回放与续聊

- [x] 6.1 历史轮次映射：把 `history.turns` 转成 `ChatTurn[]`（user 与 assistant 各一条，工具条目挂在 assistant 上），复用现有气泡与轨迹抽屉；验证：本地打开一个含工具调用的历史会话，顺序、轮次边界与工具条目与原始对话一致。
- [x] 6.2 在历史会话中续聊时沿用其 `session_key` 与首轮固化配置，配置已变更时给出现有提示，发送成功后只回填该列表行的轮数与活动时间；验证：集成验证「打开旧会话 → 发送 → 第二轮仍用首轮快照」，且列表不整表刷新即可看到轮数变化。
- [x] 6.3 过期会话禁止发送并提示新建，`SESSION_BINDING_UNAVAILABLE` 由静默新建改为显式提示，历史视图不被清空；验证：本地把会话置为过期后验证输入禁用、提示可点击新建，且历史轮次仍在屏上。
- [x] 6.4 端到端用例：三次预览会话 → 新建第四条 → 切回第一条回放 → 续聊一轮；验证：`cd backend && pytest tests/e2e` 中新增该用例通过，断言回放不产生新 run、续聊产生一条 run 且绑定未变。

## 7. 文档与收尾

- [x] 7.1 更新 README 的调试台使用说明与 API 文档：会话历史接口、列表默认只看本人会话、过期会话只读、同一标识复用会回收旧记录的边界；验证：文档中的示例请求与实际响应一致，评审通过。
- [x] 7.2 在 `docs/` 记录历史读取依赖 LangGraph checkpoint 消息结构的约束与回归范围（adapter 契约测试、升级 LangGraph 时的检查项）；验证：新增文档章节可被维护者按图索骥定位到 adapter 与测试文件。

## Workflow follow-up

- 归档顺序与 `agent-console` 对齐：`console/agent-debugging` 与 `execution/agent-session` 由该变更引入，本变更在其上追加要求；若本变更先归档，需在 `agent-console` 归档后复核这两份 spec 的合并结果。
- 归档本变更前确认迁移已在副本库演练 `upgrade`/`downgrade` 往返。
