# Design

## Context

- 前端 `AgentPreviewPanel.vue` 用 `ref<sessionId>` + `ref<turns>` 承载对话（`sessionId` 初始化于:111，`startNewSession` 于:132-136 覆盖两者），没有任何会话数组或持久化；`AgentDetailView` 与 `PlaygroundView` 复用该组件。
- 后端已持久化会话：`execution.execution_session`（唯一键 `tenant_id+agent_id+env+kind+session_key`，含 `preview_snapshot`、`status`、`last_active_at`、`expires_at`，`tables.py:30-84`），`thread_id` 由行 id 派生为 `sess-{row.id}`（`session_service.py:232`）。
- 会话元数据列表接口已存在：`GET /api/v1/sessions`（`routes.py:106`，分页、`kind`/时间范围过滤、`last_active_at desc` 排序），前端已在 `endpoints.ts:203` 封装但无人调用。
- 会话消息没有独立表：内容是 LangGraph checkpoint 的 `messages` 通道，运行时经 `executor._load_history`（`executor.py:139`）按 `thread_id` 读取；`PostgresCheckpointStore`（`checkpoint_store.py`）已能按 `thread_id` 取 checkpoint。
- `execution.run` **没有 output 列**（`tables.py:87-144`），只有 `input`、`status`、`error`，因此无法从运行记录重建聊天内容。
- 正文留存是租户级开关（`tenant.content_retention_enabled`），`trace_service.get` 已用 `actor_can_read_content && retention` 决定是否返回正文。
- 数据隔离靠 `session_scope(tenant_id)` + RLS（`0007_agent_console.py`），请求体里的租户不被信任（`ActorContext` 注释明确）。

## Goals / Non-Goals

**Goals:**

- 会话可见：列表按当前 Agent/环境列出预览会话，行内信息足以区分会话，不需要打开才能认出来。
- 回放准确：轮次边界、工具条目、工具状态与耗时与原对话一致；回放不产生任何执行副作用。
- 续聊无感：切回历史会话后继续发送，语义与「一直没关页面」等价（沿用会话标识与首轮固化快照）。
- 读取口径唯一：历史只从运行时的同一份上下文读，不为展示复制第二份消息。

**Non-Goals:**

- 不新增会话消息表，不做双写。
- 不做会话重命名、置顶、删除、分享、导出。
- 不做跨 Agent 的全局会话视图；列表始终按 Agent 收窄。
- 不改「同一标识在过期后再次调用按新会话处理」的既有语义。
- 正式调用（`kind=INVOKE`）会话的界面呈现不在本期，接口按会话通用，UI 只做预览会话。

## Decisions

### D1 历史真相源：读 checkpoint，不建消息表

历史接口按会话行的 `thread_id` 读取 LangGraph checkpoint 的 `messages` 通道，与 `_load_history` 使用同一入口（`PostgresCheckpointStore.saver().aget_tuple`）。

- 备选一：新增 `execution_session_message` 表。每轮双写，需迁移、回填与漂移兜底；一旦运行时裁剪历史或工具消息与业务表不一致，两份真相的口径就要长期维护。否决。
- 备选二：从 `execution.run` 重建。`Run` 没有存 output，`input` 只是变量字典，无法还原模型回答。否决。
- 代价：耦合 LangGraph 的消息结构。约束是把解析集中在**一个 adapter**（如 `execution/application/session_history_service.py` 内的 `_normalize_messages`），其它层只看到结构化轮次；升级 LangGraph 时只需回归该 adapter 的契约测试。

### D2 轮次合成在服务端完成

服务端把消息原语合成「轮次」，前端只渲染结构化结果，不在浏览器里解析 LangChain 消息。

合成规则：

```
 checkpoint messages: [system, human1, ai(tool_calls), tool, ai, human2, ai, ...]
                          |        \___________ 第 1 轮 _______/   \__ 第 2 轮 __/
                          |                                          |
                       丢弃（运行时每轮重新渲染）                      |

 第 N 轮 = 一条 human 起，到下一条 human 前止
   user        = human.content
   tools[]     = ai.tool_calls 与 tool 消息按 tool_call_id 配对
                 （name 取 tool_call.function.name / tool 消息的 name）
   assistant   = 该段内最后一条无 tool_calls 的 ai.content
   run 元数据  = 会话内按 started_at 升序的第 N 条 run（run_id / trace_id / status / config_hash / 用量）
```

- 工具状态与耗时来自 `insight` 的 `tool_call` 记录（`trace_service` 用的同一张表），按 `run_id IN (...)` 一次性批量取，不逐轮请求 `/runs/{id}/trace`。
- 对齐容错：run 数多于段数时，多出的 run 生成「仅错误信息」的轮次（执行在产生回答前失败）；段数多于 run 数时，该段不带 run 元数据（历史遗留或非本接口产生的会话），照常展示消息。
- 顺序与边界以 checkpoint 为准，工具条目在轮次内部按 `tool_call_id` 出现顺序排列。

### D3 元数据落库，列表不回读历史

`execution_session` 增列：

| 列 | 类型 | 说明 |
|---|---|---|
| `title` | `String(120)` nullable | 首轮输入的截断摘要，正文留存关闭时留空 |
| `turn_count` | `Integer` not null, server_default `0` | 成功产生 run 的轮数 |
| `created_by` | `Uuid` nullable | `actor.actor_id`（用户或 API Key 标识） |

索引 `(tenant_id, agent_id, kind, created_by, last_active_at desc)` 支撑默认列表查询，避免逐行回读 checkpoint。

