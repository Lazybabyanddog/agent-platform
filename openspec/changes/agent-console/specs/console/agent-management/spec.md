# Spec Delta

## Purpose

让具备权限的租户成员在控制台完成 Agent 的发现、创建与复制，并让每个操作的可用性由服务端状态与权限决定，避免为了管理一个 Agent 而直接调用接口。

## ADDED Requirements

### Requirement: Agent 管理入口与列表

控制台 SHALL 为具备 `AGENT_READ` 的用户提供「Agent 管理」入口，以服务端分页展示本租户的 Agent。每行至少包含编码、名称、类型、状态、负责人、标签与更新时间。列表 SHALL 支持按状态、类型、标签筛选与按编码或名称关键字搜索，筛选与分页参数 SHALL 传递到服务端，不得只对当前页做本地过滤。列表行 SHALL 展示 DEV、TEST、PROD 三个环境各自当前生效的版本号，未发布的环境 SHALL 明确标注而不是留空。列表为空时 SHALL 展示空状态与新建引导；请求失败时 SHALL 展示可重试的错误态，而不是空白页面。

#### Scenario: 进入列表

- **WHEN** 具备 `AGENT_READ` 的用户打开 Agent 管理
- **THEN** 系统展示第一页 Agent，每行包含三个环境各自的当前生效版本标记

#### Scenario: 按状态与关键字筛选

- **WHEN** 用户选择状态为停用并在搜索框输入名称片段
- **THEN** 列表按服务端返回结果收敛，翻页后筛选条件保持不变

#### Scenario: 部分环境未发布

- **WHEN** 某 Agent 只在 DEV 发布过
- **THEN** 该行 DEV 展示版本号，TEST 与 PROD 展示未发布标记

#### Scenario: 空状态

- **WHEN** 本租户还没有任何 Agent
- **THEN** 列表展示空状态与新建引导，不显示报错

### Requirement: 创建入口与表单

新建 SHALL 采集编码、名称、描述、类型与标签，并允许在同一表单内选择可选的初始配置（模型、Prompt、工具与运行策略）。类型 SHALL 作为产品标签提供给使用者，本期仅提供聊天型，其运行方式固定为工具循环，界面 MUST NOT 提供运行方式选择项。编码 SHALL 在前端按后端规则前置校验；服务端返回冲突时 SHALL 在编码字段上提示，且保留已填内容。创建成功后 SHALL 进入该 Agent 的详情页。缺少 `AGENT_MANAGE` 的用户 SHALL NOT 看到新建入口，直接访问新建地址也不会提交写请求。

#### Scenario: 带初始配置创建

- **WHEN** 用户填写合法编码与名称并选择了一个模型、一个 Prompt 版本与工具
- **THEN** 系统创建 Agent 并生成版本 1，详情页展示该版本的配置

#### Scenario: 不带配置创建

- **WHEN** 用户只填写基本信息且未选择任何配置
- **THEN** 系统创建 Agent，详情页展示尚无版本的引导与配置入口

#### Scenario: 编码冲突

- **WHEN** 提交的编码在本租户已存在
- **THEN** 编码字段提示冲突，其余输入保持不变

#### Scenario: 只读用户

- **WHEN** 用户不具备 `AGENT_MANAGE`
- **THEN** 新建入口不可见，且不加载配置选择器

### Requirement: 复制入口

列表与详情 SHALL 为具备 `AGENT_MANAGE` 的用户提供复制入口。复制表单 SHALL 预填来源 Agent 的名称与编码加 `-copy` 后缀，编码 MUST 允许修改且必须唯一；表单 SHALL 明确说明将复制来源的最新配置作为新 Agent 的版本 1，且不复制发布状态与运行历史。复制成功后 SHALL 进入新 Agent 的详情页。

#### Scenario: 打开复制表单

- **WHEN** 用户在某个已有版本的 Agent 上选择复制
- **THEN** 表单预填名称与唯一编码候选，并说明复制的是最新配置

#### Scenario: 复制完成

- **WHEN** 用户以未被占用的编码提交复制
- **THEN** 新 Agent 出现在列表中，详情页展示由来源快照生成的版本 1，且三个环境均为未发布

#### Scenario: 复制空壳 Agent

- **WHEN** 用户复制一个尚无任何版本的 Agent
- **THEN** 新 Agent 被创建且没有版本，界面提示需要先完成配置

### Requirement: 生命周期操作的界面可用性

控制台 SHALL 仅展示对当前状态有效的操作：草稿 Agent 展示发布与删除，启用 Agent 展示停用，停用 Agent 展示启用与归档，归档 Agent 不展示任何写操作。按钮可用性 SHALL 由服务端返回的状态与权限集合共同决定；服务端拒绝操作时 SHALL 展示具体原因，并刷新为最新状态，而不是继续停留在失效的界面状态。

#### Scenario: 归档后只读

- **WHEN** 用户打开一个已归档 Agent 的详情页
- **THEN** 配置、版本与发布信息可见，但保存、发布、启停、删除入口均不可用

#### Scenario: 状态已变化

- **WHEN** 用户在另一位成员停用该 Agent 后点击启用
- **THEN** 界面展示操作结果与服务端原因，并刷新状态为停用

#### Scenario: 无发布权限

- **WHEN** 用户具备 `AGENT_READ` 与 `AGENT_MANAGE` 但不具备 `AGENT_PUBLISH`
- **THEN** 发布与回滚入口不可用，配置与启停入口仍可用

### Requirement: 详情页的信息聚合

Agent 详情页 SHALL 在首屏聚合展示身份信息、状态、三个环境各自当前生效的版本与发布时间、最近一次运行时间与最近一次调用的成功或失败结果。缺少配置或缺少发布时 SHALL 给出下一步引导，而不是只显示空字段。

#### Scenario: 已发布的 Agent

- **WHEN** 用户打开一个已在 PROD 发布的 Agent
- **THEN** 首屏可见 PROD 当前版本号、发布时间与最近一次调用结果

#### Scenario: 从未发布

- **WHEN** 用户打开一个只有草稿版本的 Agent
- **THEN** 首屏提示先发布到 DEV 进行验证
