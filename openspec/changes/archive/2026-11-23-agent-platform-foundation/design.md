# Design

## Context

仓库当前只有需求基线 `docs/enterprise_agent_platform_12_modules_prd.html`，`openspec/` 已初始化，`specs/` 与 `changes/` 为空，没有任何可运行代码。这是完全从零开始的仓库，因此本文件同时承担技术架构基线的职责：后续所有变更都在这套分层、命名与契约上叠加。

动机见 `proposal.md`，需求见 `specs/`，此处不复述。约束来源：

- PRD 第 3 章设计原则：租户隔离、版本不可变、发布与执行解耦、资源引用可追溯、执行上下文独立、异步可靠、最小权限、可审计。
- PRD 第 6 章存储职责，按本期范围裁剪为 PostgreSQL、Redis、RabbitMQ。
- 已确认范围决策：知识库/RAG 由独立知识中台承担；RabbitMQ 采用且必要，用于服务间通信；本期先打通同步调用闭环。

## Goals / Non-Goals

**Goals:**

- 可 `conda activate agent-platform` 后直接启动的双工程骨架（API + Web），含迁移、配置、测试与本地依赖编排。
- 让"发布 → 执行"成为强绑定：执行只读快照，运行记录固定版本身份，指针切换不影响进行中的调用。
- 把跨模块耦合收敛为端口与事件两类契约，使异步任务、记忆、评估接入时不动领域代码。
- 建立与 PRD 一致的表命名、租户过滤与审计留痕惯例，避免后续模块各自发明。

**Non-Goals:**

- 不做服务拆分：本期只有单进程 API，`worker.py` 为空壳，异步消费链路属下一变更。
- 不建向量与对象存储：无 Milvus、MinIO、文档处理流水线。
- 不做多模型路由、降级、熔断：只有单模型直连与租户级限流。
- 不做可视化编排与 Multi-Agent：不预建 `Workflow*` 表，避免猜测领域。
- 不做会话记忆落表：`session_id` 仅作为入参透传并写入运行记录。

## Decisions

### D-1 架构形态：模块化单体，双入口进程

12 个能力归入 5 个限界上下文（platform / catalog / agent / execution / insight），同一代码库、同一数据库、同一进程启动；`worker.py` 作为未来消费进程入口占位。

替代：一期直接拆微服务 → 否决，团队规模与运维成本不匹配，且会让"发布记录 + 指针"的跨服务一致性变复杂；单层 `routers/services/models` 平铺 → 否决，12 个能力平铺会在数月内退化为跨模块直接读写表。

### D-2 技术栈与版本基线

| 层 | 选型 | 说明 |
|---|---|---|
| 运行时 | Python 3.12，conda 环境 `agent-platform` | `conda create -n agent-platform python=3.12`；`pip install -e ".[dev]"` 装入该环境，`pip freeze` 产出 `requirements.lock.txt` 作为可复现锁 |
| Web | FastAPI + uvicorn | DB 与外部调用全异步 |
| 持久化 | SQLAlchemy 2.0 async + asyncpg + Alembic | `Mapped[]` 声明式模型，迁移按上下文分批 |
| 校验与配置 | pydantic v2 + pydantic-settings | 配置单一来源 `settings.py`，`.env` 覆盖 |
| 编排 | LangGraph + langgraph-checkpoint-postgres | 只依赖 `StateGraph`、`ainvoke`、Checkpointer 三处 API |
| 模型接入 | langchain-openai，仅作 `ModelPort` 的一个实现 | tools/agents/chains 不使用 |
| 队列 | aio-pika | 本期只做发布侧 |
| 缓存 | redis-py (asyncio) | 限流计数、热点配置、短期锁 |
| 日志 | structlog（JSON） | 固定字段：`trace_id`、`tenant_id`、`request_id` |
| 前端 | Vue 3 + Vite + TS + Pinia + Vue Router + Element Plus | API 客户端由 `openapi-typescript` 从 `/openapi.json` 生成 |
| 测试 | pytest + pytest-asyncio + httpx.AsyncClient | 集成测试用 docker-compose 起真实 PG/Redis |

