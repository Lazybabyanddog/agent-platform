# 本地开发环境搭建（照抄可用）

> 适用：Windows / macOS / Linux，已安装 Docker、conda、Node.js 20+。
> 全部命令默认在仓库根目录 `agent-platform/` 下执行，涉及后端的命令会先 `cd backend`。

## 1. 启动依赖：PostgreSQL 16 / Redis 7 / RabbitMQ 3.13

```bash
docker compose -f deploy/docker-compose.dev.yml up -d
docker compose -f deploy/docker-compose.dev.yml ps
```

三个容器都应为 healthy。端口约定：

| 服务 | 宿主机端口 | 说明 |
| --- | --- | --- |
| PostgreSQL | 5432 | 库 `agent_platform`，超级用户 `agent/agent`，应用用户 `agent_app/agent_app` |
| Redis | 6380 | 避开本机已有的 6379 |
| RabbitMQ | 5672 / 15672 | 管理台 http://localhost:15672 （`agent/agent`） |

`agent_app` 是非超级用户，RLS 才会真正生效；它由 `deploy/postgres/init/01-app-role.sql` 在首次初始化时创建。

## 2. 创建 Python 环境并安装后端

```bash
conda create -n agent-platform python=3.12 -y
conda activate agent-platform
cd backend
pip install -e ".[dev]"
```

## 3. 配置环境变量

```bash
# 仍在 backend/ 下
cp .env.example .env      # Windows PowerShell: Copy-Item .env.example .env
```

`.env` 中的关键两项：

- `DATABASE_URL`：应用连接，必须用 `agent_app`（非超级用户）；
- `MIGRATION_DATABASE_URL`：迁移与引导连接，用 `agent`（超级用户）。

`ENVIRONMENT=prod` 时必须修改 `JWT_SECRET` 与 `SECRET_ENC_KEY`，否则启动直接报错。

## 4. 建表

```bash
# backend/
alembic upgrade head
alembic current        # 应为 0006_tool_call_tool_index (head)
```

当前共 6 个迁移，依次为 `0001_platform` → `0002_catalog` → `0003_agent` → `0004_execution` → `0005_insight` → `0006_tool_call_tool_index`。

回退验证（可选）：

```bash
alembic downgrade -1
alembic upgrade head
```

## 5. 初始化内置数据（幂等，可重复执行）

```bash
# backend/
python -m agent_platform.bootstrap
```

默认创建：

- 18 个权限点与 `platform_admin` / `tenant_admin` / `member` 三个内置角色；
- 租户 `default`；
- 管理员 `admin@example.com / ChangeMe123!`，并授予 `tenant_admin`。

可用环境变量覆盖：

```bash
BOOTSTRAP_TENANT_CODE=acme \
BOOTSTRAP_TENANT_NAME="Acme" \
BOOTSTRAP_ADMIN_EMAIL=admin@acme.com \
BOOTSTRAP_ADMIN_PASSWORD='S3cure-Pass!' \
python -m agent_platform.bootstrap
# PowerShell 不支持行内赋值，请逐行设置后再执行：
# $env:BOOTSTRAP_TENANT_CODE="acme"; $env:BOOTSTRAP_ADMIN_EMAIL="admin@acme.com"; python -m agent_platform.bootstrap
```

连续执行两次输出第二次为 `created_tenant=False created_user=False role_assigned=False`，不会产生重复数据。

## 6. 启动后端

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

登录响应里的 `access_token` 用于后续请求：`Authorization: Bearer <token>`。Swagger UI 在 http://localhost:8010/docs 。

可选的辅助进程与自检：

```bash
# backend/ —— 依赖连通性自检，任一不可用退出码非 0
python scripts/check_deps.py

# backend/ —— Outbox 搬运进程，把发布事件投递到 RabbitMQ（另开一个终端）
python -m agent_platform.workers.outbox
```

不启动 Outbox 进程时，发布事件会滞留在 outbox 表里，不影响同步调用链路。

### 日志位置与格式

日志始终写标准输出；未配置 `LOG_DIR` 时不产生任何文件，容器部署直接用 `docker logs` 即可。

本地想留档时在 `backend/.env` 里设置 `LOG_DIR`（相对或绝对路径均可）：

