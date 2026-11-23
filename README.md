# Agent Platform

企业级 Agent 中台。一期落地「配置 → 不可变版本 → 环境发布 → 同步调用 → 全链路可追溯」的最小闭环：模型、Prompt、工具与 Agent 版本统一纳管，按环境发布指针切换，调用链路全程 trace 可查。

- 后端：Python 3.12 + FastAPI 模块化单体（5 个限界上下文），LangGraph 承载运行时
- 前端：Vue 3 + Vite + TypeScript + Element Plus 控制台
- 存储/中间件：PostgreSQL 16（RLS 多租户隔离）、Redis 7（限流/缓存）、RabbitMQ 3.13（Outbox 事件）
- 当前版本：`0.1.0`

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多租户与权限 | 租户 / 成员 / 轻量 RBAC（18 个权限点 + 3 个内置角色）+ 资源级授权（ResourceGrant），PostgreSQL RLS 做纵深防御 |
| 密钥与审计 | 第三方凭据只存引用，密文用 AES-GCM 加密；关键操作全量审计留痕 |
| 模型注册 | OpenAI 兼容供应商接入、参数模板与范围校验、连通性探测、Redis 维度 RPM 与并发限流 |
| Prompt 注册 | 变量 Schema 校验、不可变版本、版本 diff、服务端渲染 |
| 工具注册 | HTTP 工具与 MCP 工具（`streamable_http` / `sse`）+ JSON Schema 入出参 + 风险等级 + 租户级授权，受控执行 |
| Agent 版本与发布 | 逻辑身份 → 不可变 `AgentVersion` 快照（`config_hash`）→ `Release` + 环境指针（DEV/TEST/PROD）→ 回滚；发布事件经 Outbox 投递到 RabbitMQ |
| 同步调用 | `POST /api/v1/agents/{id}/invoke`：解析环境指针 → 固定版本 → LangGraph 执行（受步骤数 / 超时 / Token 预算约束） |
| 可观测 | `trace_id` 贯穿 Run / LLM 调用 / 工具调用，支持轨迹查询与成功率、P50/P95 指标聚合 |

## 目录结构

```
agent-platform/
├── backend/                 # FastAPI 后端（src-layout，包名 agent_platform）
│   ├── src/agent_platform/
│   │   ├── main.py          # 应用工厂 create_app()，/healthz
│   │   ├── settings.py      # pydantic-settings 配置
│   │   ├── bootstrap.py     # 权限点/角色/租户/管理员幂等初始化
│   │   ├── api/v1/router.py # 聚合各上下文路由，挂 API_PREFIX
│   │   ├── contexts/
│   │   │   ├── platform/    # 租户、成员、RBAC、API Key、密钥、授权、审计
│   │   │   ├── catalog/     # 模型、Prompt、工具注册
│   │   │   ├── agent/       # Agent 身份、版本快照、发布/回滚
│   │   │   ├── execution/   # 运行时（LangGraph）、ModelPort/ToolPort、轨迹
│   │   │   └── insight/     # LLM/工具调用轨迹、指标聚合、内容保留策略
│   │   ├── shared/          # db、RLS、中间件、日志、ID、消息（Outbox/Envelope）
│   │   └── workers/outbox.py# Outbox 事件发布进程
│   ├── alembic/versions/    # 0001_platform … 0005_insight
│   ├── scripts/check_deps.py# PG / Redis / RabbitMQ 连通性自检
│   ├── tests/{unit,integration,e2e}/
│   └── .env.example
├── frontend/                # Vue 3 控制台（五个入口）
│   ├── src/views/           # AgentListView / PromptListView / PromptDetailView / AgentVersionsView / ReleaseView / PlaygroundView
│   ├── src/components/prompt/  # 消息编辑、变量表、渲染预览、版本对比
│   ├── src/api/             # client.ts、endpoints.ts、schema.d.ts（由 OpenAPI 生成）
│   └── scripts/gen-api.mjs  # 类型生成
├── deploy/
│   ├── docker-compose.dev.yml    # PG 16 + Redis 7 + RabbitMQ 3.13（仅开发依赖）
│   └── postgres/init/01-app-role.sql
├── docs/
│   ├── dev-setup.md                       # 本地环境搭建（照抄可用）
│   └── enterprise_agent_platform_12_modules_prd.html   # 需求基线 PRD
└── openspec/                # 规格驱动开发工件（proposal / design / tasks / specs）
```

## 技术栈

