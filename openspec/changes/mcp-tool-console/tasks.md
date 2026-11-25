# Tasks

## 1. 后端：共享端点校验与鉴权解析

- [x] 1.1 在 `backend/src/agent_platform/contexts/catalog/domain/tool.py` 把端点相关校验（`transport`/`url`/`timeout_s`/`headers`/`auth`）抽为 `_validate_mcp_endpoint()`，`_validate_mcp()` 改为先调用它再校验 `tool_name`；验证：`pytest tests/unit/test_tool_spec.py -q` 通过，且既有 MCP 校验用例一条都不需要改动
- [x] 1.2 在 `backend/src/agent_platform/contexts/catalog/application/tool_service.py` 把 `_build_headers()` 提为模块级 `build_auth_headers(tenant_id, spec)`，`_execute_http()` 与 `_execute_mcp()` 改为调用该函数；验证：`pytest tests/unit/test_secret_no_leak.py tests/integration/test_tool_executor.py tests/integration/test_tool_mcp_executor.py -q` 通过

## 2. 后端：MCP 工具探测

- [x] 2.1 在 `backend/src/agent_platform/contexts/catalog/infrastructure/mcp_client.py` 为 `McpSession` 协议新增 `list_tools()`，`SdkMcpSession` 复用 `_open_transport()` 与同一超时预算实现，并把远端工具条目归一为 `{name, description, input_schema}`；验证：新增单测用替身断言传入的 `url/transport/headers/timeout_s` 与超时传播，`pytest tests/integration/test_mcp_live_protocol.py -q` 仍通过
- [x] 2.2 在 `backend/src/agent_platform/contexts/catalog/domain/enums.py` 新增 `McpProbeVerdict`（`OK` / `MCP_PROBE_AUTH_FAILED` / `MCP_PROBE_TIMEOUT` / `MCP_PROBE_UNREACHABLE` / `MCP_PROBE_PROTOCOL_ERROR`），并新增探测用例：解析鉴权后调用 `list_tools()`，把异常映射为结论，全程不调用任何 repository；验证：单测覆盖五种结论映射，并断言远端返回空清单时结论为 `OK` 且清单为空
- [x] 2.3 在 `backend/src/agent_platform/contexts/catalog/api/schemas.py` 新增 `McpProbeRequest` / `McpProbeResponse`，在 `backend/src/agent_platform/contexts/catalog/api/routes.py` 新增 `POST /tools/probe-mcp` 并要求 `Permission.TOOL_MANAGE`，响应不回传远端原始响应体（只给结论、截断摘要与耗时）；验证：新增集成测试基于 `tests/integration/mcp_server.py` 起的真实 streamable_http server，断言返回包含 `search_documents` 及其用途描述与入参 Schema，且调用前后 `tool` / `tool_version` 行数不变

## 3. 前端：接入类型与 MCP 配置表单

- [x] 3.1 在 `frontend/src/api/endpoints.ts` 增加探测请求封装与响应类型导出，运行 `npm run gen:api` 重新生成 `schema.d.ts`；验证：`npm run typecheck` 通过，且 `schema.d.ts` 中出现 `McpProbeRequest` / `McpProbeResponse` 定义
- [x] 3.2 在 `frontend/src/views/ToolListView.vue` 的新建弹窗增加接入类型选择（`HTTP` / `MCP`），切换类型时把 spec 重置为该类型的默认骨架，提交时使用所选类型而不是固定值；验证：`npm run typecheck` 通过，并在浏览器网络面板确认提交 MCP 工具时请求体 `type` 为 `MCP`、`spec` 不含 `method`
- [x] 3.3 在 `frontend/src/components/tool/ToolSpecEditor.vue` 把非 HTTP 分支改为 MCP 结构化表单（传输方式仅 `streamable_http` 并标注不支持 `stdio`、服务端地址、远端工具名、超时、鉴权与凭据、请求头、入参/出参 Schema），保留可折叠的完整 JSON 区与未知字段原样保留提示，并按后端规则做字段级校验；验证：`npm run typecheck` 通过，且不填远端工具名时在字段上提示、不发请求

## 4. 前端：探测交互

- [x] 4.1 在 `frontend/src/components/tool/ToolSpecEditor.vue` 增加探测按钮：未填服务端地址时不发请求并提示；成功后展示工具清单，选中条目填充远端工具名、用途描述与入参 Schema，且不覆盖使用者已填写的其它字段；验证：`npm run typecheck` 通过，并对本地 `streamable_http` MCP server 探测后确认三处字段被正确填充、其余已填字段未变
- [x] 4.2 呈现探测失败态：认证失败、超时、不可达、无可用工具四种结论分别可辨识，失败时表单已有内容保持不变；验证：分别用错误凭据、不可达地址与无工具 server 手工走查四种结论
- [x] 4.3 确保探测不产生副作用：探测后放弃保存，工具版本数与调用记录均无新增；验证：探测前后对比工具详情页的版本列表与「调用记录」标签页

## 5. 前端：版本对比与测试调用模板

- [x] 5.1 在 `frontend/src/views/ToolDetailView.vue` 把版本对比的字段清单改为按 `tool.type` 计算，MCP 增加 `spec.transport` 与 `spec.tool_name`，并保留 `spec.url`/`spec.headers`/`spec.auth`/`spec.timeout_s`；验证：`npm run typecheck` 通过，且对同一 MCP 工具的两个远端工具名不同的版本对比时能看到该字段的前后取值
- [x] 5.2 在 `frontend/src/components/tool/ToolTestPanel.vue` 按所选版本的 `input_schema` 生成入参模板（`string→""`、`number|integer→0`、`boolean→false`、`array→[]`、`object→{}`，无 Schema 时为 `{}`），切换版本时重新生成；验证：`npm run typecheck` 通过，且选择声明了入参 Schema 的 MCP 版本后输入框呈现按 Schema 派生的占位参数

## 6. 端到端验收与回归

- [x] 6.1 复用 `backend/tests/integration/agent_helpers.py` 与 `backend/tests/integration/mcp_server.py` 写一个端到端用例：注册 MCP 工具 → 保存版本 → 授权租户 → 绑定 Agent 版本 → 发布 → invoke，断言调用轨迹中出现该远端工具名与其耗时；验证：`pytest tests/integration -q -k mcp_console` 通过
- [x] 6.2 全量回归；验证：`pytest -q`（在 `backend/` 下）与 `npm run build`（在 `frontend/` 下）均通过

## Workflow follow-up

- 评审通过后归档变更，并确认 `console/tool-management` 与 `catalog/tool-registry` 的增量已合入主规格。
