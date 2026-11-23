# Proposal

## Why

模型配置目前只能靠直接调用接口完成：供应商建好之后没法改 Base URL、没有区域字段、状态改不了；模型没有编辑入口；参数模板里写了 `timeout` 也会被适配器静默丢弃；连通性测试只验证凭据能不能过 `GET /models`，无法确认要用的那个模型是否存在，而且结果不留痕。管理员维护模型清单只能靠 curl 加记忆，配错了要等第一次调用失败才发现。

这个变更补上一个"够用"的内部配置台，同时把它的定位写清楚：它只是本项目里方便配模型的地方，生产环境里模型应由模型网关统一托管，不在这里实现网关职责。

## What Changes

- **供应商可变更**：`catalog.model_provider` 新增 `region`（可选，仅用于展示与筛选，不参与路由）；新增 `PATCH /providers/{id}` 支持改名称、Base URL、区域、凭据引用与状态。改 Base URL 或凭据会立即影响所有已发布版本（调用时活取），接口返回与界面必须给出这一提示。
- **模型可变更**：新增 `PATCH /models/{id}`，允许改 `model_name`、`ctx_window`、`modalities`、`capabilities`、`param_template`、`price`；`code` 与 `provider_id` 不可改（前者被 Agent 版本引用，后者会换掉已发布版本的上游供应商），需要时新建模型替代。
- **`timeout` 真正生效**：参数模板中的 `timeout`（秒）在保存 Agent 版本时随其它参数一起冻结进快照，调用时生效值为 `min(模板值, 运行预算)`；模板校验上界为 `settings.sync_max_timeout_s`。
- **参数键白名单化**：模型参数模板只接受受支持的键（采样参数 + `timeout`），不认识的键在保存时即被拒绝，替代当前"配了但不生效"的静默丢弃。
- **连通性测试升级**：区分 `OK`、`MODEL_NOT_FOUND`、`CREDENTIAL_OK_MODEL_UNVERIFIED`、凭据无效、超时、不可达六类结论；记录上游 `/models` 返回的模型清单与命中项、耗时、状态码；结果持久化并可查看历史；响应与界面均不出现凭据原文。
- **停用语义统一（BEHAVIOR CHANGE）**：供应商停用与模型停用都只阻断新绑定与发布体检，**已发布版本继续调用**。这与现有规格「停用后调用被拒绝」相反，现有实现也从未在调用路径检查状态。停用因此是治理动作，不是停服动作。
- **控制台**：新增「模型配置」单入口（供应商 / 模型 两个 tab）、供应商与模型表单、参数模板编辑器（含 `timeout` 及其生效方式说明）、连通性测试面板（并排展示"配置标识 vs 上游实际返回"）。
- **定位标注**：供应商页与模型页各一条不可关闭的提示——本模块仅用于本项目内的模型配置与连通性验证，生产环境应由模型网关统一管理凭据、路由、限流与审计。

明确不做（本期边界）：调用重试；限流、熔断、降级；`insight.llm_call` 的字段扩展与按调用记录上游快照；执行图（`execute_snapshot`、`nodes.py`、`ModelPort` 协议）的任何改动。

## Capabilities

### New Capabilities

- `console/model-configuration`: 模型配置控制台——供应商与模型的列表、创建、编辑、启停、参数模板编辑、连通性测试与"非网关"定位标注，含权限降级与只读访问行为。

### Modified Capabilities

- `catalog/model-registry`: 供应商接入（新增区域、变更接口、停用语义）；模型注册与标识分离（新增变更规则与不可变字段）；参数模板与取值约束（`timeout` 生效、参数键白名单）；连通性测试（六类结论、观察结果留痕、历史可查）。
- `agent/agent-release`: 「引用不可用时失败关闭」按资源类型区分——工具停用仍在调用时失败关闭，模型或供应商停用只阻断新绑定与发布，已发布版本继续调用。

## Impact

- **后端 catalog**：`infrastructure/tables.py`（`ModelProvider.region`、新表 `model_probe`）、`domain/enums.py`（探测结论枚举）、`domain/params.py`（参数键白名单与 `timeout` 校验）、`api/schemas.py`、`api/routes.py`（供应商 PATCH、模型 PATCH、供应商级探测）、`application/model_service.py`（变更用例、探测改写、审计）。
- **后端 agent**：`application/agent_service.py`（装配时校验供应商状态）、`application/release_service.py`（发布体检校验供应商状态）。
- **后端 execution**：`application/invocation_service.py` 的 `default_model_factory` 读取模板 `timeout` 并与运行预算取小；不触碰 runtime 与端口协议。
- **审计**：探测与配置变更写入 `platform` 上下文既有 `audit_log`（复用 `AuditService.record`），不新增审计设施。
- **数据库**：1 个迁移（`region` 列 + `model_probe` 表）。
- **前端**：`api/endpoints.ts`（供应商/模型读写与探测）、`router/index.ts` 与 `App.vue`（新增一个导航入口）、新视图与组件（含共用提示条）。与进行中的 `agent-console` 变更在路由与导航文件上重叠，需排在其收尾之后或合并处理。
- **不做的事同样影响面**：`insight` 与执行图不动，所以"上游实际返回模型"的证据只来自管理员手动探测，不来自每次调用；这一限制写进定位标注，作为迁移到网关时的理由。
