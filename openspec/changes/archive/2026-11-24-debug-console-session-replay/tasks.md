# Tasks

## 1. 运行时把输入物化为用户消息

- [x] 1.1 在 `backend/src/agent_platform/contexts/execution/runtime/` 内新增输入可读化函数（单值取原文、多值按 `name: value` 逐行、非字符串走 JSON），并在 `nodes.py` 的 `render_node` 中于"渲染结果无 user 消息"时追加一条用户消息；验证：为 `render_node` 写单测，覆盖「模板只有 system」「模板已含 user 消息」「多变量输入」三种形态，断言消息条数与内容
- [x] 1.2 确认系统提示词每轮只出现一次且历史中的 system 仍被丢弃；验证：新增单测断言两轮渲染后 system 计数为 1、历史 user/assistant 各一条
- [x] 1.3 用 system-only Prompt 跑一次端到端预览，确认模型收到的消息里包含本次输入；验证：`cd backend && pytest tests/e2e -k preview` 通过，且断言 `model.requests[-1].messages` 含用户输入文本
- [x] 1.4 确认未提供会话标识的调用不落任何会话上下文；验证：单测断言该路径下 `messages` 仅存在于本次执行结果，checkpointer 未被写入

## 2. 存量会话的轮次重建

- [x] 2.1 在 `session_history_service.py` 新增 `_turns_from_runs(runs, messages)`：每 run 一轮，user 取自 `run.input` 的可读文本（复用 1.1 的函数），assistant 按顺序消费无 tool_calls 的 assistant 消息且仅对 SUCCESS 的 run 消费，缺失留空；验证：`tests/unit/test_session_history_adapter.py` 新增「system+assistant 形态」用例，断言轮次数等于 run 数、user 文本来自 input、失败轮带 FAILED 状态
- [x] 2.2 在 `SessionHistoryService.get` 中接入降级路径（`_split_turns` 为空且有运行记录时使用），并保持既有截断/对齐、权限与正文留存逻辑不变；验证：`tests/integration/test_session_history.py` 新增用例，对无 user 消息的会话断言 `turns` 非空且与 `turn_count` 一致
- [x] 2.3 响应增加可选字段标记轮次来自重建（`SessionHistory.reconstructed`），并在 `api/routes.py` 序列化；验证：集成测试断言该字段在有 user 锚点时为 false、重建时为 true
- [x] 2.4 校验正文留存关闭时重建结果只含长度与状态；验证：集成测试在未开启留存的租户下断言 `content` 为 null 且 `length` 正确

## 3. 调试台不再整页重建

- [x] 3.1 将 `frontend/src/App.vue` 的 `router-view` key 由 `route.fullPath` 改为 `route.path`；验证：`cd frontend && npm run build` 通过，且手动在调试台完成首轮对话后对话区不清空、网络请求不再重复拉取整页数据
- [x] 3.2 为 `frontend/src/views/PromptDetailView.vue` 补 `route.query.version` 的 watch，使浏览器前进/后退切版本仍生效；验证：手动在两个版本间前进后退，选中版本跟随地址栏变化
- [x] 3.3 调整 `AgentPreviewPanel.vue` 的历史 watcher：仅 `props.sessionKey` 真值变化视为切换会话，空历史且本地有轮次时保留本地轮次并给出提示；验证：新增/更新组件测试或在浏览器中确认「首轮结束」与「切换会话」两种场景分别表现为保留与替换
- [x] 3.4 在 `PlaygroundView.vue` 区分"历史为空"与"历史读取失败"：失败不清空已有轮次，空而会话声明有轮次时展示提示与重试；验证：断网或人为让接口返回空，确认对话区保留内容并出现提示

## 4. 集成验证与文档

- [x] 4.1 补一条端到端用例：新建会话（system-only Prompt）→ 首轮完成 → 刷新页面 → 历史可回放 → 续聊沿用同一会话标识；验证：`cd backend && pytest tests/e2e -k session` 通过
- [x] 4.2 用本地库中的存量会话（system+assistant 形态）验证回放与续聊；验证：接口返回轮次数与 `execution_session.turn_count` 一致，且续聊不新增运行以外的副作用
- [x] 4.3 更新 `README` 或接口文档中关于会话历史与轮次来源的说明（何时走运行记录重建、正文留存口径）；验证：文档示例与当前实现一致，评审通过

## Workflow follow-up

- 评审通过后归档本变更，并把三份增量规格同步到 `openspec/specs/`（注意与未归档的 `playground-session-history`、`agent-console` 增量合并）。
- 归档时确认 `execution/session-history` 与 `console/agent-debugging` 的主规格 Purpose 来自既有变更，不被本次覆盖。
