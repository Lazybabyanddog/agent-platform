# Design

## Context

前端现状（已核实）：Vue 3 + Element Plus + Pinia + vue-router，`src/views/` 只有 4 个视图（Agent 列表、Agent 版本、发布与回滚、调试台），`App.vue` 的导航 3 项，`endpoints.ts` 只封装 agent / version / release / invoke / trace 五组接口。HTTP 封装 `request<T>` 已把后端 `{"error":{code,message,details,trace_id}}` 转成 `ApiError`，`details` 保留 `field`/`reason`；类型由 `openapi-typescript` 生成到 `src/api/schema.d.ts`（`npm run gen:api`）。

后端 Prompt 契约（已核实，本次不改）：

| 端点 | 返回 | 关键字段 |
| --- | --- | --- |
| `GET /prompts` | `PromptResponse[]` | `id` / `code` / `name` / `description` / `status` |
| `GET /prompts/{id}` | `PromptResponse` | 同上，**不含 messages** |
| `POST /prompts` | `PromptResponse` | 可带 `messages`/`variables`，为空时不建版本 |
| `GET /prompts/{id}/versions` | `PromptVersionResponse[]` | `id` / `version_no` / `messages` / `variables` / `config_hash` / `note` / `created_at` |
| `POST /prompts/{id}/versions` | `PromptVersionResponse` | 请求体 `messages` / `variables` / `note` |
| `GET /prompt-versions/{id}` | `PromptVersionResponse` | 同上 |
| `GET /prompt-versions/diff` | `PromptDiffResponse` | `base_version_no` / `target_version_no` / `identical` / `message_changes` / `variables_added` / `variables_removed` / `variables_changed` |
| `POST /prompt-versions/{id}/render` | `PromptRenderResponse` | 请求 `{values}`，响应 `messages: [{role, content}]` |

数据形状：`messages = [{role: system|user|assistant, content: string}]`，`variables = [{name, type: string|number|integer|boolean, required, default?}]`，占位符语法 `{{ name }}`，未声明的占位符与缺必填变量都会在写入/渲染时返回 `VALIDATION_FAILED`，`details[].field` 形如 `messages.1.content` 或 `variables.0.name`。

约束：本次不改后端；不引入新前端依赖；不引入前端测试框架。

## Goals / Non-Goals

**Goals:**

- Prompt 的全部日常操作（新建、改模板、改变量、存版本、看渲染、比版本）在浏览器内完成，不需要终端。
- 保存前就能看到"模型实际会收到什么"，把 `required` / `default` / 占位符拼错这类错误拦在发布之前。
- 权限语义与后端一致：只读账号看不到任何写入口，且不会发出写请求。

**Non-Goals:**

- 不改变 Prompt 的版本模型（不提供"编辑历史版本"的入口）。
- 不做环境发布、样例集、批量评测、Agent 引用反查。
- 不做富文本/Markdown 渲染，消息内容按纯文本编辑与预览（与后端存储一致）。
- 不为列表页新增后端字段。

## Decisions

### D1：两个整页，不用抽屉

详情区包含模板编辑、变量表、渲染预览、版本对比四块，抽屉宽度不够且预览需要纵向空间。路由：`/prompts`（列表）、`/prompts/:promptId`（详情）。详情页用查询参数 `?version=` 记住当前选中版本，便于刷新与分享定位（版本 id 或版本号，选后者可读性更好）。

### D2：以"版本列表"为唯一数据源，详情页不依赖 `GET /prompts/{id}` 的模板

`PromptResponse` 不含 `messages`/`variables`，详情页进入后：

1. `GET /prompts/{id}/versions` 拿全部版本；
2. 取 `version_no` 最大者为编辑基线（若 `?version=` 指定则用指定版本）；
3. `GET /prompts/{id}` 仅用于展示名称与描述。

避免为"取最新模板"额外造接口。没有版本时（刚创建的空 Prompt）展示引导："填充模板并保存，将生成 v1"。

### D3：脏检测用本地深比较，不用 `config_hash`

`config_hash` 是后端对 `{messages, variables}` 的哈希，前端复算需要保证字段顺序与序列化完全一致，脆弱。改为对 `draft` 与基线做规范化深比较（消息按数组顺序、变量按 `name` 排序后比较），不一致才允许保存。保存成功后用返回版本替换基线。

### D4：保存 = `POST /prompts/{id}/versions`，永不修改旧版本

与后端"版本只读"语义一致。保存成功后：更新基线 → 重新拉版本列表（或把返回值插入本地列表）→ 预览区自动切到新版本 → 顶部提示"已生成 v{n}"。失败（409 并发冲突）提示重试并保留草稿。

### D5：变量表用固定四列行内编辑，`default` 需要"是否设置"开关

