# Tasks

## 1. 统一日志管道

- [x] 1.1 重写 `backend/src/agent_platform/shared/logging.py`：root handler 改用 `ProcessorFormatter`，`logger_factory` 换 `structlog.stdlib.LoggerFactory()`，加入 `ensure_contract` processor（补 `service`/`environment`、缺失上下文键置 `None`），`configure_logging(settings, service="api")` 以 `(log_level, log_json, log_dir)` 幂等，新增 `reset_logging()`；验证：新增 `backend/tests/unit/test_logging_pipeline.py`，用 `capsys` 断言单行 JSON 含九个固定字段、`event` 为点分命名，且 `reset_logging()` 后变更 `log_level` 能生效
- [x] 1.2 在 `settings.py` 增加 `log_dir: str | None = None`、`log_max_bytes: int`、`log_backup_count: int`；验证：`backend/tests/unit/test_settings.py` 补用例，未设置时 `log_dir is None`，设置后按值返回
- [x] 1.3 实现文件落地：`LOG_DIR` 非空时增加 `RotatingFileHandler(<dir>/<service>.log)`（`encoding="utf-8"`、`delay=True`、按 `log_max_bytes`/`log_backup_count` 轮转），两个 handler 共用同一 formatter；验证：单测以 `tmp_path` 配置目录后写日志，断言 `api.log` 生成且每行可解析，写入超过上限后出现 `.1` 备份文件
- [x] 1.4 实现降级：文件目录不可创建或 handler `emit` 失败时只保留 stdout，输出一条 `logging.degraded`，不向调用方抛异常；验证：单测指向不存在且不可写的路径（如其他用户目录或文件占位同名目录），断言配置成功、日志仍出现在 stdout、业务函数不抛错
- [x] 1.5 接管 uvicorn 日志：对 `uvicorn` / `uvicorn.error` / `uvicorn.access` 清空 handlers、`propagate = True`、按 `LOG_LEVEL` 设级，缺失的 logger 跳过；验证：单测直接向 `logging.getLogger("uvicorn.access")` 写一条记录，`capsys` 断言它出现在同一 JSON 管道且字段齐全

## 2. 迁移标准库 logger

- [x] 2.1 把 `contexts/execution/infrastructure/tracing.py`、`contexts/insight/application/recorder.py`、`shared/messaging/publisher.py`、`workers/outbox.py`、`contexts/execution/application/invocation_service.py` 的 `logging.getLogger` 全部改为 `shared.logging.get_logger`，删除对应 `import logging`；验证：`rg "logging\.getLogger" backend/src` 无业务命中，`pytest backend/tests/unit` 全绿
- [x] 2.2 修掉 `publisher.py` 被吞掉的字段：`extra={"event_id": ...}` 改为显式键值传参，`outbox` 启动日志的 `exchange` 同理；验证：新增单测用替身发布器触发一次失败，`capsys` 断言输出含 `event=outbox.publish_failed` 与 `event_id`，不再丢字段
- [x] 2.3 迁移后核对失败路径仍只告警：`TracingModelPort` / `TracingToolPort` 的 recorder 异常不得改变执行结果；验证：单测注入抛异常的 recorder，断言调用返回正常且输出一条 warning 事件

## 3. 上下文绑定

- [x] 3.1 `shared/context.py` 的 `set_actor` 在设置/清除 actor 时同步 `bind_contextvars(tenant_id=...)` / `unbind_contextvars("tenant_id")`；验证：新增 `backend/tests/unit/test_logging_context.py`，认证后输出的日志含 `tenant_id`，清除后该字段为 `null`
- [x] 3.2 `invocation_service.invoke` 在创建 run 后绑定 `run_id`，`finally` 解绑；验证：同一单测文件用假模型跑一次执行，断言执行期间所有日志带同一 `run_id`，函数返回后上下文已清空
- [x] 3.3 进程级 `service` 标注：`main.py` 传 `service="api"`，`workers/outbox.py` 传 `service="outbox"`，`bootstrap.py` 如启用日志则传 `service="bootstrap"`；验证：单测分别以两个 service 名配置，断言输出字段对得上；`python -m agent_platform.workers.outbox` 启动后第一行日志 `service=outbox`
- [x] 3.4 核对 HTTP 链路标识回传不变：`X-Trace-Id` / `X-Request-Id` 仍出现在响应头，且与日志中的值一致；验证：`backend/tests/integration` 中发一次请求，断言响应头与捕获日志的 `trace_id` 相等

