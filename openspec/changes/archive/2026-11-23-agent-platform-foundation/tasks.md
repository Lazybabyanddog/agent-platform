# Tasks

## 1. 工程骨架与运行环境

- [x] 1.1 创建 `backend/pyproject.toml` 与目录骨架（`shared` + 五个 contexts + `api/v1`），用 `conda create -n agent-platform python=3.12` 建环境并 `pip install -e ".[dev]"`，验证 `conda run -n agent-platform python -c "import fastapi, sqlalchemy, langgraph, aio_pika"` 无报错
- [x] 1.2 实现 `settings.py`（pydantic-settings）与 `.env.example`，验证配置缺失时启动报错信息指出具体变量名，单测覆盖默认值与环境变量覆盖
- [x] 1.3 实现 `main.py` 的 `create_app()`：structlog 日志、trace_id 中间件、异常处理器与统一错误响应体，验证 `pytest tests/unit/test_app_errors.py` 通过且 `/healthz` 返回 200
- [x] 1.4 编写 `deploy/docker-compose.dev.yml`（PostgreSQL 16 / Redis 7 / RabbitMQ 3.13 + management），验证 `docker compose -f deploy/docker-compose.dev.yml up -d` 后 `scripts/check_deps.py` 能连通三者
- [x] 1.5 实现 `shared/ids.py`（UUIDv7）与 `shared/db.py`（async engine、session、`SET LOCAL app.tenant_id` 钩子），验证 `pytest tests/unit/test_ids.py tests/unit/test_rls_hook.py` 通过且无租户上下文时查询返回空集
- [x] 1.6 搭建 `frontend/`（Vue 3 + Vite + TS + Pinia + Element Plus + 代理 `/api` 到 8000），验证 `npm run build` 成功且 `npm run dev` 首页可访问

## 2. platform：租户、身份、授权、密钥与审计

- [x] 2.1 建 `platform` schema 与 `tenant`、`user_account`、`membership`、`role`、`permission`、`role_permission`、`user_role`、`resource_grant` 表及迁移，验证 `alembic upgrade head` 后 `alembic downgrade -1` 可回退
- [x] 2.2 实现租户创建/停用与成员管理用例，验证停用租户的请求返回拒绝且不执行后续逻辑（`tests/integration/test_tenant_lifecycle.py`）
- [x] 2.3 实现邮箱密码注册登录（Argon2id + JWT）与 `AuthPort`，验证错误密码、过期 token、停用用户三种情形均被拒绝
- [x] 2.4 实现 API Key 创建/校验/禁用/作用域校验，验证明文仅创建响应出现一次、禁用后立即 401、作用域外调用 403
- [x] 2.5 实现 `AuthorizationService`（RBAC 权限点 + ResourceGrant 双判定）与 `TenantContext` 注入，验证"有操作权限但无资源授权"被拒、"非成员"读不到资源（`tests/integration/test_authorization.py`）
- [x] 2.6 实现 `secret_ref` 与 AES-GCM 加解密、轮换、禁用，验证接口与日志均不出现明文（`tests/unit/test_secret_no_leak.py`）
- [x] 2.7 实现 `audit_log` 只追加写入与查询接口，验证更新/删除路径不存在、失败操作也留痕、跨租户查询返回空集
- [x] 2.8 实现可重复执行的 `python -m agent_platform.bootstrap`（平台管理员、内置角色、首个租户），验证连续执行两次不产生重复数据，并在 `docs/dev-setup.md` 记录照抄可用的启动步骤

## 3. catalog：模型、Prompt 与工具

- [x] 3.1 建 `catalog` schema 与 `model_provider`、`model` 表及迁移，实现供应商与模型 CRUD，验证逻辑编码与 `model_name` 分开存储、同租户编码冲突返回 409
- [x] 3.2 实现参数模板与合法范围校验，验证越界参数返回可定位字段的 422（`tests/unit/test_model_params.py`）
- [x] 3.3 实现模型连通性测试（OpenAI 兼容 `GET /models` 探测），验证成功/认证失败/超时三态，且响应与日志不含凭据原文
- [x] 3.4 实现 Redis 租户级限流（RPM + 并发），验证超限返回 429 且区分于模型错误，窗口过后恢复（`tests/integration/test_model_rate_limit.py`）
- [x] 3.5 建 `prompt`、`prompt_version` 表并实现模板存储、变量 Schema、渲染引擎，验证缺失必填变量与类型错误返回可定位错误且不产出部分渲染
- [x] 3.6 实现 Prompt 版本生成与对比接口，验证同版本号并发只有一个成功、旧版本内容不可变、对比返回逐条差异
- [x] 3.7 建 `tool`、`tool_version`、`tool_grant` 表并实现工具注册、JSON Schema 入出参校验、风险等级、租户授权，验证非法参数不发起外部请求（用本地 stub server 断言未收到调用）
- [x] 3.8 实现工具 HTTP 执行器（超时、认证注入、输出契约校验），验证超时被终止并记录、输出不符契约标记失败（`tests/integration/test_tool_executor.py`）

## 4. agent：版本快照与发布指针

