# Design

## Context

动机见 `proposal.md`。以下是决定方案的现状约束（均为实测/读码确认）：

- 运行时只把 Prompt 模板渲染成消息序列：`render_node` 取 `render_messages(prompt["messages"], prompt["variables"], state["input"])`，模板里没有占位符的变量不会被使用（`runtime/nodes.py:133-142`、`catalog/domain/render.py`）。
- 现有 Agent 的 Prompt 多为单条 system 消息，用户输入被控制台绑定到必填变量（`AgentPreviewPanel` 的 `primaryVariable`），于是 checkpoint 的 `messages` 通道只有 `[system, assistant, ...]`。直接查本地库确认：`agent.checkpoint_blobs` 中该会话 `messages` 解码后为 `system` + `assistant` 两条，没有任何 `role: "user"`。
- `_split_turns` 以 user 消息为轮次边界，遇到 `current is None` 直接丢弃后续 assistant（`session_history_service.py:93-121`），因此这类会话解析出 0 轮。
- `SessionHistoryService.get` 已经按会话取到运行记录（`list_for_session`，`session_history_service.py:249-253`），而 `execution.run.input` 里存着当轮用户原始输入（实测：`{"day": "帮我查下武汉明天天气"}`）。`Run` 表没有 output 列。
- 前端 `router-view` 的 key 绑定 `route.fullPath`（`App.vue:90`），而 `PlaygroundView` 在首轮结束后调用 `router.replace({ query: { sessionId } })`（`PlaygroundView.vue:93-114`、`306-310`），query 变化即卸载重建整个视图，本地 `turns` 与面板 `draft` 全部归零。
- 面板已有"同会话旧历史为空时保留本地气泡"的保护（`AgentPreviewPanel.vue:211-225`），但它只在组件存活期间有效，重建后 `sessionId` 计算值变化会被判定为"切换会话"并清空。
- 租户正文留存当前关闭（`platform.tenant.content_retention_enabled = false`），历史接口只返回长度与状态，这是既有规格要求，本次不改。

## Goals / Non-Goals

**Goals:**

- 让新产生的会话在上下文中具备可定位的轮次边界，回放不再依赖 Prompt 模板是否恰好写了 user 消息。
- 让已有存量会话（存量为 system + assistant 形态）也能回放出轮次，而不是空白。
- 消除调试台的整页重建，使"首轮结束 / 切换会话 / 新建会话"三种场景下已展示内容不被清空。
- 历史确实读不出来时给出可解释的提示，而不是空白。

**Non-Goals:**

- 不改正文留存策略，不在控制台为调试台开一条绕过租户开关的正文读取路径。
- 不新增会话消息表，不做双写。
- 不改变 Prompt 模板的渲染语义（占位符替换规则、变量校验规则不变）。
- 不重做调试台交互（分页、列表视觉、工具条目展示形态保持现状）。

## Decisions

### D1 运行时把本次输入物化为一条用户消息

在 `render_node` 组装完 `messages` 后追加一步：若序列中不存在 `role == "user"` 的消息，则在末尾追加一条用户消息，内容取自本次输入的可读文本。

- 可读文本规则（运行时内小函数，例如 `_input_text(input, variables)`）：输入只有一个值且为字符串时取其原文；多个值时按 `name: value` 逐行拼接；非字符串值用 `json.dumps(..., ensure_ascii=False)`。Prompt 声明的变量顺序用于稳定排序。
- 幂等：只在渲染结果没有用户消息时追加。模板已经写了 `{{question}}` 的 Prompt 不受影响，不会出现重复提问。
- 与历史去重：`render_node` 已经丢弃 history 中的 system 消息，追加的 user 消息在下一轮以 history 形式回放一次，不会自我叠加。
- 为什么不改 `render_messages`：渲染是纯函数，只负责模板替换；"输入必须有去处"是运行时上下文职责。把兜底塞进 catalog 层会让 Prompt 预览/测试也产出非模板内容，误导用户以为模板里写了用户消息。否决。
- 为什么不改成"由调用方自己拼 user 消息"：控制台只是调用方之一，正式调用同样存在"输入未进上下文"的问题；放在运行时才能一次修好。

### D2 存量会话以运行记录为锚重建轮次

`_split_turns` 保持不变，新增 `_turns_from_runs(runs, messages)` 作为降级路径：

- 每个已受理的运行记录对应一轮，`index` 连续。
- `user` 取自 `run.input` 的可读文本（与 D1 共用同一个 `_input_text`，避免两处口径漂移）。
- `assistant` 按顺序消费上下文中"无 tool_calls 的 assistant 消息"，只对 SUCCESS 的 run 消费；无对应回答时留空，不猜测。
- 工具条目继续走既有 `_tool_entries`（按 `run.id` 取 `ToolCall` 记录），状态与耗时口径不变。
- 触发条件：存在运行记录，且 `_split_turns(messages)` 的轮次数少于运行记录数。缺口在前（早期轮次没有锚点），按 `runs[:缺口]` 重建，其余使用线程原文。
- 混合场景（存量会话续聊后只剩最近一轮锚点）：只补齐早期轮次，最近一轮保留渲染后的用户原文（含模板前缀），避免整段历史被运行输入覆盖。
- 整段线程都没有锚点时，`assistant` 按顺序配对给成功运行，能还原部分回答；有部分锚点时缺口轮次的回答留空，不跨越线程边界错配。
- 代价：`.run.input` 是变量字典，单变量 Prompt 能还原成人话，多变量 Prompt 可能还原成键值列表。可接受——比空白好，且 D1 生效后新会话不再走这条路。
- 备选一：从 `execution.run` 重建全部内容。`Run` 没有 output 列，回答无法还原，否决。
- 备选二：新增 `execution_session_message` 表并双写。与 `playground-session-history` 的 D1 结论冲突（两份真相源），且需要迁移与回填，否决。