## 4. 关键位置事件

- [x] 4.1 `TracingModelPort.chat` 成功与失败两个分支都输出 `llm.call`（`model_name`、`tool_count`、`input_tokens`、`output_tokens`、`latency_ms`、`status`、`error_code`）；验证：新增 `backend/tests/unit/test_llm_call_logging.py`，用替身 `ModelPort` 与 recorder，断言事件名与字段，并断言输出中不出现提示词与响应正文
- [x] 4.2 `LangChainModelAdapter` 每个 `except` 分支输出 `llm.upstream_error`（归一后 `error_code`、`upstream_status`、`latency_ms`）；验证：同文件用抛 `openai.RateLimitError` / `APITimeoutError` 的替身客户端，断言事件存在且不含上游错误正文
- [x] 4.3 `runtime/nodes.py` 给三个节点加计时装饰器输出 `runtime.node`（`node`、`used_steps`、`used_tokens`、`message_count`），`route()` 输出 `runtime.route`（`next`、`tool_calls`），级别 DEBUG；验证：新增 `backend/tests/unit/test_runtime_node_logging.py`，以 DEBUG 级别跑一次「llm → tools → llm」流程，断言节点事件顺序与路由取值 `tools` / `end`
- [x] 4.4 `SdkMcpSession.call_tool` 把建连 + `initialize` 与 `call_tool` 分别计时并输出 `mcp.call`（`url_host`、`url_path`、`transport`、`tool_name`、`connect_ms`、`call_ms`、`is_error`），`list_tools` 输出 `mcp.probe`（`tools_count`、`duration_ms`、`ok`）；验证：新增 `backend/tests/unit/test_mcp_client_logging.py`，用替身 transport 与会话断言两个耗时键独立存在、URL 只含 host 与 path，且 `test_mcp_client_list_tools.py` 仍绿
- [x] 4.5 `invocation_service` 输出 `invocation.start`（`agent_id`、`release_id`、`env`、`kind`、`timeout_s`）与 `invocation.end`（`status`、`steps`、`tokens`、`latency_ms`、`error_code`）；验证：`backend/tests/integration` 各跑一次成功与失败的同步调用，断言两条事件成对出现且失败用例带错误分类

## 5. 配置与文档

- [x] 5.1 `backend/.env.example` 增加 `LOG_DIR=`、`LOG_MAX_BYTES=10485760`、`LOG_BACKUP_COUNT=5` 及一行说明；验证：复制为 `.env` 后 `python -c "from agent_platform.settings import get_settings; print(get_settings().log_dir)"` 输出 `None`
- [x] 5.2 `docs/dev-setup.md` 补「日志位置与格式」小节：默认只 stdout、设置 `LOG_DIR` 后 `logs/api.log` 与 `logs/outbox.log` 的路径与轮转规则、容器内保持 `docker logs`、`jq` 过滤示例；验证：按文档设置 `LOG_DIR=logs` 启动后，两个进程各自生成文件且内容不交错

## 6. 集成验证

- [x] 6.1 全量测试通过：`cd backend && pytest`；确认无新增警告与失败，日志相关单测覆盖字段契约、降级、上下文与事件
- [x] 6.2 手工端到端：设 `LOG_DIR=logs`、`LOG_LEVEL=DEBUG` 启动 API 与 outbox，从控制台发起一次带工具的调用，`tail -f logs/api.log` 中能按同一 `trace_id` 串起 `invocation.start` → `runtime.node` → `llm.call` → `mcp.call` / `tool` → `invocation.end`，且全文件搜不到提示词正文、工具参数原值与凭据

## Workflow follow-up

- 评审通过后归档该变更，并把 `platform/logging` 能力同步进主 specs。
