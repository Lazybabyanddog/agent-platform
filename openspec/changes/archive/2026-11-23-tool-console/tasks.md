# Tasks

## 1. 工具契约聚合字段（catalog 后端）

- [x] 1.1 在 `contexts/catalog/infrastructure/repositories.py` 增加 `ToolVersionRepository.latest_for_tools(tool_ids)`（`DISTINCT ON (tool_id)` + `version_no DESC`），返回 `{tool_id: ToolVersion}`；验证：在 `backend/tests/integration/test_catalog.py` 增加多版本用例，断言每个工具只取到最高版本号
- [x] 1.2 新增 `ToolView` 组装逻辑，令 `ToolService.list/get` 返回带 `risk_level`、`latest_version_no`、`granted`、`updated_at` 的视图；验证：同一测试文件断言列表与详情字段齐全，且两次查询（tools + 批量 versions + grants）即可完成，不随工具数增长
- [x] 1.3 扩展 `api/schemas.py` 的 `ToolResponse` 并让路由映射视图对象；验证：启动后端后 `curl /openapi.json` 中 `ToolResponse` 含四个新字段，`cd backend && pytest tests/integration/test_catalog.py -q` 通过
- [x] 1.4 覆盖边界：无版本工具、未授权工具、刚授权/刚撤销后的 `granted` 取值；验证：上述场景在同一测试文件中各有一条用例并通过

## 2. 工具启停与授权状态查询（catalog 后端）

- [x] 2.1 `ToolService.set_status` 实现幂等状态切换，新增 `PATCH /api/v1/tools/{tool_id}/status`（`TOOL_MANAGE`）并返回聚合视图；验证：集成测试断言重复设置同一状态返回 200 且状态不变
- [x] 2.2 验证停用语义：停用后 `tool-versions/{id}/execute` 被拒绝（`RESOURCE_UNAVAILABLE`），重新启用且授权仍在时可调用；验证：在 `backend/tests/integration/test_tool_executor.py` 增加该双段用例
- [x] 2.3 新增 `GET /api/v1/tools/{tool_id}/grant`（`TOOL_READ`），未授权返回 200 + `granted=false`，已授权返回 `granted_at`，工具不存在返回 404；验证：集成测试覆盖三种情形
- [x] 2.4 权限面验证：`member` 角色调用状态切换与授权端点返回 403，且不改变任何状态；验证：在 `backend/tests/integration/test_authorization.py` 补充用例

## 3. 按工具查询调用记录（insight 后端）

- [x] 3.1 新增迁移 `0006`，为 `insight.tool_call` 添加 `(tenant_id, tool_id, started_at)` 索引；验证：`cd backend && alembic upgrade head` 与 `alembic downgrade` 均成功且索引可回退
- [x] 3.2 `ToolCallRepository` 增加 `list_for_tool(tenant_id, tool_id, tool_version_id?, start, end, limit)` 与聚合查询（count/succeeded/failed/avg latency/按 error_code 分组）；验证：新增集成测试断言成功与失败混合时的计数与平均耗时
- [x] 3.3 新增 `ToolCallService` 与 `GET /api/v1/tool-calls`（`TOOL_READ`，默认窗口 7 天、`limit` 默认 20 上限 100，`start >= end` 抛字段级校验错误），响应含聚合与 `items`；验证：集成测试断言 `total == 0` 时 `success_rate` 为 `null`，且响应体不含 `request_body`/`response_body`
- [x] 3.4 租户隔离验证：以另一租户的工具标识查询返回空结果而非报错；验证：在 `backend/tests/integration/test_trace.py` 增加该用例

## 4. 前端契约类型与请求封装

- [x] 4.1 启动后端后执行 `cd frontend && npm run gen:api`，确认 `src/api/schema.d.ts` 的 diff 只有新增；验证：`diff` 中无字段删除，`npm run typecheck` 通过
- [x] 4.2 在 `src/api/endpoints.ts` 增加 `listTools`、`getTool`、`createTool`、`addToolVersion`、`listToolVersions`、`updateToolStatus`、`getToolGrant`、`grantTool`、`revokeTool`、`executeToolVersion`、`listToolCalls`、`listSecrets` 封装，类型全部取自生成类型；验证：`npm run typecheck` 通过且无 `any`

## 5. 工具列表页与入口

- [x] 5.1 新建 `src/views/ToolListView.vue`：展示 code/名称/类型/风险等级/启用状态/授权状态，支持本地过滤、空状态、加载失败重试；验证：启动前端后用真实租户数据核对六列与过滤行为
- [x] 5.2 实现新建对话框：`code` 前端规则校验、冲突定位到 `code` 字段、提交成功后跳转详情；验证：制造重复 code 时字段级提示且输入不丢失
- [x] 5.3 注册 `/tools` 与 `/tools/:toolId` 路由、在 `App.vue` 的 `navItems` 增加「工具管理」，并更新 `docs/dev-setup.md` 的控制台入口描述（当前写的是"五个入口"）；验证：侧边栏可进入列表，`npm run typecheck` 通过

## 6. 工具详情页与定义编辑器

- [x] 6.1 新建 `src/components/ToolSpecEditor.vue`：HTTP 类型提供方法/URL/超时/请求头/认证表单与两个 Schema 文本域，采用"原 spec 为基底、表单覆盖"策略，未知键只读提示；非 HTTP 类型退化为整段 JSON；验证：编辑一个带未知键的 spec 后保存，未知键仍在
- [x] 6.2 接入后端错误定位：`details[].field` 前缀 `spec.` 映射到对应控件，无法映射的落到顶部汇总；验证：提交非法 URL 与顶层非 object 的 Schema，各自定位到正确控件
- [x] 6.3 详情概览 + 版本列表（版本号/风险/时间/指纹）+ 新增版本 + 无变更禁用保存 + `config_hash` 与上一版相同时提示"与上一版本等价"；验证：仅改风险等级会生成新版本，无改动时保存按钮不可用
- [x] 6.4 授权与启停交互：授权/撤销需确认，停用时提示"引用该工具的 Agent 版本运行时会失败"，未授权或停用时测试入口给出可点击引导；验证：撤销授权后测试按钮变为引导态而非只报错
- [x] 6.5 认证凭据选择器：具备 `secret:manage` 时加载凭据列表（解包分页 `items`），否则提示原因并允许手填凭据 ID；验证：分别用管理员与只读角色核对两种形态

## 7. 测试调用与调用概览

- [x] 7.1 测试调用面板：JSON 入参语法校验、错误码与参数路径展示、`HIGH` 风险需勾选确认后才带 `allow_high_risk`、固定展示"测试调用不计入调用记录"；验证：对只读工具、高风险工具、非法入参三种情形分别操作
- [x] 7.2 调用概览：展示窗口内调用量/成功率/平均耗时与最近条目（时间/状态/耗时/错误码），无调用时展示空状态而非 0% 成功率，且不提供正文入口；验证：用从未调用的新工具与有失败调用的工具各看一次

## 8. 集成验证

- [x] 8.1 端到端走通：创建工具 → 授权 → 测试调用成功 → 停用后测试被拒 → 启用并撤销授权 → 调用概览反映真实调用；验证：`backend/tests/e2e/test_tool_console_flow.py` 覆盖全链路，浏览器手动复核入口与只读降级
- [x] 8.2 回归：`cd backend && pytest` 全量通过、`cd frontend && npm run typecheck && npm run build` 通过；验证：两条命令退出码为 0，且既有 Agent/Prompt 页面无回归

## Workflow follow-up

- 待评审通过后执行 `/opsx:apply` 实施。
- 实施完成后归档变更（`openspec archive tool-console`）。
