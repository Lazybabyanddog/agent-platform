# Proposal

## Why

公司各业务线各自接入模型、拼装 Prompt、直调工具，导致能力重复建设、Agent 配置无版本、线上行为不可追溯、跨租户边界无法统一治理。仓库当前只有需求文档 `docs/enterprise_agent_platform_12_modules_prd.html`，没有任何可运行代码。

本变更落地 PRD 第 9 章的"第一期最小闭环"中**同步调用这一条主干**：把"配置 → 不可变版本 → 环境发布 → 按发布执行 → 全链路可追溯"跑通，使后续知识库、异步任务、编排、记忆、评估模块都挂在这根已验收的主干上，而不是各自发明版本与执行语义。

## What Changes

- 建立 `backend/`（Python 3.12 + FastAPI + SQLAlchemy 2.0 async + Alembic）与 `frontend/`（Vue 3 + Vite + TypeScript）双工程骨架，以及 `agent-platform` conda 环境的依赖清单。
- 新建多租户身份与授权基础：租户、成员、角色权限、API Key、`TenantContext` 解析，以及作为纵深防御的 PostgreSQL RLS。
- 新建密钥引用与审计能力：第三方凭据只存引用/密文；发布、授权、密钥变更等关键动作留痕。
- 新建模型注册能力：OpenAI 兼容供应商接入、模型注册、参数模板、凭据引用、连通性测试、基础限流。
- 新建 Prompt 注册能力：多角色消息模板、变量 Schema、渲染校验、不可变版本。
- 新建工具注册能力：HTTP 工具、JSON Schema 入出参、工具版本、凭据引用、风险等级、租户授权。
- 新建 Agent 生命周期能力：Agent 逻辑身份、不可变 `AgentVersion` 配置快照（含 `config_hash`）、资源版本绑定、`Release` 与环境发布指针、回滚；发布时通过 Outbox 至少一次投递 `agent.release.published` 事件到 RabbitMQ。
- 新建同步调用能力：`POST /api/v1/agents/{id}/invoke` 解析发布指针 → 固定 Release/Version → Runtime 装载快照 → 经 `ModelPort` / `ToolPort` 执行 → 受步骤数、超时、Token 预算约束 → 返回结果。
- 新建执行可观测能力：`trace_id` 串联 Run、LLM 调用、工具调用，落 `llm_call` / `tool_call` 记录，对外可查。
- 交付四屏控制台：Agent 列表、版本快照只读视图（含版本对比）、发布/回滚、调试台（输入 → 输出 → 调用轨迹）。

**明确非目标（本变更为止）**

- **知识库 / RAG 整体移出本平台**，由独立知识中台承担；本平台不建设知识库，也不保留检索端口占位。知识能力后续通过 MCP 工具接入知识中台（见变更 `mcp-tool-integration`），本平台不引入 Milvus、MinIO、文档解析与切片。
- 异步任务执行链路（Task 队列消费、Worker 进程、租约心跳、重试退避、死信、取消/恢复）属下一变更 `async-task-worker`；本变更只交付 Outbox + 发布侧，不做消费侧。
- 可视化编排画布、Multi-Agent、记忆中心、评估中心、GraphRAG、告警与运营报表均不在本期。
- 不对接公司 SSO：本期自建账号 + API Key，认证经 `AuthPort` 隔离，后续替换为 OIDC adapter。

## Capabilities

### New Capabilities

- `identity/tenant-access`: 租户生命周期、成员归属、角色权限、API Key、请求身份解析与租户隔离强制执行。
- `platform/audit-and-secrets`: 密钥引用管理与关键操作审计留痕。
- `catalog/model-registry`: 模型供应商与模型注册、参数模板、凭据引用、连通性测试、基础限流。
- `catalog/prompt-registry`: Prompt 模板、变量 Schema、渲染校验、不可变版本与版本对比。
- `catalog/tool-registry`: 工具注册、JSON Schema 契约、工具版本、凭据引用、风险等级与租户授权。
- `agent/agent-release`: Agent 逻辑身份、不可变版本快照与资源版本绑定、环境发布指针、回滚、发布事件投递。
- `execution/agent-invocation`: 同步调用入口、按 Release 装载执行、模型与工具端口调用、执行预算与失败关闭。
- `insight/execution-trace`: 执行轨迹采集与查询，`trace_id` 贯穿一次调用的模型与工具调用。

### Modified Capabilities

无。项目当前 `openspec/specs/` 为空，本次全部为新增能力。

## Impact

- **代码**：全新仓库结构 `backend/src/agent_platform/{shared,contexts,api}`、`frontend/src`、`deploy/`；无既有代码需要改动。
- **API**：新增 `/api/v1/tenants`、`/auth`、`/api-keys`、`/models`、`/prompts`、`/tools`、`/agents`（含 `/versions`、`/releases`、`/invoke`）、`/runs/{id}/trace`。引入统一请求上下文（tenant_id、user_id、release_id、run_id、trace_id、request_id）。
- **数据**：新增 PostgreSQL schema（platform / catalog / agent / execution / insight 五组表）与首版 Alembic 迁移；启用 RLS 策略。
- **依赖与中间件**：PostgreSQL（事实源）、Redis（缓存、限流计数、短期锁）、RabbitMQ（服务间事件通信，本期仅发布侧）。不引入 Milvus、MinIO。
- **运行环境**：conda 环境 `agent-platform`（Python 3.12），前端 Node 20；`deploy/docker-compose.dev.yml` 提供本地 PostgreSQL / Redis / RabbitMQ。
- **对既有系统**：不替代公司 IAM/SSO，不接管业务系统数据；业务系统通过 API Key + 同步接口接入。
- **风险**：`AgentVersion.snapshot` 的结构一旦上线即为跨版本契约，需在首版冻结字段语义；同步调用受 HTTP 生命周期限制，长耗时场景需在下一变更由异步链路承接。
