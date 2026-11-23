# Tasks

## 1. 接口封装与类型

- [x] 1.1 `src/api/endpoints.ts` 增加：`listPrompts`、`getPrompt`、`createPrompt`、`listPromptVersions`、`createPromptVersion`、`getPromptVersion`、`diffPromptVersions`、`renderPromptVersion`
- [x] 1.2 运行 `npm run gen:api` 重新生成 `src/api/schema.d.ts`，确认 `PromptResponse`/`PromptVersionResponse`/`PromptDiffResponse`/`PromptRenderResponse` 类型可用
- [x] 1.3 `npm run typecheck` 通过，端点参数与生成类型一致（不出现 `any` 兜底）

## 2. 列表页与导航

- [x] 2.1 `src/router/index.ts` 增加 `/prompts` 与 `/prompts/:promptId` 两条路由
- [x] 2.2 `src/App.vue` 菜单增加「Prompt 管理」项，位置在「Agent 列表」之后
- [x] 2.3 `PromptListView.vue`：表格展示 code / 名称 / 描述 / 状态徽标，支持按 code 或名称本地过滤
- [x] 2.4 空状态展示引导文案与新建入口；加载态与错误态复用现有视图的处理方式
- [x] 2.5 新建对话框：只采集 code / 名称 / 描述，前端校验 code 规则（1~64 位，`[A-Za-z0-9_.-]`）
- [x] 2.6 创建成功后跳转 `/prompts/{id}`；`CONFLICT` 时把错误定位到 code 字段且保留已填内容；无 `PROMPT_MANAGE` 时隐藏新建入口

## 3. 详情页骨架

- [x] 3.1 `PromptDetailView.vue`：并行加载 `GET /prompts/{id}` 与 `GET /prompts/{id}/versions`
- [x] 3.2 选中版本逻辑：默认取 `version_no` 最大者；支持 `?version={version_no}` 指定；版本不存在时回落最新并提示
- [x] 3.3 无版本空态：展示"填充模板并保存将生成 v1"的引导，编辑器可直接进入新建草稿
- [x] 3.4 从 `useSessionStore().me.permissions` 计算 `canManage`（`PROMPT_MANAGE`）并下发给子组件
- [x] 3.5 403 / 404 的页面级错误态：展示权限或不存在提示，不白屏

## 4. 模板与变量编辑

- [x] 4.1 `components/prompt/MessageEditor.vue`：消息新增 / 删除 / 上移 / 下移，角色下拉仅 `system` / `user` / `assistant`，内容多行文本
- [x] 4.2 `components/prompt/VariableTable.vue`：`name` / `type` / `required` / `default` 四列行内编辑，变量名按后端正则校验
- [x] 4.3 `default` 用"是否设置默认值"开关控制键是否存在，避免把空字符串与未设置混为一谈
- [x] 4.4 变量类型切换时清空 `default` 并给出提示
- [x] 4.5 消息内容中的 `{{ name }}` 做轻量高亮，未声明的占位符在消息下方给出警示（保存仍由后端裁决）
- [x] 4.6 脏检测：变量按 `name` 排序后与基线深比较，无变更时禁用保存
- [x] 4.7 保存调用 `POST /prompts/{id}/versions`，成功后更新基线、刷新版本列表、预览切到新版本并提示"已生成 v{n}"
- [x] 4.8 保存失败（422）按 `details[].field` 定位到具体消息条目或变量行；409 并发冲突提示重试且保留草稿

## 5. 渲染预览

- [x] 5.1 `components/prompt/RenderPreview.vue`：按变量声明生成取值控件（字符串文本域 / 数值数字输入 / 布尔开关），有 `default` 的预填并标注来源
- [x] 5.2 提交前做必填与整数/数值格式检查，其余交给后端校验
- [x] 5.3 调用 `POST /prompt-versions/{id}/render`，逐条展示渲染后的角色与内容，`system` / `user` / `assistant` 用不同样式区分
- [x] 5.4 失败时按 `details[].field` 把错误挂到对应变量行，且不展示部分渲染结果
- [x] 5.5 切换选中版本时清空旧的预览结果，避免版本与结果错配

## 6. 版本历史与对比

- [x] 6.1 版本区域展示版本号、备注、创建时间、配置哈希前 8 位，默认选中最新版本
- [x] 6.2 `components/prompt/VersionDiff.vue`：两个版本下拉（默认 `最新-1` 与 `最新`），只有 1 个版本时提示"至少需要两个版本"
- [x] 6.3 展示 `message_changes`（按 index 的前后角色与内容）、`variables_added` / `variables_removed` / `variables_changed`
- [x] 6.4 `identical=true` 时显示"无差异"，不渲染空条目

## 7. 文档与验收

- [x] 7.1 `README.md` 控制台章节由"四屏"更新为五个入口（新增 Prompt 管理），并说明只读账号的可见性
- [x] 7.2 `docs/dev-setup.md` 若有控制台手测清单，补 Prompt 页面的一条操作路径
- [x] 7.3 `npm run typecheck` 与 `npm run build` 均通过
- [x] 7.4 手测：新建 → 编辑三条消息（三种角色）→ 加两个变量（含默认值）→ 保存 v1 → 预览 → 改模板保存 v2 → 对比 v1/v2 → 只读账号复查写入口不可见
- [x] 7.5 `openspec validate prompt-console --strict` 通过