替代：poetry/uv 管理环境 → 否决，用户已指定 conda，双工具会造成路径与锁文件割裂；Flask/Django → 否决，异步栈与 OpenAPI 契约生成更契合前端。

### D-3 主键策略与租户隔离

主键统一 **UUIDv7，应用侧生成**（`shared/ids.py` 自实现约 20 行，不引第三方包）。时间有序保证 B-tree 插入局部性，跨进程生成无冲突，且不自增外暴露数据量。

隔离采用 **单库 + 全表 `tenant_id` + PostgreSQL RLS**：

- 事务开始执行 `SET LOCAL app.tenant_id = '<uuid>'`，RLS 策略读 `current_setting('app.tenant_id', true)`。
- 用事务级 `SET LOCAL` 而非会话级 `SET`，避免连接池把租户上下文带到下一个请求；连接归还前断言已清空。
- 应用层 `TenantContext` 是第一道防线，RLS 只做纵深防御：无上下文时返回空集而非全量。

替代：schema-per-tenant → 否决，迁移需 ×N 且跨租户统计困难；独立库 → 一期成本过高；BIGINT 自增 → 与 Outbox 多实例写入及未来拆服务冲突。

### D-4 LangChain / LangGraph 隔离边界

- `ModelPort` / `ToolPort` / `CheckpointerPort` 定义在 `domain/`，只依赖标准库与 pydantic 类型。
- LangChain 仅出现在 `execution/infrastructure/llm/langchain_adapter.py`；领域层禁止 import `langchain*`，用 import-linter 在 CI 断言。
- 图构建集中在 `runtime/builder.py`：输入 `AgentSnapshot`（纯数据），输出已编译图；节点通过闭包持有 ports，不外泄 LangGraph state 对象。
- 步数、超时、Token 预算由统一节点装饰器实现，不依赖框架的 recursion limit（那是递归深度，不是业务步骤）。

替代：直接用 HTTP 调 OpenAI 兼容接口 → 保留为第二个 adapter 的能力；`create_react_agent` / `AgentExecutor` → 否决，内部循环不可介入，无法可靠执行预算与取消。

### D-5 版本快照 + 绑定关系双写

| 载体 | 内容 | 用途 |
|---|---|---|
| `agent_version.snapshot` (JSONB) | 解析后的完整可执行配置：模型标识与参数、消息模板与变量、工具契约、运行策略 | 执行时唯一事实来源，Runtime 不查活配置 |
| `agent_version_binding` (行) | `resource_type` + `resource_version_id` 外键 | 授权校验、影响面分析、版本对比 |

一致性由"生成版本"单一用例在同一事务内保证。`snapshot` 带 `snapshot_schema_version: 1` 作为演进判别位。`config_hash = sha256(规范序列化 snapshot)`，规范化为键排序、去空白、数值统一表示。

替代：只存引用、执行时现查 → 否决，资源更新会让历史版本行为漂移，回滚与评估复现同时失效；只存快照 → 否决，无法回答"哪些 Agent 引用了被撤销授权的工具"。

### D-6 同步执行模型

`invoke` 也落运行记录：受理即创建 `run`，`RUNNING` → `SUCCESS` / `FAILED`。同步不等于无记录，否则可观测性与后续异步链路无法共用同一张表。

- 受理顺序：认证 → 租户上下文 → 解析环境指针 → 校验快照引用可用 → 校验入参 → 创建 run → 执行 → 落结果。
- 同步阈值 `sync_max_timeout_s = 120`（可配）；请求声明的执行上限超过阈值时在受理阶段拒绝并提示异步接口，避免 HTTP 层先超时。
- 预算检查点：图节点入口检查步骤数与累计 Token；总超时用 `asyncio.timeout` 包住整图调用。
- 失败分类写入 `run.error`：`INPUT_INVALID`、`RESOURCE_UNAVAILABLE`、`MODEL_ERROR`、`TOOL_ERROR`、`BUDGET_EXCEEDED`、`TIMEOUT`、`INTERNAL`。

替代：同步调用不入库、只在响应返回结果 → 否决，PRD 要求历史任务可追溯发行版本。

### D-7 事件：事务内 Outbox + RabbitMQ 发布侧

