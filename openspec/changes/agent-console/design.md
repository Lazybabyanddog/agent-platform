# Design

## Context

见 `proposal.md` 的 Why。当前实现中与本次设计直接相关的约束：

- 三层模型已经成立：`agent`（逻辑身份）、`agent_version`（不可变快照 + `config_hash`）、`agent_release` / `agent_release_pointer`（环境发布）。装配逻辑集中在 `AgentService.create_version`，`build_snapshot` 固定为 `{model, prompt, tools[], policy}`，`SNAPSHOT_SCHEMA_VERSION = 1`。
- `normalize_policy` 目前只接受 `max_steps`、`timeout_s`、`token_budget` 三个键，其余键会被丢弃，因此新增会话策略必须显式改这里。
- `execution.run` 的 `agent_release_id` 与 `agent_version_id` 为 `NOT NULL` 且 `ON DELETE RESTRICT`；`session_id` 只是可空字符串列；执行时 `thread_id = str(run_id)`，checkpointer 仅用于单次执行内的状态，不承担跨调用记忆。
- `InvocationService.invoke` 的固定顺序是「解析指针 → 复检引用 → 校验入参 → 建 run → 执行 → 落结果」，任何一步失败都有明确的失败关闭语义。
- 权限为 `AGENT_READ / AGENT_MANAGE / AGENT_PUBLISH / AGENT_INVOKE`；`ResourceGrant` 的主体只有 `USER / ROLE / TENANT`。
- 列表接口返回全量数组，前端内存过滤；`AgentStatus` 只有 `DRAFT / ACTIVE / ARCHIVED`，且发布流程不会修改状态（现状中已发布的 Agent 可能仍是 `DRAFT`）。
- 迁移由 `backend/alembic` 管理，数据库为 Postgres（`JSONB` 可用），没有 Redis 等额外基础设施。

## Goals / Non-Goals

**Goals:**

- 用一条装配管线同时服务「保存版本」与「预览执行」，从结构上消除两者行为漂移的可能。
- 让连续对话成立，并且会话内的配置版本可解释、可复现。
- 让预览运行与正式运行共用执行与追踪链路，但在数据与指标上可区分。
- 保持版本、发布记录与既有 API 契约向后兼容。

**Non-Goals:**

- 不引入持久化草稿配置实体（`AgentDraft`），不做多人同时编辑同一份草稿的协同。
- 不引入通用绑定表或 `snapshot_schema_version = 2`；本期无工作流编排、无独立知识库/记忆资源类型。
- 不做异步执行、流式响应、按 API Key 粒度的资源授权。
- 不迁移历史运行记录到新的 `thread_id` 方案，旧调用不构成会话。

## Decisions

### D1. 编辑态留在客户端，服务端只提供 dry-run

不新增 `AgentDraft`。配置页的编辑态由前端持有，只有两个出口：

- `POST /agents/{id}/versions`：装配 + 持久化为不可变版本；
- `POST /agents/{id}/preview`：装配 + 执行一次，不持久化版本。

**理由**：版本仍是唯一的事实来源，避免引入「草稿与快照双写一致性」这一整类问题。**备选**：持久草稿（能表达未保存修改、支持协同），代价是新增实体、并发编辑冲突与清理策略，当前没有协同诉求。

**代价与补丁**：编辑态刷新即丢。通过「离开确认 + 以基准版本重建」缓解，且明确不做 localStorage 伪草稿，避免出现来源不明的配置。

### D2. 装配管线单一入口

抽出 `assemble_snapshot(actor, refs) -> AssembleResult`，内部完成：解析引用 → 校验状态与授权 → 构建 `model/prompt/tools` 段 → `normalize_policy` → `build_snapshot` → `config_hash` → 生成绑定列表。`create_version` 与 `preview` 都只调用它，前者多一步持久化，后者多一步执行。

```
refs ──> assemble_snapshot ──┬──> persist(version + bindings)  => 保存
                             └──> execute(snapshot, budget)    => 预览
```

这样「预览能跑通、保存却报错」在结构上不可能发生。**备选**：预览复用「保存到临时版本再删除」，会污染版本号且难以保证清理。

### D3. 预览只接受引用，不接受快照正文

`PreviewRequest` 只包含 `model_id`、`prompt_version_id`、`tool_version_ids`、`policy`、`input`、可选 `preview_session_id`。服务端重新装配，客户端无法注入 Prompt 正文或伪造工具契约。

预览执行复用 `InvocationService` 的同一套能力：引用复检、入参渲染校验、工具二次授权、步骤/超时/Token 预算、`TracingModelPort` 与 `TracingToolPort`。差异只有两点：不读发布指针（配置来自请求）、不写版本。

