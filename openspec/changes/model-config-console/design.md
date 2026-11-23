# Design

## Context

动机见 `proposal.md`。约束来自现有实现，其中三条直接决定了本次的取舍：

1. **快照冻结是不对称的**。`agent_service._resolve_model` 在生成版本时调用 `normalize_params`，把校验后的 `params`、`model_name`、`ctx_window` 写进不可变快照；`nodes.py` 的 `llm_node` 只从快照取值。但 `default_model_factory` 每次调用都重新按 `model.provider_id` 查供应商并解密凭据，`base_url` 与 `secret_ref` 是活配置。
2. **`timeout` 目前在参数模板里是死字段**。`langchain_adapter._client` 只透传 `_ALLOWED_PARAMS` 中的键，`timeout` 不在其中；真正生效的是 executor 的 `effective_timeout = min(requested, policy.timeout_s)` 与 `settings.sync_max_timeout_s`。
3. **状态字段的校验点很分散**。`model.status` 在装配（`agent_service`）与发布体检（`release_service`）被检查，`provider.status` 只在 `create_model` 被检查；调用路径（`default_model_factory`、`invoke`）对二者都不检查。

另有一个已知偏差不在本次范围内：`catalog/model-registry` 的「基础限流」需求存在，但 `ModelRateLimiter` 全仓无调用点；本变更不触碰它，也不在本次宣称已满足。

## Goals / Non-Goals

**Goals:**

- 让供应商与模型从"只能建"变成"可维护"，并把每个字段的变更影响面写死在接口与界面上。
- 让 `timeout` 从死字段变成真正生效的版本级参数，且不与运行预算打架。
- 让连通性测试能回答"这个模型到底存不存在"，并把每次结论留成可追溯的证据。
- 让"停用"在规格、代码、界面三处语义一致：治理动作，不是停服动作。

**Non-Goals:**

- 不做调用重试、限流、熔断、降级（属网关职责，本次边界外）。
- 不记录每次调用的上游真实模型、响应 id（不碰 `insight.llm_call` 与 `ModelPort`）。
- 不做模型版本化。模型记录仍是"活配置 + 快照冻结"的组合，靠不可变字段约束风险。

## Decisions

### D1 定位：项目内配置台，不是网关

所有"生产级"能力一律不实现，改用界面标注把期待管理住：不可关闭的警告条说明生产应由模型网关托管凭据、路由、限流与审计。

替代方案「本期把补上策略层」被否：一旦开始做重试与熔断，这个页面就在和网关职责摩擦，而且会产生"有重试但轨迹里看不出重试过"的状态——比没有重试更难排查。

代价要认：上游模型漂移的证据只来自管理员手动探测，而不是每次调用。这一点写进标注，正是迁移到网关的理由。

### D2 「记录实际供应商与模型快照」落在配置面

`catalog.model` 记录配置身份（`code` / `model_name` / `provider_id`），新表 `catalog.model_probe` 记录观测身份（上游返回清单、命中项、耗时、结论、时间）。界面并排展示二者，不一致时高亮，不隐藏。

替代方案「`insight.llm_call` 增 `provider_id` / `served_model` / `response_id`」被否：那是网关的可观测职责，需要 insight 迁移 + `LLMCallRecord` 扩展 + 适配器返回结构扩展，与本变更"只做配置台"的定性冲突。等真正接网关时，这些字段应在网关侧产生。

### D3 探测结论六态，而不是二态

```
OK                            凭据有效 + 模型在上游清单中
MODEL_NOT_FOUND               凭据有效 + 清单中没有该模型的 model_name
CREDENTIAL_OK_MODEL_UNVERIFIED 凭据有效 + 上游未实现/未返回 /models
MODEL_PROBE_AUTH_FAILED       401 / 403
MODEL_PROBE_TIMEOUT           请求超时
MODEL_PROBE_UNREACHABLE       连接失败或其它 HTTP 错误
```