```
业务事务: 写业务表 + 写 outbox_event(PENDING)  ──commit──>
publisher(同进程协程): SELECT ... FOR UPDATE SKIP LOCKED
        └─> RabbitMQ topic exchange `agent_platform.events`, routing_key=事件类型
        └─> 更新 status=SENT / attempts++ / next_attempt_at 指数退避
```

- 事件类型：`agent.release.published`、`audit.recorded`（本期真实产生）；`task.*`、`tool.call.completed` 契约先定、后续产生。
- 信封固定字段：`event_id`、`event_type`、`occurred_at`、`tenant_id`、`trace_id`、`payload`、`schema_version`。
- 至少一次投递，消费方按 `event_id` 去重；发布侧不追求 exactly-once。

替代：事务内直接 publish → 双写不一致；纯 DB 轮询不引 MQ → 与"RabbitMQ 必需"冲突且跨服务仍需它；本期直接上完整任务队列 → 属 `async-task-worker`，会拖慢闭环验收。

### D-8 认证、凭据与授权判定

- 用户认证：邮箱 + Argon2id 密码 → HS256 JWT（默认 8 小时）。`AuthPort` 隔离，后续加 `OidcAdapter` 对接 SSO 只换实现。
- 服务身份：API Key 格式 `ap_<key_id>_<secret>`，库存 `key_id` 与 `sha256(secret)`，前缀可索引，明文仅创建时返回。
- 凭据：`secret_ref` 存 AES-GCM `ciphertext`，主密钥来自 `APP_SECRET_ENC_KEY`；`SecretBackend` 端口预留 Vault/KMS 实现。
- 授权：`RBAC(权限点) AND ResourceGrant(具体资源)` 双判定，统一入口 `AuthorizationService.check(actor, action, resource)`，禁止在 router 散写判断。

替代：JWT 另存会话表以支持即时吊销 → 本期用短有效期 + `user.status`/`api_key.status` 实时校验换简单；自建 OAuth2 授权服务器 → 超出中台职责。

### D-9 前端四屏与契约生成

固定四屏：Agent 列表、版本与快照（含两版本对比）、发布/回滚、调试台（输入 → 结果 → 调用轨迹）。类型来自 `npm run gen:api`（`openapi-typescript` 读 `/openapi.json`），统一 `client.ts` 处理 Bearer 注入与错误码映射，Pinia 只放当前租户与当前 Agent。不做画布、不做知识库页面。

### D-10 可观测：自建 trace 表 + 预留 OTel

`trace_id` 用 UUIDv7 在入口生成，存入 `contextvars`，经日志、RabbitMQ 消息头（`x-trace-id`）、出站 HTTP 头传播。本期落库 `llm_call`、`tool_call`、`execution_event`；不接 collector，但字段按 OTel 语义命名（`trace_id`、`span_id`、`parent_span_id`、`started_at`、`ended_at`、`status`），第四期加 exporter 不改表。

替代：一期直接接 Jaeger/Tempo → 否决，本地与生产都缺后端，会把闭环验证拖成基础设施项目。

## 系统架构与项目结构

### 运行时视图

```
Web (Vue3 SPA)                 业务系统 / SDK
      |  Bearer JWT                    |  API Key
      +--------------+-----------------+
                     v
        +----------------------------+
        | FastAPI (api 进程)          |
        | trace -> auth -> tenant    |
        | -> RLS -> router           |
        +-------------+--------------+
                      |
      +---------------+----------------+
      |               |                |
      v               v                v
  platform         catalog           agent
  identity/audit   model/prompt/tool version/release
      |               |                |
      +-------+-------+--------+-------+
              v                v
       +--------------+  +-----------------+
       | execution    |->| insight         |
       | runtime+ports|  | trace/llm/tool  |
       +------+-------+  +-----------------+
              | ModelPort / ToolPort / CheckpointerPort
              v
      LLM Provider (OpenAI 兼容) / 外部 HTTP / PG checkpoint

  outbox publisher (同进程协程) --> RabbitMQ topic exchange
  PostgreSQL(事实源) / Redis(限流·缓存·锁)
```

### 目录结构

