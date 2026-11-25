# Proposal

## Why

平台后端已经具备 MCP 工具的完整能力：`ToolType.MCP` 是 `catalog.tool` 的一等类型，`_validate_mcp()` 校验 `transport / url / tool_name / timeout_s / auth / schema`，`SdkMcpSession` 支持 `streamable_http` 与 `sse` 传输，`POST /tool-versions/{id}/execute` 也已接上带风险确认的测试调用面板。

但控制台无法创建 MCP 工具：新建工具的接入类型被写死为 `HTTP`（`ToolListView.vue` 提交时 `type: 'HTTP'`），契约编辑器对非 HTTP 类型退化成一段裸 JSON 文本框。要接一个 MCP 服务，使用者只能手写整段 spec，并人工抄录远端的 `input_schema`——而 `tool_name` 是必填字段，漏填会在提交时被整体拒绝，表单里却没有对应的字段级提示。

结果是「接一个 MCP 服务，用来验证 agent 流程」这件事卡在第一步。本期把这条链路打通，让 MCP 服务可以像 HTTP 工具一样在页面上配出来、自测通过、再交给 agent 使用。

## What Changes

- 新建工具时选择接入类型（`HTTP` / `MCP`），该选择贯穿契约编辑器与首个版本提交，不再默认写死 `HTTP`
- MCP 工具改为结构化配置：传输方式、服务端地址、远端工具名、超时、鉴权、请求头、入参/出参 Schema，均以表单字段呈现并前置校验
- 新增 MCP 工具探测：连接远端 `tools/list`，返回可用工具清单；选中一项后自动填充远端工具名、用途描述与入参 Schema，替代人工抄录
- 契约变更对比覆盖 MCP 字段（传输方式、服务端地址、远端工具名），修掉 MCP 工具改版时对比面板为空的问题
- 测试调用的入参模板按所选版本的入参 Schema 生成，MCP 工具不再需要手工拼参数
- MCP 传输方式在控制台固定为 `streamable_http`，界面明确标注不支持 `stdio`（平台不托管远端进程）
- 探测为只读辅助能力：不创建工具、不产生版本、不写入调用记录

## Capabilities

### New Capabilities

- `console/tool-management`: 控制台的工具运维入口——新建工具的接入类型选择、MCP 结构化配置、MCP 工具探测与契约填充、契约变更对比、测试调用入参模板，以及按权限对写操作降级

### Modified Capabilities

- `catalog/tool-registry`: 新增「MCP 工具探测」需求——在注册之前连接远端 `tools/list` 获取工具契约，探测结果不回写注册表，且不构成工具的注册或版本

## Impact

- 后端：`catalog/api/routes.py`、`catalog/api/schemas.py`、`catalog/domain/enums.py`、`catalog/infrastructure/mcp_client.py`、`catalog/application/` 下新增探测用例
- 前端：`views/ToolListView.vue`、`views/ToolDetailView.vue`、`components/tool/ToolSpecEditor.vue`、`components/tool/ToolTestPanel.vue`、`api/endpoints.ts`
- 数据库：无迁移。探测不落库，不新增表
- 执行链路：不变。agent 运行时、`execution` 上下文与工具授权模型均不受影响
- 兼容性：`catalog/tool-registry` 现有的 `sse` 传输契约保持不变（存量 MCP 工具不受影响），本次只在控制台收敛为 `streamable_http`