```bash
LOG_DIR=logs
LOG_LEVEL=INFO          # 想看节点流转用 DEBUG
LOG_MAX_BYTES=10485760  # 单文件上限，超出轮转为 *.log.1
LOG_BACKUP_COUNT=5      # 保留的历史文件数
```

API 进程写 `logs/api.log`，Outbox 进程写 `logs/outbox.log`，两者互不交错。每条是单行 JSON，可按字段过滤：

```bash
jq 'select(.event | startswith("llm."))' logs/api.log
jq 'select(.trace_id == "trace-id-from-response")' logs/api.log
```

常用事件：`invocation.start` / `invocation.end`（每次调用首尾，含 status、steps、tokens、latency_ms）、`llm.call`、`llm.upstream_error`、`mcp.call`（建连与调用耗时分开）、`mcp.probe`、`outbox.publish_failed`；`runtime.node` 与 `runtime.route` 只在 `LOG_LEVEL=DEBUG` 时输出。日志不含提示词正文、模型响应正文、工具参数原文与凭据。

## 7. 启动前端

```bash
cd frontend
npm install
npm run dev      # http://localhost:5172 ，/api 代理到 8010
```

后端不在 8010 时可指定代理目标：

```bash
VITE_API_TARGET=http://localhost:8000 npm run dev
# PowerShell: $env:VITE_API_TARGET="http://localhost:8000"; npm run dev
```

控制台入口：Agent 列表 → Prompt 管理（列表 + 详情：模板/变量编辑、渲染预览、版本对比）→ 工具管理（列表 + 详情：契约编辑、版本历史、授权、测试调用、调用记录）→ 版本与快照（含两版本 diff）→ 发布与回滚 → 调试台（同步调用 + 轨迹）。
首屏未登录时展示登录表单，使用第 5 步创建的管理员账号填写租户编码 `default`。

Prompt 页面的手动路径：Prompt 管理 → 新建（编码 / 名称 / 描述）→ 详情页写三条消息（system / user / assistant）并声明两个变量（其一设置默认值）→「保存为新版本」生成 v1 → 变量区填值后「渲染预览」→ 改模板再保存 v2 → 版本对比选 v1 → v2。只有 `prompt:read` 的只读账号看不到新建与保存入口。

工具页面的手动路径：工具管理 → 新建（编码 / 名称 / 类型）→ 详情页填契约（HTTP：方法 / URL / 超时 / 请求头 / 认证；其他类型为整段 JSON）→「保存为新版本」生成 v1 → 授权 → 「测试调用」填 JSON 入参执行（`HIGH` 风险需勾选确认，测试调用不计入调用记录）→ 停用后再次测试被拒 → 重新启用 → 「调用记录」查看窗口内调用量 / 成功率 / 平均耗时。只有 `tool:read` 的只读账号看不到新建、保存、授权、启停与测试入口。

生成后端类型（后端启动后，默认读取 `http://localhost:8010/openapi.json`）：

```bash
npm run gen:api
OPENAPI_URL=http://localhost:8000/openapi.json npm run gen:api   # 后端不在 8010 时
# PowerShell: $env:OPENAPI_URL="http://localhost:8000/openapi.json"; npm run gen:api
```

`src/api/schema.d.ts` 由后端 OpenAPI 生成，`src/api/endpoints.ts` 只使用生成类型；
后端字段改名后 `npm run typecheck` 会直接失败。

## 8. 跑测试

```bash
cd backend
pytest                      # 全量
pytest tests/integration    # 需要 docker 依赖在线
pytest tests/e2e            # 端到端验收：完整主链 / 回滚 / 越权阻断 / 审计追溯
```

集成与端到端测试每个用例使用独立租户并在结束后清理，不会污染 `default` 租户；端到端的模型与工具上游是确定性替身，不需要外部 LLM 凭据。

## 常见问题

- **RLS 查不到数据**：应用连接串用了超级用户。请确认 `DATABASE_URL` 里是 `agent_app`。
- **Redis 连不上**：本机 6379 被占用时 compose 映射到 6380，`REDIS_URL` 需为 `redis://localhost:6380/0`。
- **`alembic upgrade` 报权限不足**：`MIGRATION_DATABASE_URL` 必须是超级用户 `agent`。
- **登录 403 tenant is suspended**：该租户被停用，用 `PATCH /api/v1/tenants/{id}/status` 改回 `ACTIVE`。
