# Proposal

## Why

调试台刚上线的「会话列表 + 会话回放」在真实使用中等于不可用：首轮发送成功后对话区会被整体重建并清空，刷新页面也读不到任何历史轮次。

排查确认这是两个叠加的缺陷，而不是「没存下来」：

- 运行时只把 Prompt 模板渲染成消息序列，用户的对话输入仅作为 Prompt 变量参与替换。模板未引用该变量时（现有 Agent 的 Prompt 大多是单条 system 消息），输入被静默丢弃，会话上下文中**不存在任何用户消息**；模型也没看到这轮提问。
- 历史读取以「用户消息」为轮次边界（`_split_turns`），于是这类会话永远解析出 0 轮，接口返回空轮次。
- 前端把会话标识写回地址栏时，`router-view` 的 key 绑定的是 `route.fullPath`，query 一变就卸载重建整个视图，本地已渲染的轮次、草稿与面板状态全部丢失。

结果就是：数据在库里、列表也在，但回放永远空白，并且每次切换会话都伴随一次整页重建。

## What Changes

- **运行时把用户输入纳入会话上下文**：每轮执行时，若渲染后的消息序列中没有用户消息，则把本次输入以一条用户消息追加进去；模板已包含用户消息时不重复追加。这同时修掉「模型看不到本次输入」的问题。
- **历史读取的轮次边界降级重建**：会话上下文中定位不到用户消息时（存量会话），按该会话的运行记录重建轮次边界，用户输入取自运行记录，回答与工具条目取自会话上下文；不得因为缺少用户消息而返回空轮次。
- **空轮次不再静默**：会话声明有轮次但解析为空时，接口在响应中如实标记，界面给出可解释提示与重试入口，而不是空白对话区。
- **调试台视图稳定装载**：地址栏同步会话标识 MUST NOT 导致视图卸载重建；历史读取返回空而本地已有轮次时保留本地轮次；切换会话与新建会话都不再触发整页重建。
- **正文留存口径不变**：租户未开启正文留存时仍只返回结构与长度，不回退到暴露正文。

## Capabilities

### New Capabilities

无。本次只修正既有能力的实现口径，不引入新的能力边界。

### Modified Capabilities

- `execution/agent-invocation`：新增「用户输入进入会话上下文」要求——本轮输入必须以用户消息形式进入消息序列，不因模板未引用变量而丢弃。
- `execution/session-history`：新增「轮次边界缺失时的重建」要求——缺少用户消息的存量会话仍须可回放，且不得返回空轮次。该能力当前只在未归档的 `playground-session-history` 变更中以增量形式存在。
- `console/agent-debugging`：新增「回放视图的稳定装载」要求——会话标识同步不得重建视图，空历史不得覆盖本地已展示轮次。该能力同样只在未归档变更中，另有 `agent-console` 的增量与其并存。

## Impact

- 运行时：`backend/src/agent_platform/contexts/execution/runtime/nodes.py`（`render_node` 的消息组装）、`runtime/executor.py`（历史装载口径）、`catalog/domain/render.py`（变量解析是否保留未引用变量的输入）。
- 历史读取：`backend/src/agent_platform/contexts/execution/application/session_history_service.py`（轮次切分与运行记录对齐）、`api/routes.py`（响应是否携带「无法解析」标记）。
- 前端：`frontend/src/App.vue`（`router-view` 的 key）、`frontend/src/views/PlaygroundView.vue`（地址栏同步与会话选中）、`frontend/src/components/agent/AgentPreviewPanel.vue`（空历史的兜底）。
- 数据：不新增表、不改表结构；只改变 checkpoint 消息的组成方式，存量会话靠运行记录重建。
- 接口：`GET /api/v1/sessions/{id}/history` 的轮次数量与内容口径变化（可能新增可选字段），不破坏既有字段。
- 行为影响：`execution.run.input` 仍按原样记录变量字典；正文留存关闭时，重建出的轮次用户内容长度可能为 0（输入只在运行记录里），需在界面口径上说明。