- [x] 4.1 建 `agent` schema 与 `agent`、`agent_version`、`agent_version_binding`、`agent_release`、`agent_release_pointer` 表及迁移，验证指针表主键为 `(tenant_id, agent_id, env)`
- [x] 4.2 实现快照构建（解析模型/Prompt/工具的确切版本）与 `config_hash` 规范化计算，验证同配置两次生成 hash 相同、上游资源生成新版本后旧快照与 hash 不变
- [x] 4.3 实现"生成版本"用例在同一事务内写 `snapshot` 与 `binding`，验证人为中断绑定写入时事务整体回滚、快照不可被后续更新语句修改
- [x] 4.4 实现发布接口与指针更新，验证同一 Agent 同环境仅有最新指针可见、历史发布记录保留且不可修改（`tests/integration/test_release_pointer.py`）
- [x] 4.5 实现回滚接口，验证指针切回历史发布、历史记录数量不变、切换时进行中的 run 仍使用原版本
- [x] 4.6 实现发布前引用可用性校验（模型/Prompt/工具停用或授权撤销即拒绝），验证返回具体不可用资源标识
- [x] 4.7 实现 `outbox_event` 写入与 publisher（`FOR UPDATE SKIP LOCKED` + aio-pika + 退避重试），验证发布后事件出现在 RabbitMQ、失败时 `attempts` 增加且不丢事件（`tests/integration/test_outbox.py`）
- [x] 4.8 实现 Agent 版本对比接口，验证返回模型/Prompt/工具版本绑定与策略参数差异

## 5. execution：同步调用与运行时

- [x] 5.1 建 `execution` schema 与 `run`、`execution_event`、`checkpoint_ref` 表及迁移，验证 `run` 记录 `release_id`、`version_id`、`trace_id` 均非空
- [x] 5.2 定义 `ModelPort` 并实现 langchain-openai adapter（含超时与错误分类），验证用 mock 服务端覆盖成功/限流/超时三条路径（`tests/integration/test_model_port.py`）
- [x] 5.3 定义 `ToolPort` 并接入工具执行器，验证参数校验在外部请求之前执行、服务端二次授权生效
- [x] 5.4 实现 `runtime/builder.py`（`AgentSnapshot` → `StateGraph`）与 `nodes.py`（渲染、LLM、工具节点 + 预算装饰器），验证步骤数、Token 预算、总超时三种上限均能终止执行（`tests/unit/test_runtime_budget.py`）
- [x] 5.5 实现 `invocation_service` 受理链路（解析指针 → 校验引用 → 校验入参 → 建 run → 执行 → 落结果），验证任一环节失败的 `run.error.code` 属于约定分类
- [x] 5.6 实现 `POST /api/v1/agents/{id}/invoke` 路由，验证成功返回结果与 `run_id`、`trace_id`，无发布返回 404、无权限返回 403 且不建 run、超同步阈值返回提示异步的错误码
- [x] 5.7 接入 LangGraph Postgres checkpointer 并写 `checkpoint_ref`，验证一次执行产生可定位的 checkpoint 引用且不复制内部结构
- [x] 5.8 实现执行期间并发发布的隔离，验证运行时新发布新版本，进行中的 run 仍按原快照完成（`tests/integration/test_execution_isolation.py`）

## 6. insight：执行轨迹与指标

- [x] 6.1 建 `insight` schema 与 `llm_call`、`tool_call` 表，实现调用记录写入，验证成功与失败调用均落库、同一 `trace_id` 贯穿模型与工具调用
- [x] 6.2 实现 `GET /api/v1/runs/{id}/trace` 查询接口，验证返回按时间排序的调用条目并可识别最耗时步骤
- [x] 6.3 实现按租户与 Agent 的指标聚合接口（调用量、成功率、失败率、P50/P95 延迟），验证与 `run` 表数据一致（`tests/integration/test_metrics.py`）
- [x] 6.4 实现脱敏与正文留存开关，验证默认只存摘要与长度、敏感字段掩码、未授权用户看不到正文

## 7. 前端四屏

- [x] 7.1 用 `npm run gen:api` 生成类型、实现 `client.ts`（Bearer 注入 + 错误码映射 + trace 显示），验证后端改字段后类型检查失败即报错
- [x] 7.2 实现 Agent 列表与版本快照页（含两版本对比），手动验证能选中版本看到快照内容与差异
- [x] 7.3 实现发布/回滚页，手动验证发布后各环境指针变化、回滚后指针回到历史发布
- [x] 7.4 实现调试台（输入 → 结果 → 调用轨迹），手动验证一次成功调用与一次失败调用都能看到 `trace_id` 与失败原因

## 8. 端到端验收

- [x] 8.1 跑通完整链路：建租户 → 注册模型/Prompt/工具 → 建 Agent → 生成版本 → 发布 PROD → 同步调用成功 → 查到 trace（`tests/e2e/test_foundation_flow.py`）
- [x] 8.2 验证回滚后新的调用使用旧版本，且历史 run 仍指向其原始 release，不被改写
- [x] 8.3 验证跨租户读取、跨租户调用、未授权工具调用、失效 API Key 四类越权均被阻断
- [x] 8.4 验证审计记录覆盖发布、回滚、授权变更、API Key 变更，且可按 Agent 追溯
- [x] 8.5 校验 `README.md` 中的启动与验收步骤可在干净机器上照抄执行成功

## Workflow follow-up

- 变更通过评审后归档，并确认 `openspec/specs/` 出现 8 个能力的主规格。
- 归档后由 `async-task-worker` 承接异步执行链路，复用本变更的 `outbox_event` 与事件信封契约。
