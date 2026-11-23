# Tasks

> 归档顺序：本变更的 spec delta 使用 `MODIFIED Requirements`，需要 `agent-platform-foundation` 先归档并生成 `openspec/specs/catalog/tool-registry` 后才能归档；先归档本变更会被拒绝（target spec does not exist）。

## 1. 契约与枚举

- [x] 1.1 `ToolType` 增加 `MCP`，保持既有 `HTTP` 值不变（`contexts/catalog/domain/enums.py`）
- [x] 1.2 `validate_spec` 改为按工具类型分派：`_validate_http` 保持现有逻辑原样，新增 `_validate_mcp`
- [x] 1.3 `_validate_mcp` 校验：`transport ∈ {streamable_http, sse}`（拒绝 `stdio`）、`url` 为绝对 `http(s)`、`tool_name` 非空且 ≤128、`timeout_s` 复用 60s 上限、`auth` 复用现有 `secret_ref_id` 联合类型、`input_schema`/`output_schema` 可选
- [x] 1.4 单测覆盖：合法 MCP 契约、缺 `tool_name`、声明 `stdio`、非法 `url`、超时越界、未知字段忽略

## 2. API 与创建路径

- [x] 2.1 `ToolCreateRequest` 增加可选 `type`（缺省 `HTTP`），`ToolResponse` 与列表响应返回真实类型（`contexts/catalog/api/schemas.py`、`routes.py`）
- [x] 2.2 `ToolService.create` 使用请求中的类型，不再硬编码 `HTTP`；`add_version` 按工具已有类型校验 spec
- [x] 2.3 集成测试：`type=MCP` 创建成功且回读为 `MCP`；不传 `type` 仍创建 `HTTP`；HTTP 工具版本校验行为不变

## 3. MCP 客户端与执行

- [x] 3.1 新增 `contexts/catalog/infrastructure/mcp_client.py`：定义 `McpSession` 协议与 `McpCallResult`，实现基于 `mcp` SDK 的 `streamable_http` / `sse` 两种传输，整个会话包在超时内
- [x] 3.2 结果归一：text 片段拼接；单条 text 且可 JSON 解析时取解析值；非文本内容转结构化摘要
- [x] 3.3 新增 `ToolMcpError`（`TOOL_MCP_ERROR`，502），远端 `isError`、协议/连接失败使用它；超时与输出不符契约沿用既有异常
- [x] 3.4 `CatalogToolPort.invoke` 增加 `ToolMcpError → TOOL_MCP_ERROR` 映射分支（仅映射，不改执行流程）
- [x] 3.5 `ToolService.execute` 拆出 `_execute_http`（既有代码平移）与 `_execute_mcp`，按工具类型分派；`ToolService` 支持注入 `mcp_session` 供测试替身使用
- [x] 3.6 单测：三种结果归一形态、`isError` 映射、超时映射、连接异常映射
- [x] 3.7 集成测试（注入替身）：成功调用、远端报错、超时、未授权租户、高风险未放行、输出不符契约

## 4. 真实协议验证

- [x] 4.1 用 `mcp` SDK 在测试进程内起一个本地 MCP server（`streamable_http`），暴露一个确定性的检索工具
- [x] 4.2 集成用例：注册指向该本地 server 的 MCP 工具 → 授权 → 真实调用一次，断言返回值与 `insight.tool_call` 记录中的远端 `tool_name`、耗时、状态
- [x] 4.3 e2e 用例：MCP 工具 + Agent 版本绑定 → 发布 → 同步调用 → 轨迹中同时出现模型调用与 MCP 工具调用（同一 `trace_id`）

## 5. 范围与文档对齐

- [x] 5.1 `docs/enterprise_agent_platform_12_modules_prd.html`：§4.6 改写为"知识库不自建、通过 MCP 接入知识中台"，§11.1 标注该项为集成而非建设，12 模块表中 #6 调整状态口径
- [x] 5.2 `README.md`：能力说明中"知识库"改为"MCP 外挂集成"，工具章节补 MCP 注册说明与一条 `curl` 示例（含 `spec` 样例）
- [x] 5.3 `openspec/changes/agent-platform-foundation/proposal.md`：把"保留 `KnowledgePort` 端口占位"改为"MCP 工具接入知识中台"，避免与代码事实不符
- [x] 5.4 在 `docs/enterprise_agent_platform_12_modules_prd.html` 记录本次范围决策日期与结论（一句话即可）

## 6. 回归与验收

- [x] 6.1 全量 `pytest` 通过，HTTP 工具相关用例零改动零跳过
- [x] 6.2 确认本变更未新增迁移：`alembic heads` 单一（当前 `0006_tool_call_tool_index`，由 `tool-console` 引入），`alembic current` 与该 head 一致
- [x] 6.3 `scripts/check_deps.py` 通过（新增 `mcp` 依赖可导入）
- [x] 6.4 `openspec validate mcp-tool-integration --strict` 通过
