# Design

## Context

现状（已核实）：

- `ToolType` 枚举只有 `HTTP`，`ToolService.create` 把类型硬编码为 `HTTP`，工具创建请求里没有 `type` 字段。
- `catalog/domain/tool.py:validate_spec` 只实现 HTTP 契约：`method` / `url` / `timeout_s`（上限 60s）/ `headers` / `auth`（`none|bearer|header` + `secret_ref_id`）/ `input_schema` / `output_schema`。
- `ToolService.execute` 在 `session_scope` 内完成授权与风险检查，然后直接用 `httpx.AsyncClient` 发请求，失败映射为 `ToolExecutionError`（`TOOL_HTTP_ERROR`）、`ToolTimeoutError`、`ToolOutputInvalidError`。
- 执行侧通过 `CatalogToolPort.invoke` 把上述异常转成 `ToolResult(ok=False, error_code=...)`，轨迹由 execution 的 `CallRecorder` 写入 `insight.tool_call`（含脱敏摘要）。
- `catalog.tool.type` 实际是 `varchar(32)`，**没有 CHECK 约束**（`pg_constraint` 里只有主键与唯一键），因此新增枚举值不涉及 DDL。

约束：不改 Runtime 执行流程、不改 `ToolPort` 契约、不改工具授权与风险分级的语义；知识库本身不由本平台建设（见 proposal.md - Why）。

## Goals / Non-Goals

**Goals:**

- 让一个已发布的 MCP Server 上的单个远端工具，能被注册成本平台的一条工具记录，并被 Agent 版本绑定、被模型选择、被执行、被记录。
- MCP 与 HTTP 在"授权、风险、超时、入出参契约、调用记录"五个维度上行为一致，运维与排查不需要区分两套心智。
- HTTP 注册与执行路径零行为变化。

**Non-Goals:**

- 不做 `tools/list` 批量导入、不做远端工具清单同步、不做 MCP Server 生命周期托管。
- 不支持 `stdio` 传输，不拉起子进程。
- 不做连接池/会话复用（首版每次调用新建会话）。
- 不新增知识相关端口、领域对象或检索接口。
- 不做 MCP 工具的专用控制台界面。

## Decisions

### D1：一个工具记录 = 远端一个 `tool_name`

`spec.tool_name` 指定远端方法，`spec.url` + `spec.transport` 指定服务端。

- 备选：注册"一个 MCP Server"再批量导入其工具清单。放弃原因：导入会产生清单漂移（远端增删工具后本地过期）、需要额外的同步与版本策略，且会破坏现有"一个工具一个版本、一个授权、一个风险等级"的模型。
- 代价：知识中台若暴露多个能力，需要注册多条工具记录（可接受，授权粒度反而更细）。

### D2：MCP 契约复用 HTTP 的字段名，用 `transport` 区分

```json
{
  "transport": "streamable_http",
  "url": "https://knowledge.example.com/mcp",
  "tool_name": "search_documents",
  "timeout_s": 15,
  "auth": {"type": "bearer", "secret_ref_id": "..."},
  "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
  "output_schema": {"type": "object"}
}
```

- `url` / `timeout_s` / `auth` / `input_schema` / `output_schema` 与 HTTP 工具同名同义，校验函数可复用。
- `type` 只存于 `tool` 表，不写进 `spec`，避免一处状态两个来源。
- 校验入口保持单一：`validate_spec(spec, tool_type)` 内部分派 `_validate_http` / `_validate_mcp`，`ToolService.create` 与 `add_version` 都传类型。

### D3：只允许远程 HTTP 类传输

`transport` 取值 `streamable_http` | `sse`。`stdio` 明确拒绝。

- 理由：`stdio` 需要在 API 进程内 spawn 子进程，容器权限、资源配额、崩溃隔离都不可控；知识中台是远程服务，没有 `stdio` 需求。
- 安全基线：`url` 必须是绝对 `http(s)`；`tool_name` 非空且长度受限；`timeout_s` 复用 60s 上限。

### D4：每次调用新建会话，不做连接池

执行路径：建立传输 → `initialize` → `call_tool` → 关闭；整体包在 `asyncio.timeout(timeout_s)` 内，避免只对底层 HTTP 客户端设超时而漏掉握手阶段。

- 备选：按 `tool_version_id` 缓存 `ClientSession`。放弃原因：远端空闲断连、重连与并发复用需要额外状态机，收益（省一次握手）远小于复杂度；MCP 调用本身耗时在百毫秒级，握手占比有限。
- 后续若出现高频调用，再以独立变更引入缓存，不影响本变更的 spec。

