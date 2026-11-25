# Design

## Context

现状由 `shared/logging.py` 的 `configure_logging` 建立：`structlog` 用 `PrintLoggerFactory` 直写 stdout，标准库另有一条 `basicConfig(stream=sys.stdout, format="%(message)s", force=True)` 通道。两者互不相通，因此 5 个使用 `logging.getLogger` 的模块（`tracing`、`recorder`、`publisher`、`outbox`、`invocation_service`）输出去掉级别、时间、`trace_id`，且 `extra` 字段被静默丢弃；`uvicorn` 自身的访问日志同样在管道之外。

请求级上下文已有基础：`TraceMiddleware` 生成并回传 `X-Trace-Id` / `X-Request-Id`，绑定到 `structlog.contextvars`；`shared/context.py` 另有一份供业务读取的 contextvars（trace_id / request_id / actor），但认证产生的 `tenant_id` 从未进入日志上下文。

真正需要证据的三个位置当前都是零日志：`llm/langchain_adapter.py` 把六类上游异常归一后直接抛；`catalog/infrastructure/mcp_client.py` 把建连、`initialize`、`call_tool` 包在同一个超时里且不留痕；`execution/runtime/nodes.py` 的 render / llm / tools / route 只有预算扣减。轨迹落库（`InsightCallRecorder`）能事后查，但执行中看不到，且不覆盖 MCP 建连阶段。

约束来自既有 spec：`insight/execution-trace` 要求「敏感内容不进入普通日志」，`platform/audit-and-secrets` 要求日志不得出现凭据原文。当前这两条是「因为没什么日志」而被动成立的，本设计必须把它们变成主动约束。

## Goals / Non-Goals

**Goals:**

- 一条 structlog 管道同时承接业务日志、标准库日志与 uvicorn 日志，字段集合一致。
- 上下文（`trace_id` / `request_id` / `tenant_id` / `run_id`）由 contextvars 自动合并，调用点不重复传参。
- 关键位置的事件清单落地，字段足以脱离数据库判断「哪一步、多久、成没成、为什么失败」。
- stdout + 可选文件双落地，两个进程各写各的文件；不配置目录时行为等同于纯 stdout。
- 敏感内容在日志层被结构性排除，而不是靠开发者自觉。

**Non-Goals:**

- 不引入 OTel / collector / Loki 客户端，不做日志上报。
- 不做日志查询接口或日志检索页面（属 `insight` 后续能力）。
- 不改动 `llm_call` / `tool_call` 表结构与正文留存开关。
- 不做日志采样、不做按租户分流到不同文件。
- 不替换 `shared/context.py` 既有的业务 contextvars，也不合并两套 contextvars。

## Decisions

### D1 单一管道：structlog 渲染 + 标准库桥接

`configure_logging` 改为组装 root handler，`structlog.configure` 的 `logger_factory` 换成 `structlog.stdlib.LoggerFactory()`，渲染交给 `structlog.stdlib.ProcessorFormatter` 挂在每个 handler 上。业务侧仍用 `structlog.get_logger()`，标准库侧保持 `logging.getLogger()`，两条路径在 handler 处汇合。

- 这样 `uvicorn.access`、`sqlalchemy`、`httpx` 的记录自动获得同样的字段与格式，`extra` 也不再丢失。
- 标准库记录没有 `event` 键：`ProcessorFormatter` 的外层 processor 用 `record.name` 与 `record.getMessage()` 合成 `event=logger_name` + `message` 字段，保证九字段契约不缺键。
- 备选「保留 `PrintLoggerFactory`，标准库另配一套 `dictConfig`」被否决：两套渲染逻辑意味着两种格式，正好是本变更要消除的问题。

### D2 字段契约由 processor 强制，而非调用点自觉

processor 顺序：

```
merge_contextvars            # trace_id / request_id / tenant_id / run_id
add_log_level                # level
add_logger_name              # logger
TimeStamper(key="timestamp", fmt="iso", utc=True)
ensure_contract              # 自写：补 service / environment，缺失键置 None
StackInfoRenderer + format_exc_info
ProcessorFormatter.wrap_for_formatter
-> JSONRenderer 或 ConsoleRenderer
```

`ensure_contract` 只做两件事：注入进程级 `service` 与 `environment`；把 `trace_id / request_id / tenant_id / run_id` 中缺失的键显式写成 `None`，避免下游按字段过滤时漏掉没有上下文的日志。`service` 由 `configure_logging(settings, service="api")` 传入并在 configure 时固定，不随请求变化。

