# Design

## Context

现状（读取自代码，非假设）：

- 工具契约只有 9 个端点，集中在 `backend/src/agent_platform/contexts/catalog/api/routes.py` L234–320；请求/响应模型在 `api/schemas.py` L138–185。
- `ToolResponse` 仅 `id/code/name/description/type/status`（`schemas.py` L146–154），无风险等级、版本、授权状态。
- 授权是**本租户自授**：`tool_service.grant` 用 `actor.tenant_id`（`tool_service.py` L150–161）；`check_invocable` 要求 tool 为 `ACTIVE` 且本租户已 grant（L171–187）。`ToolGrantRepository.list` 已存在（`infrastructure/repositories.py` L246–251）但无路由。
- 前端 `api/endpoints.ts` 无任何 tool 函数；`router/index.ts` 无 tool 路由；`App.vue` 的 `navItems` 无工具菜单。`api/schema.d.ts` 由 `frontend/scripts/gen-api.mjs` 依据后端 OpenAPI 生成（`npm run gen:api`）。
- 调用记录：`insight.tool_call`（`insight/infrastructure/tables.py` L54–87），索引为 `(tenant_id, run_id, started_at)` 与 `(tenant_id, trace_id)`，**没有 tool 维度索引**；`ToolCallRepository` 只有 `list_for_run`（`insight/infrastructure/repositories.py` L42–50）；`CallStatus` 仅 `SUCCESS|FAILED`（`insight/domain/enums.py`）。
- 迁移最新为 `0005_insight.py`，下一个编号 `0006`。
- 架构方向：catalog 不依赖 insight（execution 通过 adapter 依赖 catalog），因此工具调用查询放在 insight 侧，与 `/metrics/runs` 同侧。

## Goals / Non-Goals

**Goals:**

- 让工具的可绑定前提（启停、授权）在列表与详情中可直接判定，不依赖人工推测错误码。
- 后端增量保持纯加法：旧客户端不感知、执行链路语义不变。
- 前端 spec 编辑在"结构化表单"与"任意 JSON"之间有序退化，MCP 落地时不返工。

**Non-Goals:**

- 不修改 `grant` 的租户语义、不引入跨租户授权。
- 不改 `ToolService.execute` 的校验顺序、错误码与超时语义。
- 不为测试调用伪造运行上下文。
- 不引入前端 JSON Schema 校验引擎。

## Decisions

### D1 在 `ToolResponse` 上扩聚合字段，而不是新增 detail 端点

列表与详情共用同一响应模型，新增 `risk_level`、`latest_version_no`、`granted`、`updated_at`。

- 备选：新增 `GET /tools/{id}/detail` 聚合端点 → 列表仍是薄模型，前端两套模型与两套渲染分支，收益不足以抵消成本。
- 备选：前端对每个工具补拉 `versions` 与 `grant` → 列表 N+1，直接否决。

### D2 聚合字段在 service 层组装，SQL 只用固定次数

`ToolService.list/get` 改为返回带聚合的视图对象（dataclass），路由层映射 schema；schema 保留 `from_attributes`，但不再直接把 ORM 实体丢给 `model_validate`。

取数顺序（每次列表固定 3 条查询，与工具数无关）：

1. `ToolRepository.list(tenant_id)`
2. 批量取最新版本：`ToolVersionRepository.latest_for_tools(tool_ids)`，SQL 用 `DISTINCT ON (tool_id) ... ORDER BY tool_id, version_no DESC`
3. `ToolGrantRepository.list(tenant_id)`，在内存映射为 `{tool_id: granted_at}`

- 备选：DB 视图/物化视图 → 迁移与刷新成本高，对一个控制台列表过度设计。
- 备选：逐工具 `latest()` → N+1，否决。

### D3 启停端点返回更新后的工具视图

`PATCH /api/v1/tools/{tool_id}/status`，body `{"status": "ACTIVE"|"DISABLED"}`，`TOOL_MANAGE`，返回 200 + 聚合后的 `ToolResponse`。

- 返回视图而非 204：前端无需二次请求即可同步状态与授权态。
- 幂等：目标状态等于当前状态时直接返回当前视图，不报错（spec 要求）。
- 停用**不级联删除授权**：重新启用后原授权仍然有效，避免"停用一次就永久失去授权"的意外。
- 执行侧无需改动：`check_invocable` 已拒 `DISABLED`（`tool_service.py` L182），Agent 版本绑定的 `_resolve_tools` 也已拒非 `ACTIVE`（`agent_service.py` L295–342）。