**后端**：FastAPI、uvicorn、Pydantic v2 + pydantic-settings、SQLAlchemy 2.0 (async) + Alembic、asyncpg / psycopg、LangGraph + langgraph-checkpoint-postgres、langchain-openai（仅作 ModelPort 实现）、mcp（MCP 客户端）、uvicorn（测试内 MCP server）、aio-pika、redis、structlog、argon2-cffi、PyJWT、cryptography、orjson、httpx、jsonschema。

**前端**：Vue 3.5、vue-router 4、Pinia、Element Plus、TypeScript 5.7、Vite 6、vue-tsc、openapi-typescript。

**约定**：领域层禁止直接 import langchain / langgraph，全部通过 `ModelPort` / `ToolPort` 端口隔离；全表携带 `tenant_id` 并由 RLS 兜底。

## 快速开始

前置要求：Docker、conda（或任意 Python 3.12 环境）、Node.js 20+。以下命令默认在仓库根目录执行。

### 默认测试账号

执行过 `python -m agent_platform.bootstrap` 后，可直接用内置管理员登录控制台（http://localhost:5172）：

| 项目 | 值 |
| --- | --- |
| 租户编码 | `default` |
| 邮箱 | `admin@example.com` |
| 密码 | `ChangeMe123!` |

该账号对应内置角色 `tenant_admin`，拥有全部 18 个权限点。仅用于本地开发，生产环境务必替换。

### 1. 启动依赖服务

```bash
docker compose -f deploy/docker-compose.dev.yml up -d
docker compose -f deploy/docker-compose.dev.yml ps   # 三个容器应为 healthy
```

| 服务 | 宿主机端口 | 说明 |
| --- | --- | --- |
| PostgreSQL | 5432 | 库 `agent_platform`，超级用户 `agent/agent`，应用用户 `agent_app/agent_app` |
| Redis | 6380 | 避开本机已有 6379 |
| RabbitMQ | 5672 / 15672 | 管理台 http://localhost:15672（`agent/agent`） |

`agent_app` 为非超级用户，RLS 才会真正生效，由 `deploy/postgres/init/01-app-role.sql` 首次初始化时创建。

### 2. 安装后端

```bash
conda create -n agent-platform python=3.12 -y
conda activate agent-platform
cd backend
pip install -e ".[dev]"
```

### 3. 配置环境变量

```bash
# backend/
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
```

关键点：

- `DATABASE_URL` 必须使用 `agent_app`（非超级用户），否则 RLS 失效；
- `MIGRATION_DATABASE_URL` 使用 `agent`（超级用户）执行迁移与引导；
- `ENVIRONMENT=prod` 时必须替换 `JWT_SECRET` 与 `SECRET_ENC_KEY`，否则启动报错。

### 4. 建表与初始化数据

```bash
# backend/
alembic upgrade head
alembic current                  # 应为 0005_insight (head)
python -m agent_platform.bootstrap
```

`bootstrap` 幂等，默认创建租户 `default`、内置角色与管理员（账号见上方「默认测试账号」）。可用 `BOOTSTRAP_TENANT_CODE`、`BOOTSTRAP_ADMIN_EMAIL`、`BOOTSTRAP_ADMIN_PASSWORD` 等覆盖。

### 5. 启动后端

```bash
# backend/
uvicorn agent_platform.main:app --reload --port 8010
```

自检：

```bash
curl http://localhost:8010/healthz
curl -X POST http://localhost:8010/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"tenant_code":"default","email":"admin@example.com","password":"ChangeMe123!"}'
```

登录返回的 `access_token` 用于后续请求：`Authorization: Bearer <token>`。

### 6. 启动前端

```bash
cd frontend
npm install
npm run dev        # http://localhost:5172 ，/api 代理到 8010
```

后端不在 8010 时：`VITE_API_TARGET=http://localhost:8000 npm run dev`（PowerShell 用 `$env:VITE_API_TARGET="..."`）。

控制台五个入口：Agent 列表 → Prompt 管理（列表 + 详情：消息模板与变量编辑、渲染预览、版本历史与对比）→ 版本与快照（含 diff）→ 发布与回滚 → 调试台（同步调用 + 轨迹）。

Prompt 页面以 `/auth/me` 返回的权限集合为准：只有 `prompt:read` 的只读账号仍可查看模板、变量、版本与渲染预览，但「新建 / 保存为新版本」等写入口不显示（写操作对应 `prompt:manage`）。

生成后端类型（后端需已启动，默认读取 `http://localhost:8010/openapi.json`）：

```bash
npm run gen:api    # 可用 OPENAPI_URL 覆盖
npm run typecheck
```

## 环境变量

