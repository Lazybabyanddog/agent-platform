# Proposal

## Why

一期已经跑通「配置 → 不可变版本 → 环境发布 → 调用 → 轨迹」的主干，但 Agent 还只是一个能被打接口的技术对象，不是可运营的产品单元：

- 配置只有一个入口——提交全量快照即生成版本，改一个错别字也涨版本号，无法先把配置改到满意再固化；
- 调试台走的是已发布环境的调用路径，改完配置必须先发布到 DEV 才能试，试错成本高；
- 「聊天型 Agent」实际上是无记忆的单轮调用（`thread_id = run_id`，`session_id` 只落库不参与寻址），连续对话无法成立；
- 列表只有全量返回 + 前端过滤，缺少启停、复制、删除等基本生命周期动作，运营侧无法收敛 Agent 数量。

本变更补齐 Agent 管理中心，使 Agent 成为一个可创建、可配置、可调试、可发布、可停用的完整单元，同时守住「资源只在各自中心维护，Agent 只做引用」这条边界。

## What Changes

- **Agent 列表**：服务端分页与筛选（状态、类型、标签、关键字），列表行展示 DEV/TEST/PROD 当前生效版本与最近调用概况；空状态与无权限降级。
- **生命周期**：新增复制、启用、停用、归档、删除。状态机扩展为 `DRAFT → ACTIVE ⇄ INACTIVE → ARCHIVED`；停用即刻阻断新调用但保留发布指针，重新启用即恢复；删除仅允许从未产生版本、发布与运行记录的空壳 Agent。
- **创建**：创建时可一并提交初始配置，成功即生成 v1；未提交配置则保持 `DRAFT` 且无版本。复制以来源 Agent 的最新版本快照作为新 Agent 的 v1，编码必须重新指定。
- **配置中心**：配置页收敛为四个区块——模型、Prompt、工具、运行策略。知识库以工具中心分类的形式参与选择，不新增绑定类型；记忆以会话策略体现，不引入独立实体。保存配置仍生成不可变版本，新增 `base_version_id` 用于并发变更提示。
- **预览（dry-run）**：不引入持久草稿实体，新增预览执行——服务端用与「保存版本」完全相同的装配管线组装临时快照并执行，返回结果、轨迹与 `config_hash`，但不生成版本、不移动发布指针。
- **调试台**：支持连续对话；首轮把临时快照固化在预览会话上，后续轮复用同一份，保证多轮调试期间配置不漂移。
- **会话**：新增执行会话——以 `(租户, Agent, 环境, 会话标识)` 唯一，首轮绑定发布记录与版本，后续轮沿用该绑定；同会话并发调用直接拒绝；会话受保留期与历史 Token 上限约束；绑定版本失效时失败关闭并提示新建会话。
- **发布**：补齐「当前生效版本」聚合视图与发布前体检（复用引用可用性校验与预览）。
- **统计口径**：预览运行与正式调用分开统计，避免调试行为污染成功率与耗时指标。
- **非破坏性**：既有版本、发布记录、运行记录、API 契约保持向后兼容；`POST /invoke` 的请求体不变，`session_id` 语义由「仅记录」变为「参与会话寻址」。

## Capabilities

### New Capabilities

- `console/agent-management`: Agent 管理中心的入口、列表检索、创建与复制、启停归档删除的状态规则、只读降级。
- `console/agent-configuration`: 配置四区块的编辑与资源引用边界、保存为不可变版本、预览 dry-run 与配置摘要回显。
- `console/agent-debugging`: 调试台的连续对话、模型回答与工具调用展示、执行轨迹回溯、预览会话隔离。
- `execution/agent-session`: 执行会话的建立、版本粘性、并发控制、保留期与会话失效处理。

### Modified Capabilities

- `agent/agent-release`: 逻辑身份的状态机扩展为含停用与归档，新增复制语义、删除约束、创建即生成初始版本，以及「当前生效版本」聚合与发布前体检。
- `execution/agent-invocation`: 调用入口的会话寻址与版本粘性——首轮固定发布快照，同会话后续轮复用首次绑定，会话级并发拒绝与失效失败关闭。
- `insight/execution-trace`: 运行记录区分预览与正式调用，指标增加会话维度并在聚合时排除预览运行。

## Impact

- 后端：`contexts/agent`（`api/routes.py` 增补 PATCH/DELETE/copy/preview，`application/agent_service.py` 复用装配管线并支持初始配置、`domain/enums.py` 扩展状态，`domain/snapshot.py` 扩展 `policy` 会话字段）、`contexts/execution`（新增会话表与仓储、`invocation_service.py` 改为按会话解析版本与 thread、`api/schemas.py` 保持入参兼容）、`contexts/insight`（指标口径与会话维度）、`platform`（沿用既有权限点，无新增授权主体）。
- 数据库：新增执行会话表；`run` 表增补运行种类与会话关联字段；迁移需兼容存量运行记录。
- 前端：`AgentListView` 改为服务端分页筛选并补充生命周期操作，新增 Agent 详情/配置页与预览对话面板，`PlaygroundView` 改走预览会话，`ReleaseView` 展示当前生效版本与体检结果。
- 契约：`POST /api/v1/agents/{id}/invoke` 请求体不变；新增预览、复制、状态变更、会话查询接口；`session_id` 语义变更需在 README 与 OpenAPI 描述中说明。
- 依赖：无新增外部依赖，复用 LangGraph checkpointer 与 Postgres。
