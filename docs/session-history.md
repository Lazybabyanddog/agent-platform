# 会话历史读取：checkpoint 依赖与回归范围

调试台的会话回放不维护第二套消息表：轮次主要来自该会话线程上的 LangGraph checkpoint，
线程缺少用户消息锚点时再以 `execution.run` 补齐缺口（见「轮次来源」）。
本文记录 adapter 依赖的消息结构、已知边界，以及升级 LangGraph 时必须回归的范围。

## 链路

```
execution_session(id)
  ├─ thread_id = sess-{id}
  │    └─ PostgresCheckpointStore.aget_tuple(...)   # 只读最新 checkpoint
  │         └─ checkpoint["channel_values"]["messages"]
  │              └─ session_history_service._split_turns    # 按 user 消息切段
  └─ execution.run 列表（同会话按 started_at 升序）
       └─ session_history_service._turns_from_runs         # 线程锚点不足时补齐
            └─ _assemble（对齐 run、工具轨迹与正文）
                 └─ GET /api/v1/sessions/{id}/history
```

- 读取入口：`backend/src/agent_platform/contexts/execution/application/session_history_service.py`
- checkpoint 适配：`backend/src/agent_platform/contexts/execution/infrastructure/checkpoint_store.py`
- 路由与响应模型：`execution/api/routes.py` 的 `get_session_history`、`execution/api/schemas.py` 的 `SessionHistory*`

读取路径不写 checkpoint、不写 `execution.run`、不动发布指针；回放不会因为“打开历史”而多出一条运行。

## 依赖的消息结构

adapter 通过 `model_dump()` / `dict()` 后的普通字典消费消息，依赖以下字段：

| 字段 | 来源 | 用途 |
| --- | --- | --- |
| `role` 或 `type` | 消息类型（`system` / `user` / `assistant` / `tool`） | 切段与角色判定 |
| `content` | 字符串或内容块数组 | 展示正文；数组时拼接 `{"text": ...}` |
| `tool_calls[]` | assistant 消息 | 工具名（`name`）与调用顺序 |
| `tool_call_id` | tool 消息 | 与 `tool_calls` 配对（当前实现按顺序 + 名称兜底） |
| `name` | tool 消息 | 工具条目的名称来源 |

归一化规则：

1. 丢弃 `system`：每轮都会重渲染，不属于可回放内容。
2. 按 `user` 消息切段；段内最后一条“无 `tool_calls` 的 assistant”作为该轮回答。
3. 段内 `tool_calls` 收集为工具条目，再用同 `run_id` 的 `tool_call` 轨迹记录补 `status`、`latency_ms`、`error`；轨迹缺失时状态为 `UNKNOWN`。
4. 轨迹里存在但 checkpoint 未留下调用的工具也会补成条目，避免失败中断被吞掉。
5. 轮次与 run 按开始时间顺序对齐；数量不一致时按尾部收敛，多出的 run 单独成轮（无用户消息），多出的段不带 run 元数据。
6. `turns_limit` 从尾部截断，`index` 仍按全局轮次编号，并置 `truncated=true`。

## 轮次来源：线程锚点与运行记录重建

运行时会把本次输入物化进上下文（`runtime/input_text.py` 生成文本，`render_node` 在模板没有 user 消息时追加），
因此新会话的线程里 `user` 消息是轮次锚点，正常回放不需要运行记录。

历史存量会话（本次改动前产生的）线程里只有 `system` + `assistant`，按 `user` 切段会得到 0 轮。
`SessionHistoryService.get` 因此做一次缺口补齐：

1. 线程切出的轮次数等于 run 数：直接使用，`reconstructed=false`。
2. 少于 run 数：前 `run 数 - 线程轮次` 轮用 `_turns_from_runs` 重建，其余保留线程原文，`reconstructed=true`。
   - 整个线程都没有锚点时，assistant 消息按顺序配对给成功（`SUCCESS`）的运行；失败运行不消费回答。
   - 混合状态（存量会话续聊后）只补齐早期轮次，最近一轮仍用线程里渲染后的用户原文（含模板前缀）。
3. 用户文本取自 `run.input`：单值为原文，多值按 `key: value` 逐行，非字符串走 JSON，与运行时写入保持一致。

重建只影响展示：不写 checkpoint、不写 `execution.run`，正文仍按会话归属返回。
回答可能缺失（线程里没有对应 assistant 消息），此时 `assistant=null`，前端会提示"轮次由运行记录重建"。
`reconstructed=true` 也出现在响应里，供调用方区分"服务端重建"与"线程原文"。

## 正文可见性与权限

- 接口要求 `AGENT_READ`；他人会话与不存在会话统一 404，避免探测存在性。
- 正文由会话归属决定：创建者回放自己的会话即可拿到原文，不再要求 `insight:read_content`，也没有租户级开关。
- 会话标题在首轮无条件落库，用于列表辨识。

## 升级 LangGraph 时的检查项

升级 `langgraph` / `langgraph-checkpoint-postgres`（或更换 saver）后按顺序检查：

1. `checkpoint_store.saver()` 仍返回可 `aget_tuple` 的 saver，且 `thread_id` 仍是 `sess-{session_id}`。
2. 最新 checkpoint 仍包含 `channel_values.messages`，且消息反序列化后仍带 `role`/`type`、`content`、`tool_calls`、`tool_call_id`。
3. 消息内容仍是“字符串或内容块数组”两种形态；新增块类型不会被 `_text` 静默丢成空串。
4. `tool_calls` 仍是 `{name, args, id}` 字典列表；若改名（如 `function.name`），需同步 `_split_turns` 与 `_tool_entries`。
5. 历史读取仍不产生副作用：调用前后 `execution.run` 行数不变。

## 回归范围

| 层级 | 文件 | 覆盖点 |
| --- | --- | --- |
| adapter 契约 | `backend/tests/unit/test_session_history_adapter.py` | 固定消息序列：单轮含工具、工具失败、多轮、无回答、run/段错位、正文与长度、无 user 锚点的运行记录重建 |
| 运行时输入 | `backend/tests/unit/test_runtime_input_message.py` | system-only 模板追加 user、模板已有 user 不重复、多变量文本、两轮渲染 system 只出现一次、无会话标识不继承上下文 |
| 接口集成 | `backend/tests/integration/test_session_history.py` | 列表字段与过滤、回放不产生 run、工具条目、创建者隔离与成员回放、过期只读、`turns_limit` 截断、权限 404/403、重建标记与续聊后补齐 |
| 端到端 | `backend/tests/e2e/test_playground_session_history_flow.py` | 四会话列表 → 回放 → 续聊一轮沿用首轮绑定；system-only Prompt 的输入进入上下文并可回放 |
| 表结构 | `backend/tests/integration/test_agent_console_schema.py` | 会话元数据列、索引与默认值 |

改动 adapter 后至少运行：

```bash
cd backend
python -m pytest tests/unit/test_session_history_adapter.py tests/integration/test_session_history.py -q
```

涉及会话建立或续聊逻辑时再加跑：

```bash
python -m pytest tests/integration/test_agent_preview.py tests/integration/test_agent_session.py -q
python -m pytest tests/e2e/test_playground_session_history_flow.py -q
```