### D4 授权状态查询单列端点，未授权返回 200 而非 404

`GET /api/v1/tools/{tool_id}/grant` → `{granted, granted_at, granted_by}`，`TOOL_READ`；工具不存在才 404。

- 备选：只在 `ToolResponse.granted` 上给布尔 → 详情页要展示授权时间与授权人，仍得加字段，不如独立端点语义清晰。
- 未授权返回 404 会迫使前端把"未授权"与"工具不存在"混为一谈，否决。

### D5 调用记录查询放 insight，用 `/tool-calls` 而不是 `/tools/{id}/calls`

`GET /api/v1/tool-calls?tool_id=&tool_version_id=&start=&end=&limit=`，`TOOL_READ`，返回 `{tool_id, window_start, window_end, total, succeeded, failed, success_rate, avg_latency_ms, errors: [{code, count}], items: [...]}`；`total == 0` 时 `success_rate` 为 `null` 而非 0，供前端区分"无数据"与"全失败"。

- 备选：`/tools/{tool_id}/calls` 放 catalog → catalog 需要 import insight 的 service，违反上下文依赖方向；且 catalog 里已有 adapter 边界，反向依赖会破坏它。
- 权限用 `TOOL_READ` 而非 `RUN_READ`：视图是工具维度、且只返回摘要；`RUN_READ` 留给运行轨迹（含正文策略）。这要求响应模型**不包含** `request_body`/`response_body`，也不接受"带正文"参数。
- 默认窗口 7 天、`limit` 默认 20 上限 100；`start >= end` 抛 `ValidationFailedError.for_field`（复用 `MetricsService` 的做法）。

### D6 版本变更检测：前端规范化比较，后端 `config_hash` 作为兜底

前端把表单状态与最新版本归一化为同一结构（key 排序、空 `headers`/`auth.type=none` 归为缺省）后比较，决定保存按钮可用性；**不**在前端复算 `config_hash`（后端用 `normalized` spec，见 `tool_service.py` L129–143）。

若仍提交了等价内容，后端会生成 `config_hash` 相同的新版本——这是既有行为，不修改。前端在收到响应后比较 `config_hash`：与上一版本相同则提示"与上一版本等价"，不做假成功。

- 备选：前端复算 hash → 归一化逻辑会与后端漂移，纯收益为负。

### D7 spec 编辑器用"基底 + 覆盖"，不重建 spec

编辑状态 = 原始 spec 深拷贝；表单只覆盖它负责的键（`method`、`url`、`timeout_s`、`headers`、`auth`）；`input_schema`/`output_schema` 由两个文本域提供，提交时 `JSON.parse` 后覆盖。原始 spec 中未被表单接管的键原样保留，并在界面上以只读"其它字段"提示其存在。

- 理由：直接按表单重建 spec 会静默丢弃未知键（例如 `mcp-tool-integration` 将来引入的字段），属于数据损坏。
- 类型分支：`type == "HTTP"` 走表单；其它类型（含将来的 MCP）走整段 JSON。判定依据是 tool 的 `type` 字段，不是 spec 内容。
- 错误映射：后端 `details[].field` 形如 `spec.url`、`spec.input_schema`、`spec.op_kind`，前端按前缀映射到对应控件；无法映射的落到表单顶部的错误汇总。

### D8 认证配置：凭据选择器可选，缺失 `SECRET_MANAGE` 时降级

`GET /api/v1/secrets` 需要 `SECRET_MANAGE` 且返回 `Page[SecretResponse]`。前端在 `session.can('secret:manage')` 为真时加载列表（只取 `id`/`name`/`status`），否则显示提示并允许直接粘贴 `secret_ref_id`。

- 备选：新增"仅工具可用的凭据下拉"端点 → 新增权限面，超出本次范围。
- `auth.type == "none"` 时提交 `{"type": "none"}`，不携带 `secret_ref_id`。

### D9 高风险测试调用的确认是 UX 约束，不是权限

前端对 `risk_level == "HIGH"` 的版本要求勾选确认后才允许提交，提交时带 `allow_high_risk: true`。不新增审批表、不改后端错误语义；后端仍以 `HighRiskToolError` 拒绝未放行的高风险调用。

### D10 未授权/已停用的前置提示