写入时机：`preview_service` 在执行结束拿到 `result.status` 后调用 `session_service.record_turn(...)`；标题只在首轮写入（`turn_count == 0` 且该轮成功），轮数在该轮产生 run 时递增，失败受理（如 `SESSION_BUSY`、引用不可用）不递增。标题来源是首轮 `values` 中的主文本字段（最长可读字符串），去换行后截断，正文留存关闭时跳过。

### D4 归属过滤默认收紧，存量按租户共享

列表默认条件 `created_by = actor.actor_id OR created_by IS NULL`。新会话必写 `created_by`；迁移不回填，存量会话因此保持当前行为（同租户可见），并在响应里以 `owned=false` 让界面标注为历史遗留。

- 备选：租户内全量可见（更贴近「团队调试台」）。否决——调试输入常含未脱敏业务数据，默认收紧更安全；将来要共享，加显式开关而不是改默认。
- 访问他人会话与不存在会话统一返回 404，避免存在性探测。

### D5 过期会话只读，不删行

列表返回派生字段 `expired = expires_at <= now`。过期会话：历史可读、发送禁用、提示新建。后端不新增「过期即清理」逻辑，保留既有语义（同一标识再次调用时旧行被回收，`session_service.py:224-226`），在 README/API 描述中写清这条边界。

### D6 留存与脱敏沿用既有判定

历史接口复用 `actor_can_read_content && tenant.content_retention_enabled`：关闭时 `content` 置空，改返回 `length`，工具条目只给名称/状态/耗时；`title` 不落库，列表用「环境 · 轮数 · 相对时间」占位。

### D7 接口形态

- `GET /api/v1/sessions`（扩展，不破坏旧客户端）：新增可选 `env`、`include_expired`(默认 true)、`mine_only`(默认 true)；响应项新增 `title`、`turn_count`、`owned`、`expired`、`preview_config_hash`。
- 新增 `GET /api/v1/sessions/{session_id}/history?turns_limit=50`（`session_id` 为会话行 id）：返回 `session`（同列表项结构）与 `turns[]`，每轮含 `index`、`user`、`assistant`、`tools[]`、`run_id`、`trace_id`、`status`、`error`、`started_at`。`AGENT_READ`；`turns_limit` 超限时返回最近 N 轮并带 `truncated=true`。
- 用行 id 而不是 `session_key` 做路径参数：行 id 是主键且 `thread_id` 由它派生；`session_key` 只在「切回会话继续发送」时回传给 `preview_session_id`。

### D8 前端：列表归外层，面板受控

`PlaygroundView` 变双栏：左 `AgentSessionList.vue`，右 `AgentPreviewPanel`。会话列表状态与「当前选中会话」归 `PlaygroundView`；`AgentPreviewPanel` 增加受控 props（`sessionKey`、`historyTurns`、`readOnly`/`expired`）与事件（`turn-completed`、`request-new-session`），在未传入 `historyTurns` 时保持现有自持行为，`AgentDetailView` 用法不变。

- 回放映射：`history.turns` → `ChatTurn[]`（user 与 assistant 各一条，工具条目挂在 assistant 上），复用现有气泡与轨迹抽屉。
- 发送后只更新本地列表行（`turn_count`、`last_active_at`），不整表刷新；切换 Agent 或新建会话后才重新拉第一页。
- 过期会话：输入框禁用并给出「新建会话」按钮；`SESSION_BINDING_UNAVAILABLE` 的既有自动新建行为改为显式提示（不静默丢弃历史视图）。

## Risks / Trade-offs

- [checkpoint 内部结构变化导致回放错位] → 解析集中在单个 adapter，加契约测试（固定一份含工具调用的消息序列），升级 LangGraph 必跑。
- [长会话一次读取 payload 过大] → `turns_limit` 默认 50 + 服务端只取最新 checkpoint 的 `messages`，超出部分标记截断，前端提供「加载更早」占位。
- [轮次与 run 数量不一致] → 按 D2 的降级规则展示，缺元数据的轮次仍显示内容，不整段报错。
- [归属过滤让多人共用一台调试机时看不到彼此会话] → 文档说明默认私有；后续加共享开关而不是放宽默认。
- [过期行仍可能被 API 复用而回收历史] → UI 禁止过期会话发送，缩小触发面；在接口描述与 README 写明回收边界。
- [历史接口绕过正文留存] → 复用 `trace_service` 的同一判定，禁止在 adapter 里直接返回原始 message 内容；测试覆盖关闭留存时的响应体。
- [RLS 之外的越权读取] → 所有查询带 `tenant_id` 条件并走 `session_scope`，`created_by` 过滤在 SQL 层完成，不在内存里筛。

## Migration Plan

1. `alembic revision` 增 `title`/`turn_count`/`created_by` 与索引，全部 nullable 或带 server default，不回填；`downgrade` 仅删列与索引。
2. 先上后端（列表扩展 + 历史接口），前端旧版本继续可用（新字段忽略）。
3. 再上前端双栏与会话列表；无开关，回滚即回退前端构建。
4. 回滚策略：接口可下线，列可保留（nullable 不影响旧代码）；`turn_count` 无历史值时不显示轮数，界面降级为时间。

## Open Questions

- 会话重命名与删除（含「清空历史」）是否纳入下一期，取决于是否有 GDPR/数据清理诉求。
- 正式调用会话是否要进同一列表（需要区分 `kind` 与发布版本绑定展示），留待调用侧有真实排查需求时再定。