第三态必须单独存在：大量 OpenAI 兼容端点不实现 `/models`，把它算作失败会逼着管理员忽略这个功能。结论与应用到界面文案的映射放在 catalog 内，前端不做二次推断。

**探测只做只读 `GET /models`，不做 1-token 真实对话。** 真实对话会产生费用与副作用，而且"能列出来"已经足以回答"配置写对没有"。这条取舍写进设计，避免以后被当成缺陷。

### D4 探测结果存独立表，不存 JSONB

`catalog.model_probe`：`tenant_id`、`provider_id`、`model_id`（供应商级探测为 NULL）、`verdict`、`status_code`、`latency_ms`、`observed_models`（JSONB，截断到合理长度）、`observed_model_name`、`error_code`、`actor_id`、`created_at`。

替代方案「在 `model` 上加 `last_probe` JSONB」被否：只留最新一条无法回答"三个月前上游返回的是什么"，而历史正是"承认底层会变"的证据。

供应商级与模型级共用一张表，用 `model_id IS NULL` 区分。

### D5 `timeout` 进模板并真正接线，用 min 嵌套

```
run 预算  effective_timeout = min(requested, policy.timeout_s) <= sync_max_timeout_s
单次调用  call_timeout      = min(snapshot.model.params.timeout, effective_timeout)
```

实现落点：`default_model_factory` 从快照 `model.params` 读取 `timeout`，与传入的 `timeout_s` 取小后传构造器；不修改 `execute_snapshot`、`nodes.py` 与 `ModelPort` 协议。

`timeout` **不进** `_ALLOWED_PARAMS`：一旦进入，它会同时出现在 `**kwargs` 与显式的 `timeout=` 参数里，直接 `TypeError: got multiple values`。白名单保持只含采样参数，多出来的 `timeout` 键被白名单天然过滤掉，不会外泄给 SDK。

语义分层：`policy.timeout_s` 是"这次运行最多等多久"，模板 `timeout` 是"单次调用最多等多久"，运行级预算始终优先。模板校验上界取 `sync_max_timeout_s`，避免出现模板声明 600 秒、而同步调用上限 120 秒的组合。

时间点选择：`timeout` 随 `normalize_params` 在生成版本时冻结，因此它是版本的一部分，可复现、计入 `config_hash`；改模板不追溯影响已生成版本。

### D6 参数键白名单放在 catalog

`catalog/domain/params.py` 维护权威清单（采样参数 + `timeout`），模板保存时校验键名，不认识的键 422 并指明键名。

替代方案「保持 adapter 静默丢弃」被否：`timeout` 一旦能被配置，管理员自然会尝试配 `reasoning_effort`、`response_format`，而它们会不报错地消失。这是一个"配了却不生效"的整类 bug，值得在入口处掐掉。清单放 catalog 而非 execution，是因为 execution 已依赖 catalog，反向会成环。

### D7 停用语义：配置期阻断，运行期不阻断

`provider.status` 与 `model.status` 统一为：阻断新版本装配与发布体检，**不阻断已发布版本的调用**。实现上补两处校验（`agent_service` 装配链、`release_service` readiness），调用路径不加校验。

这与两份现有规格文本冲突，本次一并修正：

- `catalog/model-registry`「停用供应商」原场景写"停用后其下模型被调用 → 拒绝"，实际代码从未在调用路径检查状态；
- `agent/agent-release`「引用不可用时失败关闭」原文本写"发布与发布后的调用 MUST 失败关闭"，而该结论只对工具成立（工具确实在调用时失败关闭）。

保留工具在调用时失败关闭，是因为工具调用有副作用且授权可撤销；模型停用只是治理动作，阻断运行等于把一次配置整理变成停服操作。

### D8 可改性由"冻结不对称"推导，不逐字段拍脑袋

