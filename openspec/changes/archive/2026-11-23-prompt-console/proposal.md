# Proposal

## Why

Prompt 的后端能力早已完整：多消息模板、变量声明（`name`/`type`/`required`/`default`）、不可变版本、变量渲染预览、版本对比。但控制台只有四屏（Agent 列表、版本与快照、发布与回滚、调试台），Prompt 只有 API——`frontend/src/api/endpoints.ts` 连一个 Prompt 函数都没封装。

这带来两个具体问题：

- **模板是结构化的，不是一段文本**。`messages` 是带 `role` 的对象数组，变量声明是四字段对象数组，占位符只允许 `{{ name }}` 且必须已声明——全部靠手写 JSON 提交，任何一处不符就在保存时拿到 422，且看不到具体是哪条消息、哪个变量出错。
- **渲染结果不可见**。Prompt 的价值在"给定变量后模型实际看到什么"，而这只能靠调 `/prompt-versions/{id}/render` 才能看到。没有预览，改 Prompt 就是在盲改，`default` 与 `required` 配错要到调试台跑一次调用才暴露。

Prompt 恰恰是 Agent 版本配置里改得最频繁的一项，缺失的界面直接把日常调整成本推到终端。

## What Changes

- **新增「Prompt 管理」菜单与两个页面**：
  - 列表页：展示 code / 名称 / 描述 / 状态，支持按 code 或名称前端过滤，"新建"只填 code、名称、描述并跳转详情。
  - 详情页：左侧模板编辑（system / user / assistant 消息增删改与排序）+ 变量声明表（四列行内编辑）；右侧渲染预览（按变量类型生成输入控件，调渲染接口逐条展示渲染后消息）；下方版本历史与两版本对比。
- **版本语义对齐后端**：保存永远新增版本，不改旧版本；版本不可编辑，只能基于最新版本继续新增。
- **API 封装与类型**：`endpoints.ts` 增加 Prompt 列表/详情/版本列表/版本详情/新增版本/渲染/对比 共 7 个函数，`schema.d.ts` 用 `npm run gen:api` 重新生成。
- **权限收敛**：以 `/auth/me` 返回的 `permissions` 为准，缺少 `PROMPT_MANAGE` 时隐藏"新建 / 保存 / 新增版本"等写操作，页面退化为只读（与后端 `PROMPT_READ` / `PROMPT_MANAGE` 两个权限点一致）。

**明确非目标**

- 不做 Prompt 的环境发布或回滚：后端没有 `prompt_release` 概念，Prompt 通过 Agent 版本绑定与发布生效，本次不引入新概念。
- 不做测试样例集、批量评测、A/B 对比。
- 不做"哪些 Agent 版本引用了这个 Prompt"的反查（需要后端新增查询）。
- 不做 JSON Schema 级别的高级变量约束（`enum`/`pattern`/`min`），只支持后端已实现的四种类型与必填、默认值。
- 不改后端任何代码与接口。

## Capabilities

### New Capabilities

- `console/prompt-management`: 控制台侧的 Prompt 管理能力——列表与过滤、消息模板与变量声明的可视化编辑、版本新增、渲染预览、版本对比，以及基于权限的只读降级。

### Modified Capabilities

无。本变更只新增前端能力，不改变既有后端规格。

## Impact

- **代码**：新增 `frontend/src/views/PromptListView.vue`、`PromptDetailView.vue`（含模板编辑、变量表、预览、版本对比四个子块，可拆为 `components/prompt/` 下的组件）；修改 `src/router/index.ts`、`src/App.vue`（菜单项）、`src/api/endpoints.ts`；重新生成 `src/api/schema.d.ts`；`src/stores/session.ts` 复用已有 `permissions`。
- **后端**：零改动。列表页不展示"最新版本号"，避免为它新增接口或做 N+1 请求（见 Open Questions）。
- **依赖**：无新增，沿用 Element Plus。
- **验证**：`npm run typecheck` 与 `npm run build`；控制台手测新建 → 模板编辑 → 保存 v1 → 改模板保存 v2 → 预览两种变量取值 → 对比 v1/v2；用只读账号确认写操作不可见。
- **风险**：
  - 前端无自动化测试基建，本变更不引入测试框架，回归依赖 typecheck + 构建 + 手测清单；若后续前端用例增多，再单独提变更引入 Vitest。
  - 编辑器把结构化数据映射到表单，需保证"未修改不产生新版本"的体验——保存按钮在无变更时禁用，避免版本号膨胀。
  - 渲染预览依赖真实后端；本地开发需按 `docs/dev-setup.md` 起依赖与迁移。

## Open Questions

- 列表页是否需要展示"最新版本号/最近更新时间"：当前 `PromptResponse` 只有 `id/code/name/description/status`，要展示就得后端加字段或前端对每条 Prompt 各拉一次版本列表。本变更选择不展示；若确有需要，建议单独提一个后端小变更在 `PromptResponse` 增加 `latest_version_no` / `updated_at`，避免前端 N+1。