```
agent-platform/
├── backend/
│   ├── pyproject.toml  requirements.lock.txt  alembic.ini
│   ├── alembic/{env.py,versions/}
│   ├── src/agent_platform/
│   │   ├── main.py            # create_app(): 中间件、路由、生命周期
│   │   ├── worker.py          # 消费进程入口占位
│   │   ├── settings.py
│   │   ├── shared/
│   │   │   ├── ids.py         # uuid7
│   │   │   ├── db.py          # engine/session/RLS 钩子
│   │   │   ├── context.py     # TenantContext / RequestContext (contextvars)
│   │   │   ├── errors.py  security.py  pagination.py
│   │   │   └── messaging/{outbox,publisher,envelope}.py
│   │   ├── contexts/
│   │   │   ├── platform/
│   │   │   │   ├── domain/  application/  infrastructure/  api/
│   │   │   ├── catalog/       # model / prompt / tool
│   │   │   ├── agent/         # Agent, Version, Snapshot, Release, Pointer
│   │   │   ├── execution/
│   │   │   │   ├── domain/    # Run, ExecutionContext, Budget, ports
│   │   │   │   ├── application/invocation_service.py
│   │   │   │   ├── infrastructure/{run_repository,langchain_adapter,
│   │   │   │   │                 http_tool_executor,checkpoint_store}.py
│   │   │   │   ├── runtime/{builder,nodes,executor}.py
│   │   │   │   └── api/
│   │   │   └── insight/
│   │   └── api/v1/router.py
│   └── tests/{unit,integration,e2e}/
├── frontend/
│   ├── package.json  vite.config.ts  tsconfig.json
│   └── src/{app,router,stores,api,views,components}/
├── deploy/docker-compose.dev.yml   # postgres, redis, rabbitmq
├── docs/  openspec/
```

### 分层与依赖规则（CI 强制）

```
api  ->  application  ->  domain  <-  infrastructure
                              (infrastructure 实现 domain 端口)
```

| 规则 | 说明 |
|---|---|
| 禁止跨上下文读写表 | 需要他人数据走 application service 或端口；`execution` 只经 `resolve_version()` 取快照 |
| `domain/` 不 import ORM / HTTP / LangChain | 仅标准库 + pydantic 值对象 |
| `api/` 不持有 Session | 事务边界在 application 用例内 |
| 一切查询带 tenant 过滤 | Repository 基类强制注入 `tenant_id`，禁止裸 `select()` |

## 领域模型

### 上下文映射

```
+======================== platform =========================+
| Tenant  Membership  Role/Permission  ResourceGrant        |
| ApiKey  SecretRef   AuditLog                              |
+------+----------------------------------+-----------------+
       | AuthorizationPort                | AuditPort
       v                                  v
+===== catalog ======+        +========= agent =============+
| ModelProvider      |        | Agent                       |
| Model              |<-------| AgentVersion (snapshot+hash)|
| Prompt/Version     |  版本  | VersionBinding              |
| Tool/Version       |  引用  | Release -> ReleasePointer   |
+---------+----------+        +--------------+--------------+
          | ResourceVersionPort             | release_id/version_id
          v                                 v
+====================== execution ==========================+
| Run --> RuntimeGraph --> ExecutionEvent                   |
| ports: ModelPort ToolPort CheckpointerPort OutboxPort     |
+---------------------------+-------------------------------+
                            | trace_id
                            v
+======================= insight ===========================+
| LLMCall  ToolCall  ExecutionEvent  RunMetric              |
+===========================================================+
```

### 聚合与不变量

| 聚合根 | 不变量 | 生命周期 |
|---|---|---|
| `Tenant` | 编码全局唯一；停用后其下一切请求被拒 | 创建 → 启用 → 停用（不物理删除） |
| `Membership` | `(tenant_id, user_id)` 唯一 | 邀请 → 启用 → 停用 |
| `ApiKey` | 明文仅创建时可见；过期或停用即失效 | 创建 → 轮换 → 停用 |
| `SecretRef` | 引用标识稳定、内容可替换、内容永不出库 | 创建 → 轮换 → 停用 |
| `Model` | 逻辑编码租户内唯一；provider 停用则不可用 | 草稿 → 启用 → 停用 |
| `Prompt` + `PromptVersion` | 版本生成即冻结；`(prompt_id, version_no)` 唯一 | 版本追加 |
| `Tool` + `ToolVersion` | 契约或认证变更必须出新版本；风险等级属版本 | 版本追加 |
| `Agent` | 编码租户内唯一；逻辑身份不含可执行配置 | 草稿 → 启用 → 归档 |
| `AgentVersion` | 快照不可变；`config_hash` 由快照确定；引用固定到具体资源版本 | 生成即冻结 |
| `Release` + `ReleasePointer` | 指针 `(tenant_id, agent_id, env)` 唯一；发布记录不可改 | 发布 → 切换/回滚 |
| `Run` | 受理即创建；固定 `release_id`、`version_id`、`trace_id` | RUNNING → SUCCESS/FAILED |