详情页在 `granted == false` 或 `status == "DISABLED"` 时禁用测试按钮，并给出可点击的引导（"先授权" / "先启用"）。不预判后端会返回什么错误码，也不吞掉后端错误——用户绕过提示直接触发时仍展示 `RESOURCE_UNAVAILABLE` 明细。

### D11 测试调用不落库，界面上明确写清

`insight.tool_call` 的 `run_id` 是外键且非空，控制台探活没有运行上下文。界面在测试面板固定展示一句说明，并在调用概览空状态下区分"从未被 Agent 调用"。

- 备选：新建一条 synthetic run 再落记录 → 污染运行指标与轨迹，否决。

### D12 前端结构：两个页面 + 一个编辑器组件

- `views/ToolListView.vue`：列表、过滤、新建对话框（沿用 `PromptListView.vue` 的 `el-table` + `el-dialog` + `FormRules` 写法）。
- `views/ToolDetailView.vue`：概览、版本、授权/启停、测试面板、调用概览。
- `components/ToolSpecEditor.vue`：表单 + 两个 Schema 文本域，`v-model` 绑定到 spec 对象，`defineExpose` 暴露校验结果给宿主页面。
- `api/endpoints.ts` 增加 `listTools/getTool/createTool/addToolVersion/listToolVersions/updateToolStatus/getToolGrant/grantTool/revokeTool/executeToolVersion/listToolCalls/listSecrets`；类型全部来自重新生成的 `schema.d.ts`，不手写模型。
- 权限判断沿用 `useSessionStore().can('tool:manage')`。

## Risks / Trade-offs

- **前端归一化与后端 `normalized` spec 漂移，导致"禁用保存"判断与实际不一致** → 只作为 UX 优化；真正兜底是 `config_hash` 相同提示与版本不可变，最坏结果是多一个等价版本，不损坏数据。
- **表单重建丢失未知 spec 键** → D7 的"基底 + 覆盖"策略 + 未知键只读提示；新增键必须显式加入表单白名单才会被覆盖。
- **`DISTINCT ON` 是 PostgreSQL 专有语法** → 项目本身已用 `percentile_cont`（`insight/.../repositories.py` L78）与 JSONB，绑定 PostgreSQL 属既有前提；若需可移植可退化为两次查询（先取 `max(version_no)` 再 join）。
- **按工具查询在大表上慢** → 新增 `0006` 索引 `(tenant_id, tool_id, started_at)`；`start` 缺省 7 天，`limit` 有上限。
- **停用工具会让运行中的 Agent 失败** → 详情页明确警告；不阻断已有 Agent 版本（快照不可变是既有契约），也不回滚。
- **`insight.tool_call.tool_id` 可空**（失败在解析阶段时可能为空） → 按 `tool_id` 查询会漏掉这类记录；这是既有写入行为，不在本变更修改，调用概览的"无数据"文案不承诺覆盖全部失败。
- **与 `mcp-tool-integration` 并发** → 该变更未实施；本设计只依赖 `ToolType` 字段分支与"未知类型退化为 JSON"，两者不冲突。若 MCP 先落地，需复核 `create` 请求的 spec 校验路径（`validate_spec`）是否仍接受表单产出的结构。
- **`ToolResponse` 扩字段影响类型生成与既有测试** → 重新执行 `npm run gen:api`；后端集成测试若断言了响应字段全集需同步更新（加法不应破坏，但要验证）。

## Migration Plan

1. 后端契约与聚合查询（catalog），跑既有工具集成测试（`backend/tests/integration/test_tool_executor.py`、`test_tool_port.py`）确认无回归。
2. 后端调用查询 + `0006` 迁移，验证 `alembic upgrade head` 与回滚（`downgrade` 只删索引）。
3. 重新生成 `frontend/src/api/schema.d.ts`（`npm run gen:api`），再写 `endpoints.ts` 封装。
4. 前端页面与路由/导航，最后接权限与降级分支。
5. 回滚策略：前端可独立回滚（删除路由与导航项即可，后端字段是加法）；后端回滚只需回退路由注册与迁移索引，无数据变更。

## Open Questions

- 调用概览的默认窗口（7 天）与条数（20）是否需要做成可配置——可由产品在实施后调整，不影响接口形态。
- 列表是否需要"仅看未授权/已停用"的快捷筛选——属交互增强，spec 未承诺。
- MCP 落地后是否给 MCP 工具提供结构化表单——取决于该变更最终的 spec 形状，届时再评估。