`event` 命名规范：`根域.动作`，根域限定为 `invocation` / `runtime` / `llm` / `mcp` / `outbox` / `http`；禁止把 `tool_code`、`model_name` 等变量拼进 `event`，否则聚合维度失效。

### D3 双落地：stdout 恒开，`LOG_DIR` 可选

不使用 `basicConfig`，直接组装两个 handler，共用同一个 `ProcessorFormatter`：

| handler | 条件 | 说明 |
| --- | --- | --- |
| `StreamHandler(sys.stdout)` | 恒开 | 容器内由 `docker logs` / 采集器收取 |
| `RotatingFileHandler(<dir>/<service>.log)` | `LOG_DIR` 非空 | `maxBytes=LOG_MAX_BYTES`、`backupCount=LOG_BACKUP_COUNT`、`encoding="utf-8"`、`delay=True` |

选 `LOG_DIR` 而不是 `LOG_FILE`：文件名由 `service` 决定（`api.log` / `outbox.log`），从配置上就杜绝两个进程写同一文件互相交错，也少一个需要人工保证的常量。目录不存在时创建；创建或打开失败则降级为仅 stdout 并输出一条 `logging.degraded` 告警。

`_configured` 布尔守卫改为记录 `(level, json, log_dir)` 三元组：值未变则跳过，变了则重配（并 `structlog.reset_defaults()`），同时提供 `reset_logging()` 供测试使用。这修掉当前「第一次 `create_app()` 后配置永久冻结」的问题。

### D4 上下文绑定点：setter 内部同步，而非新增中间件

不在中间件里查认证结果（认证发生在路由依赖中，中间件拿不到 actor），改在 `shared/context.py` 的 `set_actor` 内部同步绑定/解绑 `tenant_id`。这样 HTTP 请求、后台任务、测试三条入口都自动覆盖，不需要每处调用方记得绑定。

- 请求级：`TraceMiddleware` 保持现有 `bind_contextvars(trace_id, request_id)`，`clear_contextvars()` 后重新绑定 `tenant_id`（若已认证）。
- 执行级：`invocation_service.invoke` 在创建 run 之后 `bind_contextvars(run_id=...)`，`finally` 解绑；`TracingModelPort` / `TracingToolPort` / MCP 调用点因此自动带 `run_id`，无需加参数。
- 进程级：`main.py` 传 `service="api"`，`workers/outbox.py` 传 `service="outbox"`。
- 跨线程注意：`asyncio.to_thread` 不自动传递 contextvars，若后续有线程池调用外部能力，需在提交前用 `contextvars.copy_context()` 复制；当前代码路径不涉及，作为约束写入实现说明。

### D5 日志与轨迹的分工

| 维度 | 日志 | 轨迹（数据库） |
| --- | --- | --- |
| 消费者 | 人（终端 / tail 文件）、采集器 | 控制台查询、指标聚合 |
| 内容 | 标识、决策、结果摘要、耗时、错误分类 | 参数摘要 / digest、Token、状态、耗时 |
| 正文 | 永远不出现 | 仅在 `RETAIN_PROMPT_CONTENT=true` 时落库 |
| 生命周期 | 由文件轮转与采集端决定 | 由数据保留策略决定 |

实现上复用 `insight` 已有的 `summarize_arguments` / `digest_payload` / `summarize_text`，不新写一套摘要逻辑，避免两处脱敏规则漂移。

### D6 事件清单与级别

| event | 级别 | 字段（除九字段外） | 位置 |
| --- | --- | --- | --- |
| `invocation.start` | INFO | `agent_id`、`release_id`、`env`、`kind`、`timeout_s`、`session_id` | `invocation_service.invoke` 创建 run 之后 |
| `invocation.end` | INFO | `status`、`steps`、`tokens`、`latency_ms`、`error_code` | 成功与失败两个收口 |
| `runtime.node` | DEBUG | `node`、`used_steps`、`used_tokens`、`message_count` | `nodes.py` 三个节点入口（装饰器） |
| `runtime.route` | DEBUG | `next`（`tools` / `end`）、`tool_calls` | `nodes.py: route()` |
| `llm.call` | INFO | `model_name`、`tool_count`、`input_tokens`、`output_tokens`、`latency_ms`、`status`、`error_code` | `TracingModelPort.chat` 收口 |
| `llm.upstream_error` | WARNING | `model_name`、`error_code`、`upstream_status`、`latency_ms` | `LangChainModelAdapter` 各 except 分支（只记归一码与状态码，不记 error body） |
| `mcp.call` | INFO / WARNING | `url_host`、`url_path`、`transport`、`tool_name`、`connect_ms`、`call_ms`、`is_error` | `SdkMcpSession.call_tool` |
| `mcp.probe` | INFO | `url_host`、`url_path`、`transport`、`tools_count`、`duration_ms`、`ok` | `SdkMcpSession.list_tools` |
| `outbox.publish_failed` | WARNING | `event_id`、`attempts`、`reason` | `publisher.py`（顺带修掉 `extra` 被吞） |