`default` 是 JSON 值而非字符串：`number`/`integer` 用数字输入，`boolean` 用开关，`string` 用文本输入。必须区分"没有 default"与"default 为空字符串"——后者是合法值。用每行一个"设置默认值"开关控制键是否存在，关闭时提交的变量对象里不含 `default` 键。

类型切换时清空 `default` 并提示，避免 `"5"` 被当作 `number` 提交后渲染报错。

### D6：渲染预览的输入控件由变量声明生成，提交前做一次前端类型检查

- `string` → 单行文本（内容可能长，用 `textarea` 更稳）；
- `number` / `integer` → 数字输入，`integer` 额外要求整数；
- `boolean` → 开关；
- 有 `default` 的预填并标注来源。

前端检查只做"必填是否为空"和"整数/数值格式"，其余交给后端 422——避免前端与后端校验规则漂移。错误按 `details[].field` 定位到变量行，`messages.*` 的占位符错误定位到消息条目。

### D7：预览结果按消息逐条展示角色徽标与内容

后端返回的是渲染后的 `messages`，直接按 `role` 上色展示（system/user/assistant 三色），并显示"由默认值填充的变量"清单（前端根据输入状态推导）。不展示未渲染的半成品。

### D8：版本对比复用后端 diff，不在前端算文本差异

`PromptDiffResponse` 已经给出 `message_changes`（按 `index` 的 `base`/`target`）、`variables_added`/`removed`/`changed`，前端只负责呈现。`identical=true` 时直接显示"无差异"，不渲染空表。版本下拉默认 `base=最新-1`、`target=最新`；只有 1 个版本时对比区显示"至少需要两个版本"。

### D9：权限判断集中在页面顶层，子组件只接收 `readonly`

从 `useSessionStore().me.permissions` 读 `PROMPT_MANAGE`，计算 `canManage` 传给各子组件。只读时：隐藏"新建""保存""新增版本"按钮，消息/变量控件置于禁用态（而不是隐藏内容），这样只读用户仍能看清模板结构，也避免直接访问 URL 时页面空白。

### D10：组件拆分到 `components/prompt/`

```
views/PromptListView.vue        列表 + 过滤 + 新建对话框
views/PromptDetailView.vue      编排：加载、选中版本、保存、权限
components/prompt/MessageEditor.vue   消息增删改与排序
components/prompt/VariableTable.vue   变量四列编辑 + default 开关
components/prompt/RenderPreview.vue   变量取值表单 + 渲染结果
components/prompt/VersionDiff.vue     版本选择 + 变更条目
```

页面持有 `draft` 与版本列表，子组件通过 `v-model` 与事件回传，避免多处各自请求。

### D11：端点封装按既有风格手写

`endpoints.ts` 增加 `listPrompts` / `getPrompt` / `createPrompt` / `listPromptVersions` / `createPromptVersion` / `getPromptVersion` / render / diff，函数体只用 `api.get/post` + 生成类型，不引入请求库或缓存层。

## Risks / Trade-offs

- [前端无自动化测试] → 本变更不引入 Vitest，用 `npm run typecheck` + `npm run build` + 明确手测清单兜底；若后续前端用例增多，单独提变更建测试基建。
- [列表不显示版本号，用户看不到"哪个 Prompt 更新过"] → 接受；见 Open Questions，需要时由后端加字段解决，而不是前端 N+1。
- [深比较对超大模板有成本] → 模板通常几条消息，成本可忽略；比较前只做浅层结构归一（变量按 name 排序）。
- [预览依赖真实后端] → 本地需先起依赖与迁移；错误提示已包含 `trace_id`，联调成本可控。
- [变量类型切换导致 default 丢失] → 切换时清空并显式提示，避免静默产生非法 JSON 值。
- [路由直达无权限页面] → 详情页在无 `PROMPT_READ` 时展示权限提示而非白屏（后端会 403，前端捕获后落到统一错误态）。

## Migration Plan

纯前端，无数据迁移、无后端发布顺序要求。

上线验证：

1. `npm run gen:api` 后 `npm run typecheck` 通过（确认类型与后端一致）；
2. `npm run build` 通过；
3. 手测：新建 → 编辑模板（三种角色）→ 加两个变量 → 保存 v1 → 预览（含默认值）→ 改模板保存 v2 → 对比 v1/v2 → 用只读账号复查写入口不可见。

回滚：回退前端构建产物即可，无数据副作用（不会产生脏数据，版本由后端管理）。

## Open Questions

- 列表是否需要展示"最新版本号 / 最近更新时间"：需要后端在 `PromptResponse` 增加 `latest_version_no` 与 `updated_at`，否则只能 N+1。本次不做，留待后续小变更。
- Prompt 的 `ARCHIVED` 状态后端没有归档/恢复接口，本次只在列表展示状态徽标，不提供状态变更操作。
