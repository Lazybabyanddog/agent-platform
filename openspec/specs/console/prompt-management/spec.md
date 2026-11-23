# Prompt Management Console Specification

## Purpose

为控制台提供 Prompt 的可视化运维入口：使用者无需直接调用接口，即可查看与编辑多消息模板和变量声明，用不可变版本承载每一次改动，并通过渲染预览与版本对比确认改动结果，按权限对写操作降级。

## Requirements

### Requirement: Prompt 管理入口与列表

控制台 SHALL 为具备 `PROMPT_READ` 的用户提供「Prompt 管理」入口，列表展示每个 Prompt 的 `code`、名称、描述与状态（`ACTIVE` / `ARCHIVED`）。列表 SHALL 支持按 `code` 或名称进行本地过滤，无需逐次请求服务端。列表为空时 SHALL 展示空状态与「新建」引导，而不是空白页面。

#### Scenario: 进入列表

- **WHEN** 具备 `PROMPT_READ` 的用户打开 Prompt 管理
- **THEN** 系统展示本租户全部 Prompt 的 code、名称、描述与状态

#### Scenario: 按关键字过滤

- **WHEN** 用户在列表搜索框输入 `code` 或名称的片段
- **THEN** 列表即时收敛为匹配项，清空输入后恢复全部

#### Scenario: 无数据

- **WHEN** 本租户还没有任何 Prompt
- **THEN** 列表展示空状态与新建入口，不显示报错

### Requirement: 新建 Prompt

新建 SHALL 只采集 `code`、名称与描述三项，创建成功后进入该 Prompt 的详情页继续填写模板。`code` SHALL 在前端按后端规则（1~64 位，允许字母数字与 `_.-`）做前置校验；后端返回冲突时 SHALL 在表单上以字段级提示呈现，不丢失已填内容。缺少 `PROMPT_MANAGE` 的用户 SHALL NOT 看到新建入口。

#### Scenario: 创建成功

- **WHEN** 用户填写合法的 `code`、名称与描述并提交
- **THEN** 系统创建 Prompt 并跳转到它的详情页

#### Scenario: code 冲突

- **WHEN** 提交的 `code` 在本租户已存在
- **THEN** 界面在 `code` 字段上提示冲突，其余输入保持不变

#### Scenario: 只读用户

- **WHEN** 用户没有 `PROMPT_MANAGE` 权限
- **THEN** 新建入口不可见，直接访问新建地址也不会提交写请求

### Requirement: 模板与变量声明的可视化编辑

详情页 SHALL 提供消息列表编辑器，支持新增、删除、上移、下移消息，每条消息可设置角色（仅 `system`、`user`、`assistant`）与内容；SHALL 提供变量表，按行编辑 `name`、`type`（`string` / `number` / `integer` / `boolean`）、`required`、`default`。保存 SHALL 新增一个版本而不是修改既有版本；相对最新版本没有任何变更时 SHALL 禁用保存，避免产生空版本。后端返回的结构校验失败 SHALL 定位到具体消息下标或变量行展示，而不是笼统提示失败。

#### Scenario: 新增版本

- **WHEN** 用户修改模板或变量后点击保存
- **THEN** 系统创建版本号加一的版本，并把它作为当前展示的最新版本

#### Scenario: 占位符未声明

- **WHEN** 模板里出现未在变量表中声明的 `{{ name }}` 占位符并保存
- **THEN** 保存失败，界面指明是哪一条消息中的哪个占位符未声明

#### Scenario: 无变更保存

- **WHEN** 模板与变量相对最新版本没有差异
- **THEN** 保存操作不可用，不会产生新版本

#### Scenario: 只读用户编辑

- **WHEN** 用户没有 `PROMPT_MANAGE` 权限
- **THEN** 编辑器为只读，保存与新增版本入口不可见

### Requirement: 渲染预览

详情页 SHALL 提供渲染预览：以当前选中的版本为输入，按变量声明生成取值控件（字符串为文本输入、数值为数字输入、布尔为开关），调用渲染接口后逐条展示渲染结果的角色与内容。缺必填变量、类型不符、占位符未声明时 SHALL 展示对应字段的错误，且不展示部分渲染结果。

#### Scenario: 正常预览

- **WHEN** 用户为全部必填变量填入合法值并触发预览
- **THEN** 界面逐条展示渲染后的消息，占位符已被替换

#### Scenario: 缺必填变量

- **WHEN** 必填变量未填值就触发预览
- **THEN** 界面提示该变量必填，且不展示渲染结果

#### Scenario: 类型不符

- **WHEN** 变量声明为 `number` 但输入无法转为数值
- **THEN** 界面提示该变量类型不符，且不展示渲染结果

#### Scenario: 使用默认值

- **WHEN** 变量声明了 `default` 且用户未填值
- **THEN** 预览使用默认值渲染，并标明该值来自默认值

### Requirement: 版本历史与对比

详情页 SHALL 列出该 Prompt 的全部版本，至少包含版本号、备注、创建时间；SHALL 支持选择两个版本进行对比，展示消息逐条变更（变更前后的角色与内容）、变量新增、变量删除与变量定义变更。两版本内容完全一致时 SHALL 明确标注"无差异"。

#### Scenario: 查看版本列表

- **WHEN** 用户打开版本区域
- **THEN** 系统展示全部版本，默认选中最新版本

#### Scenario: 对比两个版本

- **WHEN** 用户选择基准版本与目标版本
- **THEN** 界面展示消息变更、变量新增/删除/变更，并标明版本号

#### Scenario: 完全一致

- **WHEN** 两个版本的配置哈希相同
- **THEN** 界面标注无差异，不展示空的变更条目

### Requirement: 只读降级

控制台 SHALL 以服务端返回的权限集合为唯一依据决定写操作可见性。不具备 `PROMPT_MANAGE` 的用户访问详情页 SHALL 能查看模板、变量、版本与渲染预览，但所有写操作入口不可用，且界面不因缺失权限而报错。

#### Scenario: 只读访问详情

- **WHEN** 只读用户直接打开某个 Prompt 的详情地址
- **THEN** 页面正常展示模板与版本，编辑与保存入口不可见