完整清单见 [`backend/.env.example`](backend/.env.example)，分组如下：

| 分组 | 变量 |
| --- | --- |
| 应用 | `APP_NAME`、`ENVIRONMENT`、`DEBUG`、`API_PREFIX` |
| 数据库 | `DATABASE_URL`、`MIGRATION_DATABASE_URL`、`DB_POOL_SIZE`、`DB_MAX_OVERFLOW`、`DB_ECHO` |
| 缓存/消息 | `REDIS_URL`、`RABBITMQ_URL`、`EVENTS_EXCHANGE`、`OUTBOX_POLL_INTERVAL_S`、`OUTBOX_BATCH_SIZE`、`OUTBOX_PUBLISHER_ENABLED` |
| 安全 | `JWT_SECRET`、`JWT_ALGORITHM`、`JWT_TTL_SECONDS`、`SECRET_ENC_KEY`、`API_KEY_PREFIX` |
| 执行预算 | `SYNC_MAX_TIMEOUT_S`、`DEFAULT_MAX_STEPS`、`DEFAULT_TIMEOUT_S`、`DEFAULT_TOKEN_BUDGET` |
| 模型调用 | `MODEL_PROBE_TIMEOUT_S`、`DEFAULT_MODEL_RPM_LIMIT`、`DEFAULT_MODEL_CONCURRENCY_LIMIT` |
| 日志/隐私 | `LOG_LEVEL`、`LOG_JSON`、`RETAIN_PROMPT_CONTENT` |
| 引导脚本 | `BOOTSTRAP_TENANT_CODE`、`BOOTSTRAP_TENANT_NAME`、`BOOTSTRAP_ADMIN_EMAIL`、`BOOTSTRAP_ADMIN_PASSWORD`、`BOOTSTRAP_ADMIN_NAME` |
| 前端构建期 | `VITE_API_TARGET`（Vite 代理）、`OPENAPI_URL`（`gen:api`） |

## 常用命令

```bash
# 后端（backend/）
pytest                                   # 全量测试（单元 + 集成 + 端到端）
pytest tests/e2e                         # 端到端验收
ruff check src tests                     # 静态检查（当前质量门槛）
alembic revision --autogenerate -m "msg"
python -m agent_platform.workers.outbox  # 启动 Outbox 事件发布进程
python scripts/check_deps.py             # 依赖连通性自检

# 前端（frontend/）
npm run dev / build / preview / typecheck / gen:api
```

## API 概览

所有业务路由统一挂载在 `API_PREFIX`（默认 `/api/v1`），`/healthz` 位于根路径。

| 上下文 | 主要路由 |
| --- | --- |
| platform | `/auth/register`、`/auth/login`、`/auth/me`、`/tenants`、`/members`、`/api-keys`、`/secrets`、`/grants`、`/audit-logs` |
| catalog | `/model-providers`、`/models`（含 `/probe`、`/validate-params`）、`/prompts`、`/prompt-versions`、`/tools`、`/tool-versions/{id}/execute` |
| agent | `/agents`（分页）、`/agents/{id}/copy`、`/agents/{id}/activate`、`/agents/{id}/deactivate`、`/agents/{id}/archive`、`/agents/{id}`（DELETE）、`/agents/{id}/versions`、`/agents/{id}/versions/{version_id}/readiness`、`/agent-versions/diff`、`/agents/{id}/releases`、`/agents/{id}/pointers/{env}`、`/agents/{id}/rollback` |
| execution | `/agents/{agent_id}/invoke`（响应头返回 `X-Trace-Id`） |
| insight | `/runs/{run_id}/trace`、`/metrics/runs` |

后端启动后可访问 `/docs`（Swagger UI）与 `/openapi.json`。

### Agent 创建、配置版本与复制

**带初始配置创建**：`config` 可选，提供时在同一事务内装配并生成版本 1，任一引用不可用则整体回滚，不会留下无配置的 Agent。

```bash
curl -X POST http://localhost:8010/api/v1/agents \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "code": "support-bot",
    "name": "Support Bot",
    "config": {
      "model_id": "'$MODEL_ID'",
      "prompt_version_id": "'$PROMPT_VERSION_ID'",
      "tool_version_ids": ["'$TOOL_VERSION_ID'"],
      "params": {"temperature": 0.3},
      "policy": {"max_steps": 6, "timeout_s": 30, "token_budget": 8000, "session_ttl_h": 168, "history_token_limit": 4000}
    }
  }'
```

