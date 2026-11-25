# Proposal

## Why

`add-model-tool-calling-foundation` 把「模型可见契约」「工具返回语义」「运行时上下文」做进了执行链路与接口，但控制台没有跟上：工具版本只能编辑调用契约，描述来源不可见，保存告警被丢弃；Agent 版本无法声明运行时上下文，且页面保存的新版本会把它清零；调试台预览不携带声明，导致页面无法验证该能力。结果是这些能力只能靠手工调用接口维护，误操作还会静默削弱线上配置。

## What Changes

- 工具契约编辑器增加「模型可见契约」编辑：工具用途描述、每个入参的说明与格式；结构不得与调用契约分叉。
- 工具版本增加「返回语义」声明：以状态值列表声明业务终态。
- 保存工具版本后展示服务端返回的字段级告警（不阻塞保存）。
- 工具详情展示用途描述的来源（工具级 / 版本内覆盖 / 缺失），并提供就地补充入口。
- Agent 配置页增加运行时上下文声明（当前时间、时区），以版本为基准编辑时回显，保存时原样提交。
- Agent 版本对比增加运行时上下文一节。
- 调试台与配置页预览请求携带运行时上下文声明；调试面板标出本次执行注入了哪些上下文项。
- **BREAKING**（接口层，向后兼容默认值）：预览请求新增可选字段 `runtime_context`，未提交时行为与今天一致（不注入）。

## Capabilities

### New Capabilities

- `console/tool-management`: 工具契约的控制台维护面——调用契约、模型可见契约、返回语义、描述来源与保存告警的编辑与呈现。该路径与 `mcp-tool-console` 变更已规划的能力一致，需求集不重叠，谁先归档谁建立基础规格。
- `console/agent-configuration`: Agent 配置页对版本内容的编辑面，新增运行时上下文声明的编辑、回显与版本对比呈现。该路径与 `agent-console` 变更已规划的能力一致，同上。

### Modified Capabilities

- `console/agent-debugging`: 调试台预览按最新版本快照携带运行时上下文声明，并在面板标出注入项。

## Impact

- 前端：`ToolSpecEditor`、`ToolDetailView`、`useAgentConfigEditor`、`AgentDetailView`、`AgentVersionsView`、`PlaygroundView`、`AgentPreviewPanel`、`src/api/schema.d.ts`（重新生成）。
- 后端：`execution/api/schemas.py` 的预览请求新增 `runtime_context`，预览路由与预览服务把声明透传给装配，装配规则与保存版本共用。
- 数据：无迁移。已保存的版本快照结构不变，`runtime_context` 缺省为空列表。
- 测试：工具契约编辑与告警展示的前端类型检查与手工验收；预览携带声明的后端单元/集成测试。
- 文档：`docs/runtime-context.md` 与 `docs/tool-contract.md` 补充控制台侧操作说明。
- 归档顺序：`mcp-tool-console`、`agent-console` 任一先归档都不会与本次增量产生同名需求；若本变更先归档，上述两个能力的基础规格由本次增量建立。