两个刻意的拆分：

1. `mcp.call` 分别测 `connect_ms`（建连 + `initialize`）与 `call_ms`（`call_tool`）。当前两者共用一个 `asyncio.timeout`，超时后无法判断卡在哪一阶段，这正是 MCP 排障最痛的点；拆开后「服务在监听但握手失败」与「远端工具执行超时」可以区分。
2. `llm.call` 放在 `TracingModelPort` 而不是 `LangChainModelAdapter`：`TracingModelPort` 已经持有 run、模型名与计时，且包住了全部异常分支，一处收口即可覆盖成功与失败；适配器层只补 `llm.upstream_error`（需要上游状态码，那里才有）。

级别选择依据：一次执行的首尾与每次外部调用是生产 INFO（默认可见）；节点流转与路由是高基数、只对排障有用，放 DEBUG，需要时用 `LOG_LEVEL=DEBUG` 打开。

### D7 失败降级

- handler 用子类化包装：`emit` 整体 `try/except` 后调用 `handleError`，不向调用方抛异常。文件被删除/磁盘满属最常见的现场，不能让日志拖垮请求。
- `ProcessorFormatter` 的 processor 链中不放大规模数据（不 `repr` 请求体），降低渲染抛错概率。
- configure 阶段文件打开失败 → 只留 stdout，输出 `logging.degraded` 一条，`LOG_DIR` 原值保留在字段里便于排查。

### D8 uvicorn 接入

`configure_logging` 末尾对 `uvicorn` / `uvicorn.error` / `uvicorn.access` 三个 logger 做：`handlers.clear()`、`propagate = True`、`level = LOG_LEVEL`。这样访问日志并入 root 管道，无需额外的 `--log-config` 文件，`--reload` 下子进程导入 app 时会重新执行并生效。

- 备选「关掉 access log，改由应用自己打 request 事件」被否决：等于重写一遍访问日志，且丢失 uvicorn 已有的状态码与耗时。
- 副作用：`docker-compose` 里不再需要为 app 容器配置 logging driver，stdout 仍是唯一出口。

## Risks / Trade-offs

- [日志量放大约 3–5 倍] → 生产默认 INFO 只保留 `invocation.*`、`llm.call`、`mcp.*`、`outbox.*`；`runtime.*` 固定 DEBUG；不引入采样，量级仍远小于轨迹落库。
- [日志层重新引入敏感内容] → 事件字段固定为标识/摘要/计数，正文类字段不进入事件签名；URL 只取 `urlsplit().hostname` 与 `.path`；错误只记归一码与状态码。用「事件字段集合断言」的单测兜底，防止后续有人顺手加 `content`。
- [Windows 下文件轮转与句柄占用] → 按 service 分文件后单进程独占；`delay=True` 避免未写先建；`--reload` 父进程不写文件，仅子进程持有句柄。
- [两套 contextvars 漂移] → `set_actor` 内部同步是实现层唯一连接点，测试覆盖「认证后日志带 tenant_id、未认证为空」即可锁定。
- [`cache_logger_on_first_use=True` 使测试重配失效] → `reset_logging()` 显式重置；`_configured` 改为键值比较。
- [uvicorn 私有 logger 名变更] → 名称来自 uvicorn 公开文档；实现时对 `logging.getLoggerObjects()` 做容错，缺失的 logger 直接跳过。

## Migration Plan

1. 重写 `shared/logging.py`（保留 `configure_logging(settings)` 签名兼容，新增 `service` 关键字并默认 `api`），部署无感。
2. 迁移 5 处标准库 logger 到 `get_logger`，`publisher` 的 `extra` 改为显式字段。
3. 按 D6 补事件与计时打点。
4. 配置项与文档：`.env.example` 增 `LOG_DIR`（默认空）、`LOG_MAX_BYTES`、`LOG_BACKUP_COUNT`；`docs/dev-setup.md` 写明 `logs/api.log` 与 `logs/outbox.log` 的位置与轮转规则；容器部署保持不设 `LOG_DIR`。
5. 回滚：不设 `LOG_DIR` 即回到纯 stdout；事件本身独立于控制流，删除或整体降级为 DEBUG 不影响任何业务行为。
