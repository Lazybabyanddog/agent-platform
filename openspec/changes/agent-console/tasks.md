# Tasks

## 1. 数据模型与迁移

- [x] 1.1 在 `agent/domain/enums.py` 增加 `AgentStatus.INACTIVE`，在 `execution/domain/enums.py` 增加 `RunKind`（`INVOKE` / `PREVIEW`）与 `SessionStatus`（`IDLE` / `BUSY`），并同步 ORM 列定义；验证：`cd backend && alembic upgrade head` 与应用导入均无错误。
- [x] 1.2 新增 `execution_session` 表定义（唯一键 `tenant_id+agent_id+env+session_key`、`kind`、绑定的发布与版本、`preview_snapshot`、`status`、`busy_since`、`last_active_at`、`expires_at`）；验证：`alembic revision --autogenerate` 生成预期列，单测断言唯一键与索引存在。
- [x] 1.3 扩展 `execution.run`：新增 `kind`（非空默认 `INVOKE`）、`execution_session_id`、`preview_snapshot`、`config_hash`，把 `agent_release_id` 与 `agent_version_id` 改为可空，并加 `CHECK` 约束区分两类运行；验证：迁移可 `upgrade` 与 `downgrade` 往返，约束单测通过。
- [x] 1.4 编写数据回填迁移：`run.kind='INVOKE'`；对存在任一发布记录的 Agent 置为 `ACTIVE`；验证：在含已发布 `DRAFT` 数据的测试库上执行后计数符合预期，且重复执行幂等。

## 2. 装配管线与版本写入

- [x] 2.1 从 `AgentService.create_version` 抽出 `assemble_snapshot(actor, refs)`，返回快照、绑定列表、`config_hash` 与不可用引用明细，`create_version` 改为调用它并持久化；验证：既有 `backend/tests/unit` 与 `integration` 全部通过。
- [x] 2.2 创建 Agent 支持可选初始配置，在同一事务内装配并写入版本 1，任一引用不可用时整体回滚；验证：集成测试覆盖「生成 v1」与「不可用引用不留下 Agent」两条路径。
- [x] 2.3 `POST /agents/{id}/versions` 接受可选 `base_version_id`，非最新基准时返回可识别的冲突标记与差异摘要；验证：并发场景集成测试断言后提交被标记且不静默覆盖。
- [x] 2.4 新增 `POST /agents/{id}/copy`：新编码必填，复制最新快照为版本 1 并重建绑定，不复制发布与运行；验证：集成测试断言 `config_hash` 与来源一致、三环境未发布、编码冲突被拒。
- [x] 2.5 在 README 与 OpenAPI 描述中补充创建带初始配置、`base_version_id`、复制接口；验证：按 README 记录的请求样例可在本地跑通。

## 3. 生命周期与状态机

- [x] 3.1 实现状态转移规则：首次发布成功 `DRAFT → ACTIVE`，`ACTIVE ⇄ INACTIVE`，任意非删除路径可 `→ ARCHIVED`，拒绝非法转移；验证：状态转移矩阵单测覆盖全部合法与非法组合。
- [x] 3.2 在 `InvocationService._resolve_release` 前置校验状态，`INACTIVE` 与 `ARCHIVED` 失败关闭且错误码可区分；验证：集成测试断言停用后调用失败、发布指针未变。
- [x] 3.3 实现停用期间发布不自动启用、重新启用恢复调用；验证：集成测试覆盖「停用 → 发布新版本 → 仍失败 → 启用 → 成功」。
- [x] 3.4 实现归档与删除约束：删除仅限无版本、无发布、无运行的 Agent，归档保留编码；验证：集成测试覆盖删除成功释放编码、有历史被拒并提示归档。
- [x] 3.5 为启停、归档、删除、复制记录审计事件；验证：测试断言每次操作产生一条带资源标识的审计记录。

## 4. 列表、详情与发布体检

- [x] 4.1 `GET /agents` 改为服务端分页与筛选（`status`、`type`、`tag`、`q`、`page`、`page_size`），默认按更新时间倒序；验证：集成测试断言筛选与分页边界（空结果、最后一页、非法页码）。
- [x] 4.2 列表与详情返回三环境当前生效版本与发布时间，未发布环境显式标注；验证：集成测试断言一次列表请求即可取得三个环境信息，且不产生逐行查询。
- [x] 4.3 新增 `GET /agents/{id}/versions/{version_id}/readiness`，返回每个绑定的可用性与最近同 `config_hash` 预览摘要；验证：集成测试覆盖可用、撤销授权、无预览记录三种情况。
- [x] 4.4 确认发布接口仍独立复检并失败关闭，体检只读；验证：测试断言多次调用体检不新增发布记录与指针变更。

## 5. 预览执行