| 字段 | 可改 | 生效范围 | 依据 |
|---|---|---|---|
| `provider.name` / `adapter` / `region` | 是 | 展示层 | 不参与调用 |
| `provider.status` | 是 | 阻断新绑定与体检 | D7 |
| `provider.base_url` / `secret_ref_id` | 是，二次确认 | 所有已发布版本立即生效 | 调用时活取 |
| `model.code` | 否 | — | 租户内唯一，Agent 引用它 |
| `model.provider_id` | 否（新建替代） | — | 活取，会换掉已发布版本的上游 |
| `model.model_name` | 是 | 只影响新版本 | 快照冻结 |
| `model.ctx_window` / `modalities` / `capabilities` / `price` | 是 | 只影响新版本与校验 | 快照冻结 |
| `model.param_template` | 是 | 只影响新版本（含 `timeout`） | `normalize_params` 结果被冻结 |

`region` 是可空自由文本（长度 ≤ 32），不做枚举：区域对合规与延迟有信息价值，但本平台不据此路由，硬编码区域列表只会限制私有部署。

### D9 前端单入口两分区

一个二级入口「模型配置」，内含供应商 / 模型两个分区，编辑用抽屉而非独立详情页。原因是导航冲突：进行中的 `agent-console` 变更仍有任务改 `router/index.ts` 与 `App.vue`，一个入口把冲突面压到最小，也符合"配置台"而非"两个业务系统"的形态。

提示条做成 `components/common/NoticeBanner.vue`，仅供新页面使用，不回头改造既有页面中重复的 `el-alert` + `.notice` 样式——避免这次变更顺手扩大面。

权限沿用 `MODEL_READ` / `MODEL_MANAGE`，不新增权限点。

## Risks / Trade-offs

- **改 Base URL 会立即影响全部已发布版本** → 接口与界面双重提示 + 二次确认 + 写入 `audit_log`（含 actor、旧值、新值），让事后能回答"谁在什么时候改的"。
- **停用语义从"停服"改为"治理"** → 规格已同步修改，需在发布说明中显式声明这是行为变更，避免运维按旧直觉把停用当停机开关使用。
- **参数键白名单会让存量模板在下次保存时被拒** → 迁移前先扫描 `catalog.model.param_template` 中不在清单内的键并输出清单，由管理员逐个清理；不在迁移中自动删除任何参数，因为静默删配置比拒绝保存更危险。
- **存量 `param_template` 已含 `timeout` 的模型，行为会改变**（此前被忽略，此后生效） → 迁移扫描同时输出这些模型，提醒复核超时值是否合理；默认值偏大时不会造成危害（受运行预算兜底）。
- **`model_probe` 表随探测频率增长** → 记录截断 `observed_models` 长度；保留策略见开放问题。
- **"模型不存在"结论依赖上游实现 `/models`** → 第三态 `CREDENTIAL_OK_MODEL_UNVERIFIED` 显式表达该限制，避免把未实现误判为失败。
- **不记录每次调用的上游模型** → 上游漂移只能在手动探测时被发现；这是本期边界，靠标注与后续网关迁移弥补。
- **与 `agent-console` 在路由与导航文件重叠** → 本变更排在其任务 8.7 之后，或合并处理导航入口。

## Migration Plan

1. 新增 1 个迁移：`catalog.model_provider` 加可空 `region`（无回填），创建 `catalog.model_probe` 表与索引（`tenant_id` + `created_at`、`tenant_id` + `model_id`）。
2. 迁移前扫描并输出两份清单：含未知参数键的模型、已含 `timeout` 的模型。不自动改数据。
3. 发布顺序：后端（字段 + 接口 + 校验 + 探测 + timeout 接线）→ 前端（页面 + 标注）。后端先上不影响既有前端，`region` 可空且新接口为增量。
4. 回滚：删表与删列即可，无存量数据依赖；`timeout` 接线回滚表现为"模板值不再生效"，回到当前行为，不会产生不可恢复状态。
5. 行为变更公告：停用语义与 `timeout` 生效两项需在发布说明中单独列出。

## Open Questions

- `model_probe` 的保留策略（保留最近 N 条，还是按天数清理）留到实现时按实际探测频率决定；无论哪种选择，都不改变接口与界面契约，只需在列表接口上加截断。

