# Proposal

## Why

调试台的会话只活在前端内存里：`AgentPreviewPanel` 用单个 `session_id` 值加一个 `turns` 数组承载当前对话，点「新会话」直接覆盖这两个值，旧会话在界面上再无任何引用。后端其实留着会话行与 LangGraph checkpoint，但既没有「按会话读回消息」的接口，前端也没有接入已有的会话列表查询——结果是使用者只能看见「这一次」，看不见「上一次问了什么、模型答了什么、调了哪些工具」。

这直接违背了调试台的前提：调试是一个反复试错、事后复盘的过程。看不见历史，就只能靠记忆和重新提问，且一旦误点「新会话」，可复现的上下文就只剩数据库里的孤儿记录。

## What Changes

- **调试台会话列表**：在调试台左侧新增会话栏，按当前 Agent 列出该租户下的预览会话，按最近活动倒序、分页加载；每行展示可辨识信息（标题、轮数、环境、最近活动时间、是否过期），并高亮当前会话。
- **完整回放**：选中历史会话后，把该会话的每一轮重新渲染出来——用户提问、模型回答、该轮工具调用条目（名称、状态、耗时）——顺序与轮次边界与原对话一致。
- **续聊**：在选中的历史会话上继续发送，沿用该会话的会话标识与首轮固化配置；配置已变更时仍按既有规则提示新建会话。
- **新建不再销毁历史**：新建会话只清空当前视图并生成新标识，旧会话留在列表里可随时切回。
- **会话历史读取接口**：新增只读接口，一次返回会话元信息与结构化轮次（含逐轮运行标识、状态与工具条目），服务端从 checkpoint 与运行记录合成，不产生任何新运行、不移动发布指针。
- **会话可辨识元数据落库**：会话记录补充标题（首轮用户输入的截断）、轮数与创建者，使列表查询无需回读每条会话的历史。
- **归属与过期可见性**：新会话记录创建者，列表默认只返回本人创建的会话；正文留存未开启时历史只给摘要与长度；过期会话的历史可读但不可续聊，并在列表上明确标出。

非破坏性：`GET /sessions` 只增字段与可选过滤；`POST /preview` 入参不变；不新增外部依赖。

## Capabilities

### New Capabilities

- `execution/session-history`: 会话历史的可读性契约——按 Agent 归属列出可辨识的会话、按会话读取结构化轮次、历史读取的权限与正文留存约束、过期会话的只读语义，以及与运行时同源（checkpoint 线程）的读取边界。

### Modified Capabilities

- `console/agent-debugging`: 新增「调试会话列表与切换」「历史会话回放」「恢复会话后的续聊约束」三条要求，把原先仅在一个场景里顺带提及的「旧会话历史仍可查看」提升为可验收的能力。
- `execution/agent-session`: 新增「会话可辨识元数据与归属」「过期会话的历史保留」两条要求——会话行需带标题、轮数与创建者，且过期不再意味着历史立即消失。

> 说明：`console/agent-debugging` 与 `execution/agent-session` 由并行变更 `agent-console` 引入，尚未归档到主干 specs。本变更以增量方式在其上追加要求，归档顺序应与 `agent-console` 保持一致；若本变更先归档，需在 `agent-console` 归档后复核合并结果。

## Impact

- 后端：`contexts/execution`（`api/routes.py` 新增会话历史路由并扩展会话列表过滤、`api/schemas.py` 新增轮次结构、新增历史读取服务、`infrastructure/repositories.py` 扩展列表条件、`infrastructure/tables.py` 会话表补列、新迁移）、`contexts/insight`（批量按 run 取轨迹条目用于工具状态与耗时）、`contexts/platform`（复用 `content_retention_enabled`，无新增权限点）。
- 数据库：`execution.execution_session` 增补 `title`、`turn_count`、`created_by` 与相应索引；存量行可空，不回填。
- 前端：`PlaygroundView` 改为「列表 + 对话」双栏并承接会话切换；新增会话列表组件与历史轮次映射；`AgentPreviewPanel` 从「自持单一会话」改为「受控会话 + 可注入历史轮次」，`AgentDetailView` 中的用法保持不变。
- 契约：新增 `GET /api/v1/sessions/{session_id}/history`；`GET /api/v1/sessions` 增加 `env`、`expired` 过滤与 `title`、`turn_count`、`owned`、`expired` 字段，旧客户端行为不变。
- 风险：历史内容的正文留存与归属过滤涉及隐私边界；过期会话在被同一标识再次调用时仍会回收旧行，需在文档与界面同步说明。