**权限**：预览需要 `AGENT_INVOKE`（会产生模型成本），查看调试结果需要 `AGENT_READ`。

### D4. `run` 表扩展而不是新建预览表

`run` 增加：

| 列 | 类型 | 说明 |
|---|---|---|
| `kind` | `INVOKE` / `PREVIEW`，非空，默认 `INVOKE` | 运行种类 |
| `agent_release_id` | 改为可空 | 预览无发布记录 |
| `agent_version_id` | 改为可空 | 预览无版本 |
| `execution_session_id` | 可空，FK `execution_session.id` | 会话关联 |
| `preview_snapshot` | `JSONB`，可空 | 预览所用临时快照，用于解释该次运行 |
| `config_hash` | 可空 `String(64)` | 预览配置摘要，用于「是否已存在同配置版本」判断 |

**理由**：轨迹（`model_call` / `tool_call`）与执行事件都以 `run_id` 为外键，新建预览表会导致同一套追踪逻辑分叉、跨表查询与指标聚合复杂化。**备选**：独立 `preview_run` 表——被否，收益仅为隔离，成本是双份追踪与双份查询。

### D5. 会话实体与 thread_id 派生

新增 `execution_session`：

| 列 | 说明 |
|---|---|
| `tenant_id` / `agent_id` / `env` / `session_key` | 联合唯一，`session_key` 为调用方提供的会话标识 |
| `kind` | `INVOKE` / `PREVIEW` |
| `agent_release_id` / `agent_version_id` | 正式会话首轮绑定，预览会话为空 |
| `preview_snapshot` | 预览会话首轮固化的临时配置 |
| `status` | `IDLE` / `BUSY` |
| `busy_since` | 抢占残留 `BUSY` 的依据 |
| `last_active_at` / `expires_at` | 保留期 |

`thread_id` 使用 `sess-{execution_session.id}`，而不是拼接 `tenant:agent:env:session_key`。理由：长度可控（不超过 128）、不把租户标识写进 checkpoint 键、会话被归档后线程仍然唯一。

**无 `session_id` 的调用保持单轮**：不建会话行，`thread_id` 仍为 `run-{run_id}`，行为与现状一致。连续对话是显式选择，不是对所有调用的隐式改变。

### D6. 会话并发用行状态抢占，不引入分布式锁

执行前对会话行执行 `UPDATE ... SET status='BUSY', busy_since=now() WHERE id=? AND (status='IDLE' OR busy_since < now() - interval '...')`，受影响行数为 0 则返回 `SESSION_BUSY`。执行结束在 `finally` 中释放回 `IDLE` 并更新 `last_active_at`。

**理由**：不新增 Redis 依赖；单库即可保证同一会话互斥。**代价**：进程崩溃会留下 `BUSY`，通过 `busy_since` 的超时抢占兜底，而不是依赖进程退出钩子。

### D7. 版本粘性在受理阶段完成，失效则失败关闭

正式会话首轮：解析指针 → 写入会话绑定 → 用该绑定执行。后续轮：直接读会话绑定，不再读指针。每轮仍执行 `_reference_problems` 复检，但复检失败时**不重新解析指针**，而是返回失败关闭并提示新建会话。这保持了「一次会话一个快照」的语义，与既有「一次执行一个快照」一致。

### D8. 会话策略进 `policy`，不升 snapshot schema

`normalize_policy` 增加三个键并给出默认值与上限：

| 键 | 默认 | 约束 | 作用 |
|---|---|---|---|
| `session_ttl_h` | 168 | 1 ~ 720 | 会话保留期 |
| `history_token_limit` | 4000 | 256 ~ 32000 | 参与执行的历史 Token 上限 |
| （并发策略固定） | — | — | 同会话并发一律拒绝，不做配置项 |

历史裁剪在执行器内完成：加载 checkpoint 状态后按消息从旧到新丢弃，直到估算 Token 不超过上限，并在运行事件中记录被裁剪的条数，便于解释「为什么模型忘了前文」。

### D9. 状态机与发布联动

状态语义与迁移：

```
DRAFT --首次发布成功--> ACTIVE <--启用/停用--> INACTIVE
   |                      |                     |
   +----------------------+---------------------+--> ARCHIVED
```

- 发布成功且当前为 `DRAFT` → `ACTIVE`；`INACTIVE` 下发布**不**自动启用。
- `INACTIVE` / `ARCHIVED` 在 `_resolve_release` 前置校验中直接失败关闭，错误码可区分。
- 删除仅在「无版本、无发布、无运行」时允许，在事务内以计数查询判定；归档保留编码与全部历史。

### D10. 复制在一个事务内重建快照与绑定