### 关键值对象

| 值对象 | 字段 | 约束 |
|---|---|---|
| `ResourceVersionRef` | `resource_type`、`resource_id`、`version_id`、`version_no` | 类型限 model / prompt / tool |
| `AgentSnapshot` | `snapshot_schema_version`、`model`、`prompt`、`tools[]`、`policy` | 纯数据，可序列化，不含运行时对象 |
| `ExecutionBudget` | `max_steps`、`timeout_s`、`token_budget`、`used_steps`、`used_tokens` | 单调递增计数，超限即抛预算异常 |
| `RunError` | `code`、`message`、`step`、`resource_ref` | `code` 取自失败分类枚举 |
| `EventEnvelope` | `event_id`、`event_type`、`occurred_at`、`tenant_id`、`trace_id`、`payload`、`schema_version` | 雪花式不可变结构 |

## 数据库设计

### 约定

- 五个 PostgreSQL schema 与上下文同名：`platform`、`catalog`、`agent`、`execution`、`insight`。表名用单数。
- 通用列：`id uuid pk`、`tenant_id uuid not null`（平台级表除外）、`created_at/updated_at timestamptz`、`created_by uuid`。
- 状态用带约束的 `text` + `CHECK`，不用枚举类型（迁移更轻）。
- 软删除统一用 `status`，不设 `deleted_at`，避免唯一索引与部分索引纠缠。
- 所有业务表建 `(tenant_id, ...)` 打头的复合索引；时间统一 UTC 存储。

### platform

```
tenant(id, code uq, name, status CHECK(ACTIVE|SUSPENDED), created_at)
user_account(id, email uq, password_hash, display_name, status)
membership(id, tenant_id, user_id, department_id null, status, uq(tenant_id,user_id))
role(id, tenant_id null, code, name, builtin bool, uq(tenant_id,code))
permission(id, code uq, name, resource_type, action)
role_permission(role_id, permission_id, pk(role_id,permission_id))
user_role(id, tenant_id, user_id, role_id, uq(tenant_id,user_id,role_id))
resource_grant(id, tenant_id, resource_type, resource_id, subject_type, subject_id,
               actions jsonb, granted_by, uq(tenant_id,resource_type,resource_id,subject_type,subject_id))
api_key(id, tenant_id, key_id uq, secret_hash, name, scopes jsonb, expires_at null, status)
secret_ref(id, tenant_id, name, backend, ciphertext, key_version, status,
           uq(tenant_id,name))
audit_log(id, tenant_id, actor_type, actor_id, action, resource_type, resource_id,
          result, detail jsonb, trace_id, created_at)
outbox_event(id, tenant_id, event_type, payload jsonb, status, attempts,
             next_attempt_at, created_at, sent_at null)
```

`audit_log` 与 `outbox_event` 只允许 INSERT；前者应用层无 UPDATE/DELETE 权限，后者仅 publisher 更新投递状态。

### catalog

```
model_provider(id, tenant_id null, name, adapter, base_url, secret_ref_id null, status)
model(id, tenant_id, provider_id, code, model_name, ctx_window, modalities jsonb,
      capabilities jsonb, param_template jsonb, price jsonb, status,
      uq(tenant_id,code))
prompt(id, tenant_id, code, name, description, status, uq(tenant_id,code))
prompt_version(id, prompt_id, version_no, messages jsonb, variables jsonb,
               config_hash, note, created_by, created_at, uq(prompt_id,version_no))
tool(id, tenant_id, code, name, description, type CHECK(HTTP), status, uq(tenant_id,code))
tool_version(id, tool_id, version_no, spec jsonb, risk_level CHECK(READ_ONLY|WRITE|HIGH),
             config_hash, uq(tool_id,version_no))
tool_grant(id, tenant_id, tool_id, granted_by, uq(tenant_id,tool_id))
```

