# Proposal

## Why

知识库能力已明确**不由本平台建设**，改由公司知识中台承担，平台侧通过 **MCP** 接入其检索能力。但当前工具注册只支持 `HTTP` 一种接入类型，`ToolType` 枚举与执行器都只认识 HTTP 请求，MCP Server 既无法注册也无法被模型调用——也就是说"通过 MCP 接知识中台"这条路在代码里并不存在。

同时，仓库内三处文档对知识库的表述与实际决策不一致：PRD §4.6 仍把知识库列为本平台模块，`README.md` 与 `agent-platform-foundation` 提案都写"保留 `KnowledgePort` 端口占位"，而代码中并没有这个端口。范围与实现口径需要一次性对齐，否则后续知识中台对接会以错误的前置假设开始。

## What Changes

- **工具接入类型扩展**：`ToolType` 增加 `MCP`，工具创建接口接受可选 `type`（缺省仍为 `HTTP`，既有调用方与已注册工具不受影响）。
- **按类型分派的契约校验**：把现有 `validate_spec` 拆为 HTTP 与 MCP 两套校验。MCP 契约包含 `transport`（`streamable_http` / `sse`）、`url`、`tool_name`、`timeout_s`、`auth`（沿用 `secret_ref` 引用）与可选的 `input_schema` / `output_schema`。
- **按类型分派的执行**：`ToolService.execute` 依据工具类型选择执行器；MCP 分支建立会话、调用远端工具、把结果归一为与 HTTP 工具一致的输出，并复用入参校验、输出契约校验、超时上限、租户授权与高风险拦截。
- **数据模型**：`catalog.tool.type` 当前是 `varchar(32)` 且没有 CHECK 约束（已对本地库核实），新增枚举值不需要 DDL，本变更不新增迁移、也不顺手引入 DB 级约束；类型合法性继续由应用层的枚举与接口校验保证。
- **依赖**：新增官方 `mcp` Python SDK。
- **范围文档修订**：PRD §4.6 与 §11.1、`README.md` 能力说明、`agent-platform-foundation` 提案中的"知识库/`KnowledgePort` 占位"统一改为"知识库不自建，通过 MCP 工具接入知识中台"。

**明确非目标**

- 不新增 `KnowledgePort` 或任何检索专用端口/接口，不为 RAG 预留领域模型。
- 不做文档接入、解析、切分、向量化、索引、Rerank、ACL 过滤——这些由知识中台负责，平台只记录"调了哪个 MCP 工具、参数是什么、耗时多少、结果多大"。
- 不支持 `stdio` 传输：平台进程内拉起第三方子进程的隔离与资源治理成本过高，首版只接受远程 HTTP/SSE 端点。
- 不做 MCP `tools/list` 批量导入：一个 MCP 工具记录对应远端一个 `tool_name`，知识中台的多个能力按需注册为多条工具，以保留现有的授权与风险分级粒度。
- 不改造 Runtime：MCP 工具走既有 `ToolPort`，执行预算、轨迹记录、错误码映射不变。

## Capabilities

### New Capabilities

无。本变更扩展的是既有工具注册能力，不引入新的领域边界。

### Modified Capabilities

- `catalog/tool-registry`: 工具接入类型由"仅 HTTP"扩展为"HTTP + MCP"；新增 MCP 契约校验、MCP 调用执行与错误映射；原有 HTTP 工具的全部行为与验收标准保持不变。

## Impact

- **代码**：`contexts/catalog/domain/{tool.py,enums.py,errors.py}`、`contexts/catalog/application/tool_service.py`、`contexts/catalog/api/schemas.py`、新增 `contexts/catalog/infrastructure/mcp_client.py`、`contexts/execution/infrastructure/tool_adapter.py`、`pyproject.toml`。
- **API**：`POST /api/v1/tools` 与 `POST /api/v1/tools/{id}/versions` 的 `spec` 允许 MCP 形态，新增可选 `type` 字段；其余端点与响应结构不变，`ToolResponse.type` 会新增一个枚举值。
- **数据**：无表结构变更、无迁移、无数据回填；`tool.type` 只是多出一个可写入的枚举值。
- **依赖**：新增 `mcp` SDK；不引入 Milvus / MinIO / 文档解析库。
- **文档**：`docs/enterprise_agent_platform_12_modules_prd.html`、`README.md`、`openspec/changes/agent-platform-foundation/proposal.md` 需同步修订。
- **风险**：
  - 归档顺序：`catalog/tool-registry` 的主规格要等 `agent-platform-foundation` 归档后才生成，本变更应在 foundation 归档之后（或紧随其后）归档，否则 delta 无对应主规格可合并。
  - 远端 MCP 服务不可信：必须强制超时上限、输出契约校验与授权检查，错误一律收敛为平台错误码，不把远端原始响应透给调用方。
  - MCP SDK 版本迭代较快，需锁定兼容范围并在设计中固定所用传输客户端。
