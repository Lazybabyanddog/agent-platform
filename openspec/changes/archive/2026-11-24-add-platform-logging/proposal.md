# Proposal

## Why

后端目前只有 stdout 一条输出通道，没有任何日志文件；且真正写日志的模块只有异常处理器一个，模型调用、MCP 调用、执行节点这些最需要现场证据的位置一条日志都不打。结果是：请求一旦异常，终端里没有可读的证据链，只能等调用结束后去数据库翻 `llm_call` / `tool_call`。项目既要本地直跑（终端一关就全丢），也要跑在容器里（需要结构化 stdout 交给采集），两种形态都缺一套可依赖的落盘与字段约定。

## What Changes

- 统一日志出口：后端所有模块改用 `shared.logging.get_logger`，并把标准库 `logging`（含 uvicorn 自身、第三方库）桥接进同一条 structlog 管道，消除「JSON 主链路 + 裸文本旁路」并存。
- 建立字段契约：每条日志固定带 `timestamp` / `level` / `event` / `service` / `environment` / `trace_id` / `request_id` / `tenant_id` / `run_id`（无值时为 `null`），`event` 采用点分命名，不再用自由文本 message 承载语义。
- 上下文贯穿：请求中间件绑定 `trace_id` / `request_id`，认证成功后补充 `tenant_id`，执行入口补充 `run_id`，outbox worker 启动时绑定自身 `service`。
- 关键位置补事件：一次调用的首尾、执行图节点流转与路由决策、模型调用与上游错误、MCP 调用与 MCP 探测，按约定级别与字段输出。
- 双落地（A+B）：始终写 stdout 供容器/docker logs 采集；配置了 `LOG_FILE` 时同时按大小轮转写文件，供本地直跑与单机私有化部署排查，API 与 outbox 各写各的文件，避免交错。
- 敏感内容纪律上移到日志层：日志只出标识、摘要、耗时、错误分类与长度/digest，不出提示词正文、模型响应正文、工具参数原文、凭据原文，URL 只出 `host + path`。
- 日志失败不反噬业务：写入或渲染异常被吞掉并降级，不改变请求结果与执行状态。

不改动：轨迹落库的字段与留存开关（`insight/execution-trace` 现状保留），以及现有审计事件的写入时机。

## Capabilities

### New Capabilities

- `platform/logging`: 后端日志的字段契约、上下文传播、关键事件覆盖、双落地与敏感内容边界。

### Modified Capabilities

<!-- 无。`insight/execution-trace` 的「敏感内容不进入普通日志」与 `platform/audit-and-secrets` 的「日志不得出现凭据原文」作为既有约束被本变更遵守，其需求文本不变。 -->

## Impact

- 代码：`shared/logging.py`（管道与字段处理器）、`settings.py`（文件落地配置项）、`shared/middleware.py`（tenant_id 绑定）、`main.py`、`workers/outbox.py`、`shared/messaging/publisher.py`、`contexts/execution/application/invocation_service.py`、`contexts/execution/runtime/nodes.py`、`contexts/execution/infrastructure/{tracing.py,llm/langchain_adapter.py,tool_adapter.py}`、`contexts/catalog/infrastructure/mcp_client.py`、`contexts/catalog/application/mcp_probe_service.py`、`contexts/insight/application/recorder.py`。
- 配置与运行：`backend/.env.example` 新增 `LOG_FILE` / `LOG_MAX_BYTES` / `LOG_BACKUP_COUNT`；`docs/dev-setup.md` 说明文件位置与轮转；`deploy/docker-compose.dev.yml` 保持默认 stdout（不写容器内文件）。
- 依赖：`structlog` 已在依赖中，不新增第三方包。
- 对外行为：HTTP 接口契约不变；变化只发生在日志输出与运维可见性上。
