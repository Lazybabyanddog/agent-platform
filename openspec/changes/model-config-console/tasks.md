# Tasks

## 1. 数据模型与迁移

- [x] 1.1 在 `catalog/infrastructure/tables.py` 为 `ModelProvider` 增加可空 `region`（长度 32），新增 `ModelProbe` 表（`tenant_id` / `provider_id` / `model_id` 可空 / `verdict` / `status_code` / `latency_ms` / `observed_models` / `observed_model_name` / `error_code` / `actor_id` / `created_at`）与两个索引，新增迁移 `0009_model_config_console`；验证：`alembic upgrade head` 与 `alembic downgrade -1` 均成功
- [x] 1.2 在 `catalog/domain/enums.py` 增加 `ProbeVerdict` 六态，并把 `model_service` 现有字符串常量映射到枚举；验证：单测断言六个取值与既有常量一一对应，且序列化到响应为稳定字符串
- [x] 1.3 编写存量扫描脚本（`backend/scripts/`）输出两份清单：参数模板含未受支持键的模型、参数模板已含 `timeout` 的模型；验证：脚本在开发库上运行成功并打印两份清单，不修改任何数据

## 2. 供应商维护能力

- [x] 2.1 增加 `ProviderUpdateRequest`（`name` / `base_url` / `adapter` / `region` / `secret_id` / `status`，全部可选）与 `PATCH /providers/{id}`，权限 `MODEL_MANAGE`，`base_url` 保持 `http(s)://` 校验、`secret_id` 仍走 `secret_service.resolve` 校验可解析；验证：集成测试断言字段更新成功，且对已停用供应商下新建模型仍返回不可用
- [x] 2.2 `ProviderResponse` 增加 `region` 与最近一次探测摘要（结论、时间、命中的上游模型标识）；验证：schema 单测覆盖 `region` 为空与有值两种情况
- [x] 2.3 供应商变更写入 `audit_log`（action `provider.update`，detail 含变更字段的旧值与新值，凭据字段只记标识不记内容）；验证：集成测试断言审计记录存在且不含凭据明文

## 3. 模型维护能力

- [x] 3.1 增加 `ModelUpdateRequest` 与 `PATCH /models/{id}`，允许 `model_name` / `ctx_window` / `modalities` / `capabilities` / `param_template` / `price`，`code` 与 `provider_id` 出现在请求中即拒绝并指明字段；验证：集成测试覆盖成功更新与两条不可变字段拒绝用例
- [x] 3.2 模型变更写入 `audit_log`（action `model.update`，detail 含变更字段新旧值，`param_template` 只记键名与变化摘要）；验证：集成测试断言审计记录存在
- [x] 3.3 在 `model_service` 的接口文档字符串中写明"修改 `model_name` 只影响之后生成的 Agent 版本，`base_url` 与凭据在供应商上且立即生效"；验证：代码审查确认说明与 `design.md` D8 表一致

## 4. 参数模板与超时接线

- [x] 4.1 在 `catalog/domain/params.py` 增加权威参数键清单 `SUPPORTED_MODEL_PARAMS`（采样参数 + `timeout`），`validate_template` 拒绝未受支持的键并返回键名，`timeout` 校验为 `0 < x <= settings.sync_max_timeout_s`；验证：单测覆盖未知键、超时越界、超时合法三种情况
- [x] 4.2 修改 `execution/application/invocation_service.py` 的 `default_model_factory`，从快照 `model.params` 读取 `timeout` 并与传入 `timeout_s` 取小后作为构造器超时；验证：单测断言（模板 5 秒 + 预算 30 秒 → 5 秒，模板 60 秒 + 预算 8 秒 → 8 秒，无模板值 → 预算值）
- [x] 4.3 断言 `timeout` 不会进入适配器 `**kwargs`（`_ALLOWED_PARAMS` 保持不含它）；验证：单测对 `LangChainModelAdapter._client` 传入含 `timeout` 的参数不抛 `TypeError`，且构造的客户端超时等于期望值
- [x] 4.4 回归既有模板校验用例，确认加入白名单后没有误伤 `price`、`param_template` 等相邻逻辑；验证：`python -m pytest tests/unit -q` 通过

## 5. 连通性测试

