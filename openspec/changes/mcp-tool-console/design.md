# Design

## Context

动机见 `proposal.md - Why`。这里只记录影响实现方式的现状约束：

- `catalog` 已经把 `MCP` 作为一等接入类型：`ToolCreateRequest.type` 接受 `HTTP | MCP`，`validate_spec()` 按类型分派到 `_validate_http` / `_validate_mcp`，`_execute_mcp()` 走 `SdkMcpSession`，`POST /tool-versions/{id}/execute` 已存在且被 `ToolTestPanel` 使用。
- `_validate_mcp()` 要求 `tool_name` 非空，因此**不能直接复用它校验探测请求**——探测发生在"还没选定远端工具"之前。
- `McpSession` 端口目前只有 `call_tool()` 一个方法，`_open_transport()` 负责把 `streamable_http` / `sse` 收敛到两处 SDK 调用，其它传输方式天然不成立。
- 鉴权解析在 `ToolService._build_headers()` 里，是实例私有方法。
- 平台已有探测先例可参照：`model_service.probe()` + `ProbeVerdict`（稳定字符串枚举、前端不做二次推断）+ `POST /models/{id}/probe`。
- 前端现状：`ToolListView` 提交时 `type: 'HTTP'` 写死；`ToolSpecEditor` 对非 HTTP 类型退化为裸 JSON textarea；`ToolDetailView` 已传 `:type="tool.type"`，但版本对比的字段列表是 HTTP 专属常量；`ToolTestPanel` 已具备版本选择、风险确认、granted/active 门禁，且与接入类型无关。
- `ToolService.execute()` 的前置 `check_invocable()` 要求工具 `ACTIVE` 且已授权给租户——测试面板的门禁与之对齐，本期不改。

## Goals / Non-Goals

**Goals:**

- 让 `MCP` 工具在控制台可创建、可结构化配置、可在注册前探测远端契约、可对比版本变更、可带模板试调用
- 探测与保存共用同一套端点校验与鉴权解析，避免"探测能连、保存后 401"这类错位
- 不引入新表、新状态机、新执行路径

**Non-Goals:**

- 不在控制台开放 `sse` 选项（后端契约保留，存量工具不受影响）
- 不提供 `stdio` 传输（平台不托管远端进程，这是既有约束）
- 不做 OpenAPI/Swagger 导入、不做 tool-hub、不做工具审核状态机与工具集
- 不持久化探测结果，不新增探测审计表

## Decisions

### D1：探测以「未保存的端点配置」为入参，不引入草稿实体

`POST /tools/probe-mcp` 接收 `{transport, url, auth, headers, timeout_s}`，返回 `{verdict, tools[], latency_ms, error_summary}`。

- 为什么不用 `tool_id`：探测发生在创建工具之前，工具还不存在。
- 备选与代价：「先建工具再探测」会产生一批垃圾工具与空版本，且需要清理逻辑；「引入草稿表」要新增状态机与生命周期，明显超出本期。
- 探测返回的 `tools[]` 每项含 `name`、`description`、`input_schema`，正好对应 `MCP` 契约里需要人工填写、也最容易填错的三个字段。

### D2：把端点校验从 `_validate_mcp()` 里抽出来复用，而不是给探测复制一份

`_validate_mcp()` 拆分顺序：先校验 `transport / url / timeout_s / headers / auth / schemas`，再校验 `tool_name`。前一部分提为 `_validate_mcp_endpoint()`，`_validate_mcp()` 与探测共用。

- 理由：两份校验逻辑必然漂移，漂移的表现是"探测通过了，保存却被拒"，这类不一致最难排查。
- 探测对 `schemas` 不校验（探测本身不携带 Schema，Schema 是探测的产出）。

### D3：探测结论用独立枚举，不复用 `ProbeVerdict`

新增 `McpProbeVerdict`：`OK`、`MCP_PROBE_AUTH_FAILED`、`MCP_PROBE_TIMEOUT`、`MCP_PROBE_UNREACHABLE`、`MCP_PROBE_PROTOCOL_ERROR`。

- 不复用 `ProbeVerdict`：它的取值全是 `MODEL_*` 语义（`MODEL_NOT_FOUND` 等），混用会让前端出现对 MCP 无意义的结论。
- 沿用既有约定：序列化即稳定字符串，前端只做展示映射，不做二次推断。
- 空清单不算失败：`verdict=OK` 且 `tools=[]`，与 spec 一致；否则前端无法区分"连上了但没工具"和"没连上"。

### D4：`list_tools` 加在 `McpSession` 端口上，不在路由层直接建客户端

`McpSession` 协议新增 `list_tools(*, url, transport, headers, timeout_s) -> list[McpToolDescriptor]`，`SdkMcpSession` 用同一个 `_open_transport()` 与同一个超时预算实现。

- 理由：与 `call_tool` 共享传输收敛点与超时语义；测试可注入替身；路由层不直接依赖 mcp SDK（保持既有"契约层与 SDK 解耦"的分层）。
- `_open_transport()` 只认 `streamable_http` / `sse`，`stdio` 在传输层就不成立，无需额外分支。