**保存新版本**：只提交引用与策略。可选 `base_version_id` 声明本次编辑所依据的版本；它不是最新版本时返回 `409 AGENT_VERSION_CONFLICT`，`details[0]` 含 `base_version_no`、`latest_version_no` 与 `diff`（基准到最新的差异摘要）。用户确认后省略 `base_version_id` 重新提交即可覆盖保存。

```bash
curl -X POST http://localhost:8010/api/v1/agents/$AGENT_ID/versions \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "model_id": "'$MODEL_ID'",
    "prompt_version_id": "'$PROMPT_VERSION_ID'",
    "tool_version_ids": [],
    "policy": {"max_steps": 8},
    "base_version_id": "'$BASE_VERSION_ID'"
  }'
```

**复制 Agent**：必须提供未被占用的新编码。复制来源最新快照为新 Agent 的版本 1，配置摘要与来源一致；不复制发布记录、发布指针与运行历史，来源无版本时只创建身份。

```bash
curl -X POST http://localhost:8010/api/v1/agents/$AGENT_ID/copy \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"code": "support-bot-copy", "name": "Support Bot (copy)"}'
```

**分页列表与发布体检**：`GET /agents` 返回 `{items,total,page,page_size}`，支持 `status`、`type`、`tag`、`q` 筛选，每项附 `environments`（DEV/TEST/PROD 当前生效版本，未发布为 `null`）。发布前的 `readiness` 接口逐个绑定返回可用性与原因，并附最近一次同 `config_hash` 的预览摘要（无预览为 `null`）。

```bash
curl "http://localhost:8010/api/v1/agents?page=1&page_size=20&status=ACTIVE" \
  -H "Authorization: Bearer $TOKEN"

curl http://localhost:8010/api/v1/agents/$AGENT_ID/versions/$VERSION_ID/readiness \
  -H "Authorization: Bearer $TOKEN"
```

**调试预览**：`POST /agents/{id}/preview` 只提交引用与本次输入，复用保存版本的同一装配管线，返回 run id、输出、`config_hash` 与错误。不产生版本、不改发布指针；同一组引用得到的 `config_hash` 与保存的版本一致。权限点 `agent:invoke` 即可调用（成员可预览，不能保存）。

```bash
curl -X POST http://localhost:8010/api/v1/agents/$AGENT_ID/preview \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "model_id": "'$MODEL_ID'",
    "prompt_version_id": "'$PROMPT_VERSION_ID'",
    "tool_version_ids": [],
    "params": {"temperature": 0.2},
    "policy": {"max_steps": 4},
    "input": {"question": "hello"}
  }'
```

## 工具接入（HTTP / MCP）

注册工具时用 `type` 声明接入类型：`HTTP`（缺省）或 `MCP`。MCP 工具指向远端 MCP server 的某一个工具，契约在注册时校验，运行期失败只返回稳定错误码，不透传远端原始响应。

- 传输只支持远程 HTTP 类：`streamable_http`（缺省）与 `sse`；声明 `stdio` 会被拒绝（平台不托管远端进程）。
- `spec.url` 必须是绝对 `http(s)` 地址，`spec.tool_name` 为远端工具名，`spec.timeout_s` 上限 60 秒。
- `input_schema` / `output_schema` 可选；填写后入参在执行前校验、出参在返回后校验，不符则分别返回 `VALIDATION_FAILED` / `TOOL_OUTPUT_INVALID`。
- 鉴权复用密钥引用：`"auth": {"type": "bearer" | "header", "secret_ref_id": "<secret id>"}`，密钥不会出现在轨迹里。
- 失败映射：远端 `isError`、连接/协议异常 → `TOOL_MCP_ERROR`；超时 → `TOOL_TIMEOUT`。平台不做自动重试。

```bash
curl -X POST http://localhost:8010/api/v1/tools \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "code": "knowledge.search",
    "name": "知识检索",
    "type": "MCP",
    "spec": {
      "transport": "streamable_http",
      "url": "https://knowledge.example.com/mcp",
      "tool_name": "search_documents",
      "timeout_s": 10,
      "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
      "output_schema": {"type": "object", "properties": {"hits": {"type": "integer"}}, "required": ["hits"]}
    }
  }'

# 授权给租户（未授权的租户调用会被拒绝）
curl -X POST http://localhost:8010/api/v1/tools/{tool_id}/grant -H "Authorization: Bearer $TOKEN"
```

把 `/api/v1/tools/{tool_id}/versions` 返回的版本 id 绑进 Agent 版本（`tool_version_ids`）并发布，即可在调用中触发该 MCP 工具；轨迹里会记录远端 `tool_name`、耗时与状态。