- [x] 5.1 改写 `model_service.probe` 为六态判定并提取 `observed_models`（截断长度）与 `observed_model_name`；验证：`httpx.MockTransport` 单测覆盖命中、清单不含、无清单、401、超时、连接失败六种响应
- [x] 5.2 探测结果写入 `catalog.model_probe`，并写审计（action `model.probe`，detail 含结论与耗时）；验证：集成测试断言落库字段与审计记录，且审计不含凭据明文
- [x] 5.3 增加 `POST /providers/{id}/probe`（供应商级，`model_id` 为空）与 `GET /providers/{id}/probes`、`GET /models/{id}/probes`（时间倒序、支持 limit）；验证：集成测试覆盖供应商级探测与两个历史接口的排序与条数
- [x] 5.4 确认探测响应结构不含凭据原文（含错误分支）；验证：单测遍历六种结论的响应体，断言不出现密钥字符串

## 6. 停用语义统一

- [x] 6.1 在 `agent_service._resolve_model` 反查供应商并校验状态，停用时返回可定位到 `model_id` 的不可用原因；验证：单测断言供应商停用时生成版本被拒且原因含供应商
- [x] 6.2 在 `release_service` 的发布体检中对模型绑定增加供应商状态校验；验证：单测断言体检结果标记该绑定不可用并阻断发布
- [x] 6.3 回归调用路径：模型或供应商停用后，已发布版本的调用仍成功且不返回 `RESOURCE_UNAVAILABLE`；验证：集成测试用假模型工厂跑一次 invoke，断言成功
- [x] 6.4 复核工具停用仍在调用时失败关闭，未被本次改动影响；验证：既有工具停用集成测试通过

## 7. 控制台

- [x] 7.1 新建 `frontend/src/components/common/NoticeBanner.vue`（warning 样式、不可关闭、支持标题与正文插槽），供两个分区复用；验证：`npm run typecheck` 通过，且组内不引入对既有页面的改造
- [x] 7.2 重新生成 `schema.d.ts` 并在 `api/endpoints.ts` 增加供应商与模型的列表/创建/更新、供应商与模型探测、探测历史封装；验证：`npm run gen:api && npm run typecheck` 通过
- [x] 7.3 新建 `views/ModelConfigView.vue`：供应商与模型两个分区、按名称或编码本地过滤、空状态、`MODEL_MANAGE` 缺失时隐藏写操作入口；验证：只读账号访问时无写入口且无报错（手测 + `npm run build` 通过）
- [x] 7.4 新建 `ProviderForm`：Base URL 与凭据变更走二次确认并展示"影响全部已发布版本"，停用展示"只阻止新绑定与发布"；验证：手测确认两类变更的确认流程与文案
- [x] 7.5 新建 `ModelForm`：`code` 与所属供应商只读并说明"需更换供应商时新建模型"，`model_name` 变更提示"只影响之后生成的版本"；验证：手测编辑既有模型时两项不可输入
- [x] 7.6 新建 `ParamTemplateEditor`：按行编辑类型与范围、内置 `timeout` 项与"取模板值与运行预算较小者"说明、未受支持键前端提示、后端错误定位到具体参数行；验证：手测提交未知键与越界超时，错误落在对应行
- [x] 7.7 新建 `ConnectivityPanel`：六态文案、配置标识与上游命中标识并排且在差异时高亮、历史展开按时间倒序；验证：用手工构造的探测数据（或 mock 响应）确认三种关键态展示正确
- [x] 7.8 增加路由与一个导航入口，排在 `agent-console` 任务 8.7 之后或与其合并处理；验证：`npm run build` 通过，导航项在高亮与刷新后状态正确

## 8. 集成校验

- [x] 8.1 端到端串一遍主路径：建供应商 → 建模型（含 `timeout`）→ 探测（mock 上游）→ 生成 Agent 版本 → 发布 → 调用，确认生效超时为模板值与预算的较小者；验证：e2e 测试或手工脚本记录实际耗时与日志
- [x] 8.2 全量回归：`python -m pytest tests/unit tests/integration -q` 与 `ruff check`、`mypy` 通过
- [x] 8.3 确认本次未触碰 `insight`、`execute_snapshot`、`nodes.py`、`ModelPort` 与限流相关代码；验证：`git diff --stat` 逐项核对无越界改动

## Workflow follow-up

- 在发布说明中单独列出两项行为变更：停用语义由"停服"改为"治理"，参数模板中的 `timeout` 由被忽略改为生效。
- 归档变更，并确认归档后的主规格包含本次三份增量。
