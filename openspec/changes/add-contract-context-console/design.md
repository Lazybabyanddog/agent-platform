# Design

## Context

见 `proposal.md - Why`。影响实现方式的现状约束：

- 工具契约编辑器 `ToolSpecEditor.vue` 以结构化表单产出 `spec`：先展开原 `spec`，再覆盖已知字段（`method` / `transport` / `tool_name` / `url` / `timeout_s` / `headers` / `auth` / `input_schema` / `output_schema`），未知键原样保留。HTTP/MCP 类型没有原始 JSON 模式，因此 `model_schema`、`returns`、`description` 今天既不可见也不可编辑。
- 后端结构等价规则：`tool.py::_assert_same_structure` 只允许注解类关键字（`description`、`title`、`format`、`examples`、`default`）不同，`type`、属性键集合、`required`、`items` 与组合分支必须一致；`returns` 仅接受 `terminal_statuses`（`RETURN_KEYS`）；保存响应里的 `warnings` 形如 `{"field": "spec.input_schema.properties.<name>", "reason": ...}`。
- 终态命中判定在运行时读取「工具成功返回、返回体 `status` 等于声明值」（`nodes.py::_terminal_status`）。
- Agent 配置编辑态 `useAgentConfigEditor` 的载荷只含 `model_id` / `prompt_version_id` / `tool_version_ids` / `params` / `policy`，`loadFromVersion` 也不读取 `runtime_context`；因此页面保存的新版本会把既有声明清零。
- 预览 `PreviewService.preview` 用提交的引用重新装配快照，`assemble_snapshot(...)` 未传 `runtime_context`，调试台因此永远拿不到上下文；`PreviewResponse.preview_snapshot` 已回传装配结果，可直接用于展示实际注入项。
- `src/api/schema.d.ts` 由 `npm run gen-api` 从运行中的后端 `openapi.json` 生成，当前版本早于 `runtime_context`，且此前有手工补录（`RuntimeContextDiff`、工具版本 `warnings`）。

## Goals / Non-Goals

**Goals:**

- 模型可见契约、返回语义、描述来源、保存告警都能在工具详情页完成维护与查看，且产出的 `spec` 必然能通过后端结构等价校验。
- Agent 版本的运行时上下文声明可编辑、可回显、可对比，页面保存不再丢失声明。
- 调试台与配置页预览能验证声明的真实效果，且面板显示的是执行实际注入了什么。
- 类型定义来自后端 OpenAPI，而不是继续手工补录。

**Non-Goals:**

- 不新增工具级描述编辑接口：版本内描述在契约编辑器里维护即可，工具级描述继续在新建工具时确定。
- 不对 `model_schema` 的嵌套对象、`anyOf` / `oneOf` / `allOf` 分支提供逐层界面编辑，这些结构在界面上只读呈现并提示通过接口维护。
- 不改变 `returns` 的既有语义（只支持 `terminal_statuses`），不引入任意返回字段声明。
- 不改动正式调用的上下文注入实现，也不改快照结构。

## Decisions

### 1. 模型可见契约用「结构镜像 + 注解编辑」，而不是自由 JSON 编辑器

编辑器不直接编辑 `model_schema` 的骨架：骨架在保存时由 `input_schema` 派生（属性键、类型、`required`、`items` 一一对应），作者只填每个参数在模型侧的 `description` / `title` / `format`。

- 为什么：后端只允许注解类差异，任何自由编辑都会在提交后 422，作者无法判断是结构还是格式问题。镜像派生让「模型按 A 构造、平台按 B 校验」在界面上不可能发生。
- 备选：给 HTTP/MCP 开放原始 JSON 模式（当前只有其它类型有）。放弃原因是它把结构错误推迟到服务端，且与既有表单校验的定位能力冲突。
- 参数注解单独存放（按参数名索引的表单状态），`buildSpec()` 时叠加到派生的骨架上；所有注解都为空时不写 `model_schema`，保持「未声明即回退调用契约」的既有行为。
- 调用契约的 `input_schema` 在编辑器里仍是 JSON 文本，结构变化后派生骨架随之变化；已被删除参数的注解在保存时被丢弃。

### 2. 返回语义只暴露状态值列表

用标签式多列表控件编辑 `returns.terminal_statuses`，只接受非空字符串，列表为空时删除 `returns` 键（等价于未声明）。界面文案明确命中时机（成功返回且返回体 `status` 等于声明值），与 `_terminal_status` 的实现一致，避免作者把它理解成"调用失败也终止"。