### D5：结果归一为"可被 output_schema 校验的值"

远端返回的 `content` 数组处理规则：

1. 全部为 text 片段 → 拼接为一个字符串；
2. 只有一条 text 且可 JSON 解析 → 用解析后的值参与 `output_schema` 校验与返回；
3. 含 image/resource 等非文本内容 → 转为 `{"type": ..., "summary": ...}` 的结构化摘要，不把二进制塞进结果。

这样 HTTP 与 MCP 工具对 Runtime 都表现为"结构化或文本结果"，`json.dumps` 那条既有分支无需改动。

### D6：错误码新增 `TOOL_MCP_ERROR`，只加一个映射分支

- 新增 `ToolMcpError(AppError)`，`code = "TOOL_MCP_ERROR"`，`http_status = 502`。
- 超时仍抛 `ToolTimeoutError`；远端 `isError=true` 与协议/连接错误抛 `ToolMcpError`；输出不符契约仍抛 `ToolOutputInvalidError`。
- `CatalogToolPort.invoke` 增加一个 `except ToolMcpError` → `error_code="TOOL_MCP_ERROR"`。这是适配层映射，不改执行流程、不改 `ToolPort`。
- 不对失败调用自动重试（写操作副作用不确定）。

### D7：MCP 客户端隔离在 infrastructure，可注入替身

新增 `catalog/infrastructure/mcp_client.py`，暴露一个窄协议：

```
class McpSession(Protocol):
    async def call_tool(self, *, url, transport, tool_name, arguments, headers, timeout_s) -> McpCallResult: ...
```

`ToolService` 构造时接受可选 `mcp_session`（与现有 `transport: httpx.AsyncBaseTransport` 的测试注入风格一致）。真实实现基于官方 `mcp` SDK 的 `streamablehttp_client` / `sse_client`，依赖锁定为 `mcp>=1.2,<2`。

- 理由：协议演进快，把 SDK 隔离在一个文件里，替换成本低；测试可用注入替身覆盖成功/超时/远端报错，不必真起 MCP 服务（另留一条用 SDK 起本地端点的集成用例验证真实协议）。

### D8：不新增数据库迁移

已核实 `catalog.tool.type` 为 `varchar(32)` 且无 CHECK 约束，`ToolType.MCP` 直接可写。本次不加 DB 约束（保持与现有列一致），避免为了一个枚举值引入与 `metadata.create_all` 迁移风格不一致的手写 ALTER。

## Risks / Trade-offs

- [MCP SDK 与 `langgraph` / `pydantic` 版本冲突] → 固定 `mcp>=1.2,<2`，装依赖后跑全量测试；冲突无法解决时回退到手写 HTTP+JSON-RPC 客户端（契约与 spec 不变）。
- [远端工具改名或下线，本地注册仍存在] → 调用期失败并保留 `tool_name` 与远端错误摘要；不做发布前网络探测（会把部署耦合到远端可用性）。
- [每次调用握手增加延迟] → 首版接受；记录 `latency_ms` 以便后续判断是否需要连接复用。
- [MCP 返回结构差异大，`output_schema` 校验可能过严] → 注册时 `output_schema` 可选；不填则只做类型归一不做校验。
- [远端服务不可信] → 强制超时、输入输出校验、授权与高风险拦截前置，错误不透传原始响应。
- [归档顺序] → `catalog/tool-registry` 主规格要等 `agent-platform-foundation` 归档后生成，本变更需在其后归档，否则 delta 无处合并。

## Migration Plan

无数据迁移。

上线：

1. 安装依赖（`mcp`）并重装 `-e ".[dev]"`；
2. 部署后端，`POST /api/v1/tools` 以 `type=MCP` 注册知识中台的检索工具，`POST /tools/{id}/grant` 授权给业务租户；
3. 建 Agent 版本时绑定该工具，发布后从调试台发起一次真实检索调用，确认 `tool_call` 记录里出现远端 `tool_name`。

回滚：代码回滚即可。已注册的 MCP 工具记录会因枚举值消失而无法反序列化，需在回滚前把相关 Agent 版本解除绑定（数据量小，手工处理）；HTTP 工具不受影响。

## Open Questions

- 知识中台的 MCP Server 最终使用 `streamable_http` 还是 `sse`、认证方式是对端签发的静态 token 还是别的形式——两种传输与凭据形态本设计都已覆盖，等对端确定后只需在注册时选择，不改变实现结构。