### D3 历史响应显式标记"重建"与"不完整"

- `SessionHistory` 增加 `reconstructed: bool`，接口响应增加可选字段 `reconstructed`，前端据此在对话区顶部显示"历史由运行记录重建，回答可能不完整"。
- 当 `turns` 为空而 `session.turn_count > 0` 时，前端展示提示与重试入口，而不是空白。后端不为此新增错误码——空轮次对"从未产生消息"的会话是合法结果，用响应字段表达比用 4xx 更准确。

### D4 前端不再让 query 决定视图生命周期

`App.vue` 的 `:key` 从 `route.fullPath` 改为 `route.path`。

- 理由：query 在本项目里是页面内部状态（`PlaygroundView` 的 `agentId`/`env`/`sessionId`、`PromptDetailView` 的 `version`），不应触发组件销毁。path（含 `/agents/:id` 这类参数）变化仍然重建，符合"换实体换视图"的预期。
- 备选一：直接移除 key。同 path 下不同参数会复用组件，`/agents/:id` 靠 path 已能区分，但保留 `:key="route.path"` 更明确，否决移除。
- 备选二：`PlaygroundView` 改用 `window.history.replaceState` 写地址栏，绕开 router。会导致 `route.query` 与实际地址不一致，刷新恢复逻辑要重写，否决。
- 兼容性：全仓 `router.replace({ query })` 只有 `PlaygroundView` 与 `PromptDetailView` 两处，且两者都在组件内维护自身状态（前者首次挂载读取 query，后者有 `applyRequestedVersion`）。改 key 后 `PromptDetailView` 需要补一个 `watch(() => route.query.version)`，否则浏览器前进/后退切换版本不再生效——列入任务。

### D5 面板的空历史兜底改为显式会话比较

`AgentPreviewPanel` 的 watcher 目前用计算出的 `sessionId`（`props.sessionKey ?? draft`）判断是否"切换会话"，重建后 draft 变新值即被判为切换并清空。改为：

- 只把 `props.sessionKey` 的真值变化当作"切换"；`null → 新值` 不视为切换（首轮采纳会话正是这种情况）。
- 历史为空数组且本地已有轮次时保留本地轮次，并置 `historyNotice`（"历史暂不可读"）。
- 父级 `PlaygroundView` 在历史读取失败时不清空 `historyTurns`，只设置 `historyError`，避免"失败即空"。

## Risks / Trade-offs

| 风险 | 缓解 |
| --- | --- |
| 追加 user 消息会改变既有 Prompt 的模型输入（多一条消息），可能影响已发布 Agent 的回答风格 | 仅对"模板无 user 消息"的 Prompt 生效；system 仍只出现一次；回归 prompt/agent 相关 e2e 与 `StaticModel` 用例断言消息条数 |
| 重建轮次的 `user` 文本来自 `run.input`，多变量 Prompt 会呈现为键值列表 | 属于可接受的降级；D1 生效后新会话不再触发；前端在 `reconstructed` 时给出说明 |
| `route.path` 作 key 后，同 path 不同 query 的浏览器前进/后退不再重建视图 | `PromptDetailView` 补 query watch；`PlaygroundView` 已有"首次挂载按 URL 恢复"逻辑，前进/后退场景列入回归清单 |
| checkpoint 只保留最近消息（`trim_history`），重建的回答数可能少于轮次数 | 如实留空并保留既有 `truncated` 语义；不伪造回答 |
| 历史读取把 `run.input` 当作正文来源，可能把正文写进日志 | 重建逻辑只取值不打印；沿用现有的正文留存开关决定是否返回 |
| LangGraph 消息形态升级会同时影响运行时与读取端 | 继续用 `tests/unit/test_session_history_adapter.py` 作为契约测试，并新增"无 user 锚点"用例 |

## Migration Plan

1. 无数据库迁移；先发后端：新会话开始带 user 消息，存量会话在读侧降级重建，二者可共存。
2. 再发前端：切换为 `route.path` key、面板兜底与提示文案。
3. 回滚：后端回滚后，含 user 消息的新会话仍能被旧的 `_split_turns` 正确解析；前端回滚只回到"整页重建"的体验，不损坏数据。
4. 验证：本地库中既有会话（`sess-01a20cdf-...` 这类 system+assistant 形态）应能读出与 `turn_count` 一致的轮次；新开会话在库中 `messages` 应含 user 消息。

## Open Questions

- 重建轮次时 `index` 是否应与运行记录的全局顺序严格一致（含被裁剪的更早轮次）？当前按"可见轮次从 1 开始编号并复用 `truncated`"处理，若后续需要跨页游标再调整。