### 3. 告警按字段路径回填到参数行

`addToolVersion` 的响应已带 `warnings`，控件按 `spec.input_schema.properties.<name>` 解析出参数名，回填到该参数行；无法解析的统一放在编辑器顶部汇总。告警与错误分开展示：错误阻止保存（红色、字段级），告警只做提示（不阻塞、可关闭）。这样「保存成功但需要作者注意」与「保存失败」不会被混为一谈。

### 4. 描述：版本内编辑 + 来源标注，不新增工具级编辑接口

契约编辑器增加「用途描述」输入，绑定 `spec.description`：留空时删除该键，由后端继承工具级描述。详情页展示描述时标注来源（版本内 / 工具级 / 缺失），仅当两处都为空时保存失败，并把错误定位到这个输入。

- 备选：新增 `PATCH /tools/{id}` 编辑工具级描述。放弃原因是它扩大了后端接口面与权限测试范围，而版本内描述已足以让任何既有工具产出可保存的版本。

### 5. 运行时上下文声明进入配置编辑态的序列化与载荷

`runtimeContext: string[]` 加入编辑态：`loadFromVersion` 从快照读取（`SnapshotShape` 增加该字段，读取失败按空数组），`serialize()` 纳入它以便「未保存改动」判断覆盖它，`referencePayload()` 直接提交它。清空全部声明时用确认框提示模型将失去平台时间与时区。

另外在保存成功后校验响应快照里的声明与提交值一致：前后端版本不一致时（旧后端会忽略未知字段）这一条能把静默丢失变成可见提示，而不是下一轮执行才暴露。

### 6. 预览请求新增可选 `runtime_context`

`PreviewRequest` 增加 `runtime_context: list[str] = []`，路由透传给 `PreviewService.preview`，最终进入 `assemble_snapshot(...)`，与保存版本共用同一套声明校验（未知项 422 并指出是哪一项）。未提交时默认空数组，既有调用方行为不变。

前端两处使用：

- 配置页预览：`toPreviewPayload()` 带上编辑态声明，因此未保存的声明也能预览。
- 调试台：`loadBasis()` 把最新快照的 `runtime_context` 放进 `refs`，`buildRequest()` 随引用一起提交；面板优先展示 `preview_snapshot.runtime_context`（执行实际装配结果），缺失时退回版本声明，未声明时显示"未注入"。

### 7. 类型定义重新生成，不继续手补

用 `npm run gen:api` 从后端 OpenAPI 重新生成 `schema.d.ts`，随后确认三处存在：`AgentConfigRequest.runtime_context`、`PreviewRequest.runtime_context`、`AgentVersionDiffResponse.runtime_context`，以及工具版本的 `warnings`。生成会覆盖此前手补内容，因此先前手补的字段必须在生成结果中自然出现，否则说明后端 schema 未随实现更新。

## Risks / Trade-offs

- [只镜像一层参数结构] → 嵌套对象与组合分支的模型侧注解仍需接口维护：界面在遇到这类 schema 时只读展示并给出提示，不假装支持。
- [派生骨架与调用契约的编辑顺序] → 作者同时修改 `input_schema` 文本与其注解时，结构可能先变后校验：保存前统一以当前 `input_schema` 派生再校验，冲突的注解被丢弃并在界面提示。
- [旧后端忽略 `runtime_context`] → 保存成功后比对响应快照与提交值，不一致时明确提示需要升级后端，避免静默丢声明。
- [生成类型依赖本地后端] → 环境起不来时先手工同步并标注，类型检查通过后再在有后端的环境重跑生成，避免把手工补录当成长期方案。
- [调试台展示注入项依赖 `preview_snapshot`] → 该字段在预览响应中已存在；若为空（历史会话沿用首轮快照），面板显示会话首轮快照的声明并标注来源。

## Migration Plan

- 无数据迁移：既有版本快照结构不变，`runtime_context` 缺省为空列表；`model_schema` / `returns` 仍是可选键。
- 部署顺序：后端（预览请求字段）→ 前端（表单与展示）。前端先上、后端未上时，保存会触发「声明未生效」提示，不产生错误数据。
- 回滚：前端回退表单与展示即可；后端 `runtime_context` 为可选字段，回退不影响既有调用。

## Open Questions

- 无。`model_schema` 的深层编辑与工具级描述接口都已在 Non-Goals 明确排除，可在后续独立变更中评估。