- [x] 5.1 新增 `POST /agents/{id}/preview`，只接受引用、输入与可选 `preview_session_id`，服务端调用 `assemble_snapshot` 后执行，创建 `kind=PREVIEW` 的运行并落 `preview_snapshot` 与 `config_hash`；验证：集成测试断言不新增版本、不移动指针、运行记录带种类与摘要。
- [x] 5.2 预览复用引用复检、入参校验、工具二次授权与预算上限；验证：集成测试覆盖未授权工具被拒、超步骤上限终止、入参缺失返回字段级错误。
- [x] 5.3 保证同一组引用经保存与预览得到相同 `config_hash`；验证：契约测试对同引用分别调用保存与预览并比较摘要。
- [x] 5.4 预览入口权限为 `AGENT_INVOKE`，查看调试结果与轨迹为 `AGENT_READ`；验证：权限测试断言缺失权限时分别返回 403 且不产生运行记录。

## 6. 多轮会话

- [x] 6.1 新增 `ExecutionSessionRepository`（按唯一键取、创建、抢占 `IDLE → BUSY`、释放、更新活跃时间）；验证：仓储单测覆盖抢占成功、抢占失败、超时抢占三种结果。
- [x] 6.2 受理时首轮解析指针并写入会话绑定，后续轮直接读绑定；验证：集成测试断言发布新版本后同会话仍用旧版本、新会话用新版本。
- [x] 6.3 `thread_id` 改为 `sess-{session_id}`（无会话时保持 `run-{run_id}`），并让 checkpointer 复用同一线程；验证：集成测试断言第二轮模型输入包含第一轮消息，且无会话调用仍为单轮。
- [x] 6.4 同会话并发返回可识别的 `SESSION_BUSY`，不影响其他会话；验证：并发集成测试断言第二条被拒、第一条正常完成。
- [x] 6.5 实现 `session_ttl_h` 与 `history_token_limit` 校验、超期按新会话处理、超长历史裁剪；验证：单测覆盖策略边界，集成测试断言超长历史仍执行成功并记录裁剪条数。
- [x] 6.6 会话绑定版本引用失效时失败关闭且不自动切换版本；验证：集成测试撤销工具授权后断言第二轮失败、错误指向该工具。
- [x] 6.7 预览会话首轮固化临时配置，后续轮复用，修改编辑态需新建会话；验证：集成测试断言预览多轮始终使用首轮快照。
- [x] 6.8 新增会话查询能力（按 Agent 与时间范围列出会话及绑定版本）；验证：接口测试断言分页与租户隔离，OpenAPI 文档同步。

## 7. 指标口径与轨迹

- [x] 7.1 运行查询与指标聚合默认排除 `kind=PREVIEW`，并支持显式包含与按种类筛选；验证：单测断言同一数据集在两种口径下的调用量与成功率不同且符合预期。
- [x] 7.2 指标新增会话数，调用量与会话数分别统计；验证：集成测试构造同会话多次调用，断言调用量大于会话数。
- [x] 7.3 预览运行可在轨迹页按种类筛选并展示 `config_hash`；验证：接口测试断言返回摘要字段，正式运行返回发布与版本标识。

## 8. 控制台前端

- [x] 8.1 `AgentListView` 接入服务端分页与筛选，展示状态与三环境版本列，空态与错误态可重试；验证：`cd frontend && npm run build` 通过，并在本地验证筛选后翻页条件保持。
- [x] 8.2 新增 `AgentDetailView`：身份概览、配置四区、版本列表与发布面板；引用选择器只读资源内容；验证：构建通过，本地验证四区加载与引用信息展示。
- [x] 8.3 配置编辑态抽为 composable，保存与预览共用同一引用集合，支持基准版本与未保存离开确认；验证：本地验证保存后基准更新、刷新后按基准重建。
- [x] 8.4 新增 `AgentPreviewPanel`：连续对话、工具调用条目、单轮轨迹抽屉、配置变更提示新建会话；验证：构建通过，本地验证多轮上下文与工具条目顺序。
- [x] 8.5 生命周期操作按状态与权限渲染，服务端拒绝时刷新状态并展示原因；验证：以只读账号与管理员账号分别验证按钮可用性。
- [x] 8.6 `ReleaseView` 展示三环境当前生效版本与发布前体检结果；`PlaygroundView` 改用预览能力；验证：构建通过，本地验证体检问题阻断发布入口。
- [ ] 8.7 更新路由与导航，使 Agent 管理入口对 `AGENT_READ` 可见；验证：构建通过，直接访问受限路由不报错。

## 9. 集成验证与文档

- [ ] 9.1 编写端到端用例：创建（含初始配置）→ 预览 → 保存版本 → 发布 DEV → 连续对话 → 停用 → 调用失败 → 启用恢复；验证：`cd backend && pytest tests/e2e` 通过。
- [ ] 9.2 演练迁移与回滚：在副本库执行 `upgrade head` → `downgrade` → `upgrade head`，核对 Agent 状态与运行种类回填；验证：迁移脚本输出与数据核对结果一致。
- [ ] 9.3 更新 README 的运行方式说明与 API 文档中 `session_id` 语义变更（由仅记录改为参与会话寻址），并说明无 `session_id` 时仍为单轮；验证：文档中的示例请求与当前实现一致，评审通过。

## Workflow follow-up

- 完成实现与评审后归档本变更，并确认归档后的主 spec 中已包含 `INACTIVE` 状态与预览运行种类。