### agent

```
agent(id, tenant_id, code, name, description, type, owner_id, tags jsonb, status,
      uq(tenant_id,code))
agent_version(id, agent_id, version_no, snapshot jsonb, snapshot_schema_version,
              config_hash, note, created_by, created_at, uq(agent_id,version_no))
agent_version_binding(id, agent_version_id, resource_type, resource_id,
                      resource_version_id, role, overrides jsonb,
                      uq(agent_version_id,resource_type,role))
agent_release(id, tenant_id, agent_id, agent_version_id, env CHECK(DEV|TEST|PROD),
              status, released_by, released_at)
agent_release_pointer(tenant_id, agent_id, env, release_id, updated_at,
                      pk(tenant_id,agent_id,env))          -- 唯一发布指针
```

`snapshot jsonb` 结构：

```json
{
  "snapshot_schema_version": 1,
  "model":  {"model_id": "...", "model_name": "gpt-4o", "params": {"temperature": 0.2}},
  "prompt": {"prompt_version_id": "...", "messages": [], "variables": []},
  "tools":  [{"tool_version_id": "...", "name": "echo", "spec": {}}],
  "policy": {"max_steps": 12, "timeout_s": 90, "token_budget": 20000}
}
```

### execution 与 insight

```
run(id, tenant_id, agent_id, agent_release_id, agent_version_id, env,
    caller_type, caller_id, session_id null, input jsonb, status,
    trace_id, started_at, finished_at null, error jsonb null,
    idx(tenant_id, agent_id, started_at))
execution_event(id, tenant_id, run_id, seq, type, payload jsonb, created_at,
                uq(run_id,seq))
llm_call(id, tenant_id, run_id, trace_id, span_id, model_id, model_name,
         input_tokens, output_tokens, cost_estimate, latency_ms, status, error_code null)
tool_call(id, tenant_id, run_id, trace_id, span_id, tool_version_id,
          request_digest, response_digest, latency_ms, status, error_code null)
checkpoint_ref(id, run_id, thread_id, checkpoint_id, created_at)
```

`checkpoint_ref` 只存 LangGraph checkpoint 的引用，不复制其内部结构。

### RLS 与连接钩子

```sql
ALTER TABLE execution.run ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON execution.run
  USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
```

`db.py` 在 `AsyncSession` 事务开始时执行 `SELECT set_config('app.tenant_id', :tid, true)`；`SET LOCAL` 语义等价且随事务结束自动失效。迁移脚本按上下文顺序：`platform → catalog → agent → execution → insight`，每个 revision 可 `downgrade` 回退。

## 分期规划

本变更是第 0 期，后续每期独立成一个 OpenSpec 变更，各自有可验收闭环。

| 期 | 变更名 | 主要内容 | 退出标准 |
|---|---|---|---|
| P0 | `agent-platform-foundation`（本变更） | 租户/RBAC/API Key、审计与密钥、模型/Prompt/工具注册、Agent 版本与发布指针、同步执行、基础 trace、Outbox 发布侧、四屏控制台 | 发布 → 同步调用跑通；回滚后旧 Run 仍指向旧 Release；跨租户访问被阻断 |
| P1 | `async-task-worker` | Task/Run 异步化、Worker 进程、租约心跳、幂等消费、重试退避、死信、取消/恢复、`task.*` 事件 | Worker 崩溃后任务被回收；重复投递不重复执行；取消在秒级生效 |
| P2 | `workflow-orchestration` | Workflow/WorkflowVersion、节点与条件分支、Multi-Agent 委派、调试运行、审批节点 | 画出含 LLM/工具/条件分支的流程并调试、发布 |
| P3 | `memory-management` | 会话、消息历史、摘要压缩、任务记忆、长期记忆治理 | 会话可持久化，摘要替换上下文但原始消息可查 |
| P4 | `evaluation-center` | 数据集、离线批量评估、RAG 指标（对接知识中台检索）、版本对比、人工评审 | 同一数据集对比两个版本并给出失败样例 |
| P5 | `governance-observability` | 配额体系、成本报表、告警规则、敏感信息治理、审批策略、OTel exporter | 配额与告警生效，成本可归因到租户与 Agent |