### D5：鉴权解析提为共享函数，探测与执行走同一条路径

把 `ToolService._build_headers()` 提为模块级 `build_auth_headers(tenant_id, spec)`，`ToolService._execute_*` 与探测用例都调用它。

- 理由：探测如果自己解析 secret，很容易漏掉 `header` 类型（`auth.name`）或 Bearer 前缀，结果是探测 401、实际调用正常，或反之。
- 代价：探测必须拿到 `ActorContext` 以确定 `tenant_id`——这与权限校验天然一致，不构成额外负担。

### D6：探测权限 = `TOOL_MANAGE`，并限定输出面

探测允许持有 `TOOL_MANAGE` 的用户让服务端连接任意 `http(s)` 地址。这与"创建工具后可指向任意 URL 并测试调用"的现有能力等价，本期不放宽也不额外收紧。

限定措施：不透出远端原始响应体（只给结论 + 截断摘要）、超时上限沿用 `MAX_TIMEOUT_S`、错误统一映射为 `McpProbeVerdict`。

探测不落库，因此**不产生审计行**——这是本期的已知缺口，记入 Risks，不在此处私自加表。

### D7：MCP 用结构化表单，但保留「完整 JSON」作为逃生口

`ToolSpecEditor` 的非 HTTP 分支由"裸 JSON 唯一形态"改为：结构化表单（传输方式、服务端地址、远端工具名、超时、鉴权、请求头、入参/出参 Schema）+ 可折叠的完整 JSON。

- 不直接删掉裸 JSON：MCP 侧的字段会演进，删掉会让前端成为接入瓶颈；但默认形态必须是表单，否则 `tool_name` 漏填这类错误仍旧无法定位。
- 表单未识别到的键继续走既有的"原样保留 + 提示"逻辑，与 HTTP 分支保持一致。

### D8：接入类型在新建弹窗选择，切换时重置 spec 骨架

`ToolListView` 提交时使用表单里的 `type`；切换类型时把 spec 重置为该类型的默认骨架（`HTTP`: `method/url/headers`；`MCP`: `transport/url/tool_name`）。

- 必要性来自后端行为：`_validate_mcp()` 返回 `{**spec, ...}`，会**原样保留**未知键。若把带 `method` 的 spec 提交为 MCP，脏字段会静默进入不可变版本，之后再也删不掉。
- 因此重置必须在切换时做，不能只在提交时挑选字段。

### D9：测试调用模板纯前端生成

`ToolTestPanel` 增加 `templateFromSchema(schema)`：遍历 `properties`，按 `type` 给占位值（`string→""`、`number|integer→0`、`boolean→false`、`array→[]`、`object→{}`），无 Schema 时用 `{}`。

- 不做 `$ref` 解析：入参 Schema 在保存时已通过 Draft 2020-12 校验，模板只是便利设施，解析失败时回落到 `{}` 即可。
- 切换版本时重算模板，避免用旧版本的参数去打新版本。

### D10：版本对比按工具类型选择字段集

`ToolDetailView` 的对比字段常量改为按 `tool.type` 计算：`HTTP` 保持现状；`MCP` 增加 `spec.transport`、`spec.tool_name`，保留 `spec.url`、`spec.headers`、`spec.auth`、`spec.timeout_s`。

## Risks / Trade-offs

- [探测可被用作内网地址探测（SSRF）] → 权限收敛到 `TOOL_MANAGE`、只回结论与截断摘要、超时受限。真正的出网收敛依赖后续的 tool-hub / egress，本期不做，需在评审时明确接受。
- [探测没有审计记录] → 本期只依赖应用日志。若后续要求"谁在什么时候探测了哪个地址"，需要单列审计需求与表结构，不能靠本期补丁。
- [探测与保存之间存在竞态：远端在探测后变更 schema] → 版本不可变只能保证"保存即固定"，无法保证"保存时仍与远端一致"。漂移检测属于后续独立能力。
- [后端仍允许 `sse`，控制台只提供 `streamable_http`] → 存量 `sse` 工具不受影响，但会出现"接口能做、页面不能做"的不对称。若确认要彻底移除 `sse`，应作为 **BREAKING** 变更单独提出，不在本期夹带。
- [`ToolSpecEditor` 继续变重] → 目前只有两种接入类型，内联可接受；出现第三种类型时应拆分为按类型分文件的编辑器组件，而不是继续加 `v-if`。
- [试调用需要先授权] → 新建的 MCP 工具在授权前无法试调用。这是既有治理约束，本期不改行为，只在面板上强化"去授权"引导，避免使用者误判为工具不可用。

## Migration Plan

无数据库迁移、无配置项变更、无数据回填。

上线顺序：先部署后端（新增端点与领域方法，纯增量，向后兼容），再发布前端。前端未更新时功能不受影响。

回滚：下线前端入口即可；后端探测端点无状态、无副作用，可保留不动。若必须回滚后端，因未改动既有端点契约与执行链路，回滚风险低。

## Open Questions

- 探测是否需要支持"带上 `tool_name` 直接试调一次"（当前只列清单，试调用统一走已保存版本的测试面板）。可后续追加，不影响本期 spec 与任务拆分。