`POST /agents/{id}/copy`：校验新编码 → 查来源最新版本 → 新建身份（`DRAFT`）→ 插入版本 1（沿用来源 `snapshot` 与 `config_hash`）→ 由 `snapshot_reference_ids` 重建绑定行 → 审计事件。不复制发布、指针、运行与统计。来源无版本时只建身份。

**理由**：复制必须得到「可发布」的完整配置，而不是只有身份的壳；快照是自包含的，因此复制不受来源资源后续变化影响。

### D11. 列表查询下沉到服务端

`GET /agents` 改为分页响应（复用现有 `Page[T]`），支持 `status`、`type`、`tag`、`q`（编码或名称模糊）、`page`、`page_size`。三环境当前版本通过一次 `LEFT JOIN agent_release_pointer + agent_release + agent_version` 聚合取得，避免列表行逐条查询（N+1）。排序默认 `updated_at DESC`。

### D12. 权限与授权主体不变

沿用 `AGENT_READ / AGENT_MANAGE / AGENT_PUBLISH / AGENT_INVOKE`，不新增权限点，也不扩展 `GrantSubjectType`。调用授权仍由租户级 API Key + `ResourceGrant`（`USER / ROLE / TENANT`）表达。若后续需要「按 Key 授权到单个 Agent」，再单独立项补主体类型。

### D13. 前端结构

- `AgentListView`：服务端分页与筛选，行内展示三环境版本与状态，操作菜单按状态与权限渲染。
- 新增 `AgentDetailView`：身份概览 + 配置四区 + 版本列表 + 发布面板；配置编辑态由一个 composable 持有，保存与预览都从它取引用集合。
- 新增 `AgentPreviewPanel`：连续对话、工具调用折叠、单轮轨迹抽屉；持有 `preview_session_id`，配置变更时提示新建会话。
- `PlaygroundView` 改为复用 `AgentPreviewPanel` 的预览能力，不再走已发布环境的 invoke。

### D14. 发布前体检复用装配与预览结果

`GET /agents/{id}/versions/{version_id}/readiness` 返回每个绑定的可用性与最近一次同 `config_hash` 预览的结果摘要。体检只读，不改变发布状态；发布接口仍独立复检一次，遵循「失败关闭」。

## Risks / Trade-offs

- **`run` 的两个外键改为可空**，打破了「每条运行必有版本」的隐含假设 → 用非空 `kind` 约束区分：`INVOKE` 必须有发布与版本，`PREVIEW` 必须有 `preview_snapshot` 与 `config_hash`，在应用层与数据库 `CHECK` 双向约束；查询默认按 `kind` 过滤。
- **存量状态不自洽**：现存已发布 Agent 的状态可能仍是 `DRAFT` → 迁移脚本按「存在任一发布记录」回填 `ACTIVE`，并记录回填数量。
- **`BUSY` 残留** → `busy_since` 超时抢占；会话详情可观测当前持有者。
- **checkpoint 表持续增长** → `expires_at` 到期后拒绝续聊，清理任务（按 `expires_at` 删除 thread）列为后续任务；本期先保证不再无限累积新线程。
- **会话粘版本让热修到不了进行中的会话** → 这是刻意的取舍；通过失败关闭与「新建会话」提示把行为说清楚，而不是静默切换。
- **预览与正式的双入口仍可能漂移** → 二者共用 `assemble_snapshot` 与 `InvocationService` 的执行段；用契约测试固定「同引用同配置得到同 `config_hash`」。
- **历史调用无法跨版本续聊** → 明确不迁移：升级前的 run 使用 `run-{id}` 线程，不能续接；对外只承诺升级后的会话语义。

## Migration Plan

1. 迁移一：`agent.status` 增加 `INACTIVE`；新增 `execution_session` 表；`run` 增加 `kind`（默认 `INVOKE`）、`execution_session_id`、`preview_snapshot`、`config_hash`，并把两个版本类外键改为可空；加 `CHECK` 约束与 `(tenant_id, agent_id, kind, started_at)` 索引。
2. 迁移二（数据回填）：`run.kind = 'INVOKE'`；对存在发布记录的 Agent 回填 `status = 'ACTIVE'`。
3. 部署后端：`invoke` 在无 `session_id` 时行为不变，兼容旧客户端；新增接口先行上线。
4. 部署前端：列表切换到分页接口；调试台切换到预览接口。
5. 回滚策略：新列均可空且旧代码忽略；回滚代码后 `execution_session` 与新增列保留不删除，待确认无写入后再单独清理。

## Open Questions

- 会话历史裁剪的 Token 估算口径（按字符近似还是按模型 tokenizer）在实现时确定，不影响 spec 与任务拆分。
- checkpoint 清理任务的调度方式（随应用定时器或外部 cron）可延后决定，`expires_at` 已足够支撑读路径的正确性。
