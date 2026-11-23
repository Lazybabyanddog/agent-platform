# Proposal

## Why

后端已经具备 9 个工具端点（注册、版本、授权、撤销、测试调用），但控制台没有任何工具入口：唯一的工具痕迹是 Agent 版本 diff 里的只读文本（`views/AgentVersionsView.vue`）。结果是 PRD 模块 04 的注册、Contract、认证、授权、风险分级、版本这些能力只能靠 `curl` 或 Swagger 手工操作，而 Agent 版本绑定工具的前置条件（工具是否启用、是否已授权）在界面上完全不可见——绑定失败只能看到一条 `RESOURCE_UNAVAILABLE`。

同时现有契约不足以支撑一个可用的界面：`ToolResponse` 只返回 `id/code/name/description/type/status`，没有风险等级、没有版本、没有授权状态；没有状态写接口（工具创建后无法停用）；没有授权查询接口（`ToolGrantRepository.list` 已存在但无路由）；调用记录只能按 `run_id` 查，无法回答"这个工具最近被调了多少次、失败率多少"。

## What Changes

**控制台（新增）**

- 新增「工具管理」入口与列表页：展示 `code`、名称、类型、风险等级、状态、是否已授权，支持按 `code`/名称本地过滤与空状态引导。
- 新增工具详情页：概览（类型/风险/启用状态/授权状态）、版本列表（只读）、新建版本、测试调用面板、最近调用统计。
- 新建工具与新建版本共用一套 spec 编辑器：常用字段（方法、URL、超时、请求头、认证、风险等级）用表单，`input_schema`/`output_schema` 用 JSON 文本域；JSON Schema 的真值校验仍由后端负责。
- 保存版本遵循既有不可变语义：保存即新增版本，与最新版本无差异时禁用保存。
- 测试调用区分风险等级：`HIGH` 需要显式二次确认后才带 `allow_high_risk`；错误以错误码 + 字段定位呈现。
- 只读降级：`TOOL_READ` 可看全部内容，所有写操作按 `TOOL_MANAGE` 决定可见性；凭据选择器依赖 `SECRET_MANAGE`，缺失时降级为手工填写凭据 ID。

**后端（4 个增量，均为加法）**

- `ToolResponse` 增补 `risk_level`、`latest_version_no`、`granted`、`updated_at`，消除列表页 N+1。
- 新增 `PATCH /api/v1/tools/{tool_id}/status`，支持 `ACTIVE`/`DISABLED` 切换（`TOOL_MANAGE`）。
- 新增 `GET /api/v1/tools/{tool_id}/grant`，回显本租户授权状态与授权时间（`TOOL_READ`）。
- 新增 `GET /api/v1/tool-calls`（按 `tool_id`、可选 `tool_version_id` 与时间窗口过滤），返回调用聚合与最近 N 条记录，只含摘要字段。该端点由 insight 提供，与既有 `/metrics/runs` 同侧，避免 catalog 反向依赖 insight。

**明确不做（本变更范围外）**

- 跨租户授权：`grant` 当前语义是"本租户自授"，控制台按此呈现，不引入租户选择器。
- `ToolPolicy`：重试、限流、熔断、幂等、并发上限均不在本变更内，工具定义仍只有 `timeout_s`。
- 高风险人工审批流：仍由调用方显式传 `allow_high_risk`，控制台只做二次确认，不构成审批。
- Python 函数工具、MCP 结构化表单（MCP 能力由未实施的 `mcp-tool-integration` 负责，本变更对未知类型退化为 JSON 编辑）。
- 测试调用落库：`insight.tool_call` 需要 `run_id`，控制台探活调用没有运行上下文，界面明示"测试调用不计入调用记录"。
- Agent 版本创建/绑定工具的控制台交互（属于 Agent 模块，本变更只保证工具侧的授权与启用状态可被看见）。

## Capabilities

### New Capabilities

- `console/tool-management`: 工具管理中心的界面行为——列表、详情、spec 编辑、版本新增、测试调用、授权与启停操作、调用概览、权限降级。

### Modified Capabilities

- `catalog/tool-registry`: 工具查询返回聚合字段；新增启停与授权状态查询。
- `insight/execution-trace`: 工具调用记录新增"按工具查询与聚合"能力，与现有按运行查询并存。

## Impact

- 前端：新增 `views/ToolListView.vue`、`views/ToolDetailView.vue`，可能新增 `components/ToolSpecEditor.vue`；改动 `router/index.ts`、`App.vue` 的 `navItems`、`api/endpoints.ts`；因为后端 schema 变化需重新生成 `api/schema.d.ts`。
- 后端：`contexts/catalog/api/{routes,schemas}.py`、`application/tool_service.py`、`infrastructure/repositories.py`（授权查询复用 `ToolGrantRepository.list`）；`contexts/insight/api/routes.py`、`application/*`、`infrastructure/repositories.py`（新增按工具查询）。
- 契约：OpenAPI 只增字段与端点，不破坏既有调用方；`ToolResponse` 新增字段对旧客户端是加法。
- 数据库：一个加法迁移（`0006`），为 `insight.tool_call` 增加 `(tenant_id, tool_id, started_at)` 索引，支撑按工具的时间窗口查询；不新增表、不改列。
- 权限：不新增权限点，沿用 `TOOL_READ`/`TOOL_MANAGE`；调用记录查询只返回摘要，不需要 `INSIGHT_READ_CONTENT`。
- 与其他变更的关系：`mcp-tool-integration`（未实施）会扩展 `ToolType` 与 spec 形状，本变更的表单按 `type` 分支并对未识别类型退化为 JSON，避免与其冲突；`prompt-console` 的只读降级与版本不可变约定在此复用，不重复定义。
- 既有 spec 文本冲突（需后续对齐）：`catalog/tool-registry` 的「租户授权」需求写的是"授权给指定租户"，而实现只能对本租户自授。本变更不解决跨租户授权，控制台文案按实际语义呈现，该需求措辞的修正留给租户治理相关变更。
