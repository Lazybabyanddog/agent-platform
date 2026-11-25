# Tasks

## 1. 预览链路携带运行时上下文

- [x] 1.1 在 `execution/api/schemas.py` 的 `PreviewRequest` 增加可选 `runtime_context: list[str] = []`，并让 `execution/api/routes.py` 的预览路由透传给预览服务；验证：`tests/unit` 通过，且不带该字段的预览请求行为与改动前一致（现有预览测试不需要修改即可通过）
- [x] 1.2 让 `PreviewService.preview` 接收声明并传入 `assemble_snapshot(... runtime_context=...)`；验证：扩展 `tests/integration` 中预览用例，断言 `preview_snapshot.runtime_context` 与提交一致，且声明未知项时返回 422 并在错误详情指出该项
- [x] 1.3 在 `tests/integration` 增加一条「声明了上下文的预览」用例：断言模型收到的消息序列里上下文位于系统消息之后、且会话历史不累积该消息；验证：`python -m pytest tests/integration -q` 全绿

## 2. 类型定义与请求契约同步

- [x] 2.1 启动后端后执行 `npm run gen:api` 重新生成 `src/api/schema.d.ts`；验证：生成结果中能检索到 `AgentConfigRequest.runtime_context`、`PreviewRequest.runtime_context`、`AgentVersionDiffResponse.runtime_context`、工具版本 `warnings`，且 `npm run typecheck` 通过
- [x] 2.2 若本机无法启动后端，改为手工同步上述四处并在变更内标注待重跑（未触发：直接调用 `create_app().openapi()` 导出 `openapi.json` 完成生成，无需手工同步）；验证：`npm run typecheck` 通过，且 `git diff src/api/schema.d.ts` 仅包含上述字段变化

## 3. 工具契约编辑：描述与模型可见契约

- [x] 3.1 在 `ToolSpecEditor.vue` 增加「用途描述」输入，绑定 `spec.description`，留空时删除该键；验证：`npm run typecheck` 通过，页面保存后 `GET /tools/{id}/versions` 返回的 spec 中描述与所填一致、留空时不出现该键
- [x] 3.2 增加模型可见契约编辑区：按当前 `input_schema` 派生参数骨架（属性名、类型、必填、`items`），作者逐参数填写 `description` / `title` / `format`，保存时把注解叠加到派生骨架生成 `model_schema`；全部注解为空时不写该键；验证：`npm run typecheck` 通过，且填写格式后保存的版本 spec 中 `model_schema` 与 `input_schema` 结构一致（接口返回 201，不被 `_assert_same_structure` 拒绝）
- [x] 3.3 让模型可见契约区在 `input_schema` 结构变化后重新派生，并在存在嵌套对象或组合分支时只读展示、提示需通过接口维护；验证：把 `input_schema` 的某个属性改名后，模型可见契约区参数随之更新且已删参数的注解消失（手工操作一次并在 PR 描述记录结果）
- [x] 3.4 在 `docs/tool-contract.md` 补「在控制台维护模型可见契约」一节（结构镜像规则、注解允许项、嵌套结构的限制）；验证：文档中给出的操作步骤能在页面上按顺序完成

## 4. 工具契约编辑：返回语义与保存告警

- [x] 4.1 在 `ToolSpecEditor.vue` 增加业务终态列表控件，编辑 `returns.terminal_statuses`（非空字符串、去重），列表为空时删除 `returns`；界面文案说明命中时机为「成功返回且返回体 `status` 等于声明值」；验证：`npm run typecheck` 通过，保存后 spec 中 `returns` 与所填一致，空列表提交的 spec 不含 `returns`
- [x] 4.2 在 `ToolDetailView.vue` 的保存流程中读取响应 `warnings`，按 `spec.input_schema.properties.<name>` 回填到对应参数行，无法定位的在编辑器顶部汇总，且与错误区分展示；验证：对日期参数不填 `format` 保存一次，版本创建成功且页面出现该参数的提示，不出现红色错误态
- [x] 4.3 展示所选版本的描述来源（版本内 / 工具级 / 缺失），两处都为空时把保存错误定位到描述输入且保留其余已填内容；验证：用无描述的工具保存版本，错误定位到描述字段而非仅顶部文本；补齐后再保存成功

## 5. Agent 配置：运行时上下文声明与版本对比

- [x] 5.1 在 `useAgentConfigEditor.ts` 增加 `runtimeContext`：`SnapshotShape` 读取快照声明、`serialize()` 纳入脏检查、`referencePayload()` 提交该字段；验证：`npm run typecheck` 通过，且 `tests` 之外用页面验证——基于已声明版本仅改模型后保存，新版本快照仍含原声明
- [x] 5.2 在 `AgentDetailView.vue` 的配置表单增加「运行时上下文」多选（当前时间 / 时区），说明注入位置与「不写入会话历史」，清空全部时弹确认；验证：勾选后保存并重新进入页面，勾选状态按版本回显；取消全部并确认后保存，新版本不含声明
- [x] 5.3 保存成功后比对响应快照的声明与提交值，不一致时提示「声明未生效，请确认后端版本」；验证：用旧后端（未含该字段）保存一次，页面出现该提示而不是静默成功
- [x] 5.4 在 `AgentVersionsView.vue` 的对比区增加运行时上下文一节，展示各自声明项与「已变更 / 未变更」；验证：对比未声明与声明两个版本时显示变更与前后取值，对比两个相同声明时显示未变更
- [x] 5.5 在 `docs/runtime-context.md` 补「在控制台声明」一节（含清空声明的影响与版本对比位置）；验证：文档步骤与页面一致，可照着操作一遍

## 6. 调试台与配置页预览的注入展示

- [x] 6.1 在 `useAgentConfigEditor.ts` 的 `toPreviewPayload()` 带上编辑态声明；验证：勾选「当前时间」但未保存版本时发起配置页预览，`PreviewResponse.preview_snapshot.runtime_context` 含 `now`
- [x] 6.2 在 `PlaygroundView.vue` 的 `loadBasis()` 把最新版本快照的声明放进 `refs`，`buildRequest()` 随引用提交；切换 Agent 时重新装载；验证：在声明与未声明两个 Agent 之间切换，请求载荷中的 `runtime_context` 随之变化（网络面板或后端日志确认）
- [x] 6.3 在调试面板展示本次注入的上下文项：优先取 `preview_snapshot.runtime_context`，缺失时退回版本声明并标注来源，未声明时显示「未注入」；验证：声明版本显示两项、未声明版本显示未注入，执行均正常完成

## 7. 集成验收

- [x] 7.1 端到端串一遍：新建/编辑工具版本（描述 + 模型可见契约 + 终态）→ 配置 Agent 版本（含声明）→ 调试台对话 → 正式调用；验证：`GET /runs/{run_id}/trace` 中工具卡带描述与 `format`、消息序列含上下文、工具终态命中后模型不重试
- [x] 7.2 全量回归：`python -m pytest tests -q`、`npm run typecheck`；验证：两者均无失败，且 `openspec validate add-contract-context-console --strict` 通过