能力归属矩阵（`P0` 只含本变更）：

```
能力           P0        P1      P2      P3     P4     P5
Agent 管理     版本/发布  -      调用配置  -      -      -
模型管理       接入/限流  -      路由/降级  -     -     配额成本
Prompt         模板/版本  -      样例/敏感  -     -      -
Tool           注册/授权  -      风险/审批  -     -     审批策略
编排           -          -      全部      -      -      -
知识库         -          -      -         -     RAG指标 GraphRAG(外部)
记忆           -          -      -        全部    -      -
Runtime        同步执行   暂停恢复 -
任务           事件契约   全部    -         -      -      -
评估           -          -      -         -     全部    -
可观测         trace      指标    检索观测  -      -     告警报表
安全权限       地基       配额    -         -      -     细粒度治理
```

## Risks / Trade-offs

| 风险 | 缓解 |
|---|---|
| `AgentSnapshot` 一旦上线即为跨版本契约，后续加字段可能破坏旧版本执行 | 带 `snapshot_schema_version`，Runtime 按版本分发解析器，新增字段只做向后兼容扩展 |
| UUIDv7 依赖单调时间，时钟回拨可能产生重复或逆序 id | `ids.py` 内维护进程内计数器与上次时间戳，回拨时借用序列位并记录告警 |
| RLS 依赖事务内 `SET LOCAL`，漏设上下文会静默返回空集 | `db.py` 统一入口 + 启动自检用例；仓储层单测断言无租户上下文时查询为空 |
| 同步执行占用 HTTP 工作协程，长任务会拖垮实例 | 入参上限 + `asyncio.timeout` 硬超时 + 文档明确"长任务等 P1 异步" |
| LangGraph 升级破坏内部 API | 只依赖 `StateGraph`/`ainvoke`/Checkpointer 三处，封装在 `runtime/`，并 pin 版本 |
| Outbox publisher 与 API 同进程，进程退出可能延迟投递 | 幂等 + 重启续投；P1 拆为独立 worker 进程 |
| 审计表无限增长 | P0 只建 `(tenant_id, created_at)` 索引，P5 引入按月分区与归档 |
| 自建账号与未来 SSO 双轨 | `AuthPort` 单一入口，两种实现共存，用户可按邮箱归并 |
| 前端类型从 OpenAPI 生成，后端改契约会立即暴露 | 属预期收益；CI 增加 `gen:api` 与提交产物一致性检查 |

## Migration Plan

全新仓库，无存量数据迁移。

1. `conda create -n agent-platform python=3.12` → `pip install -e "backend[dev]"` → `pip freeze` 生成锁文件。
2. `docker compose -f deploy/docker-compose.dev.yml up -d` 启动 PostgreSQL 16、Redis 7、RabbitMQ 3.13（带 management 插件）。
3. `alembic upgrade head` 建五个 schema 与全部表，并创建 RLS 策略与应用角色。
4. 启动 `uvicorn agent_platform.main:app`，`/healthz` 与 `/openapi.json` 可用后，前端 `npm ci && npm run dev`。
5. 首个租户、平台管理员与内置角色由 `python -m agent_platform.bootstrap` 初始化（可重复执行，幂等）。

回滚策略：本变更无生产数据，整体回滚 = `alembic downgrade base` + 移除部署单元。业务侧回滚指发布指针切回历史 Release，属运行时能力而非部署回滚。

## Open Questions

- 生产环境是否已有可复用的 PostgreSQL / Redis / RabbitMQ 集群及其 vhost、账号权限由谁提供（影响部署清单，不影响本期设计与任务拆分）。
- `agent_version` 是否需要在 P0 支持"一个版本绑定多个 Prompt"（如角色卡 + 用户模板分离）；当前按单 Prompt 设计，若确认为多 Prompt 只需扩展 `snapshot.prompts[]` 与绑定表，不改其余结构。
- `sync_max_timeout_s` 的最终取值需压测后确认，当前取 120 秒。
