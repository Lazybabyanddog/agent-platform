# Spec Delta

## MODIFIED Requirements

### Requirement: Agent 逻辑身份

系统 SHALL 维护 Agent 的逻辑身份，包含租户内唯一编码、名称、描述、类型、负责人、标签与状态。类型本期仅支持聊天型，其运行方式固定为工具循环，不作为独立可选项。逻辑身份本身不承载可执行配置。状态 SHALL 取值为草稿、启用、停用与归档：草稿表示尚未在任何环境成功发布，启用表示可被调用，停用表示被显式阻断调用，归档表示只读且不可调用。

#### Scenario: 创建 Agent

- **WHEN** 开发者在某租户下提交唯一编码与名称
- **THEN** 系统创建 Agent 并返回标识，其状态为草稿

#### Scenario: 编码冲突

- **WHEN** 同一租户下提交已存在的 Agent 编码
- **THEN** 系统拒绝创建并返回冲突错误

#### Scenario: 状态取值互斥

- **WHEN** 查询任意 Agent
- **THEN** 其状态为草稿、启用、停用与归档中的恰好一个

## ADDED Requirements

### Requirement: 创建时生成初始版本

创建 Agent 的请求 SHALL 接受可选的初始配置（模型、Prompt、工具与运行策略）。提供初始配置时，系统 MUST 在同一事务内生成版本 1；任一引用不可用或校验失败时整次创建 MUST 失败，不得留下无配置的 Agent 或半成品版本。未提供初始配置时，Agent 创建后没有任何版本，状态保持草稿。

#### Scenario: 创建并固化版本 1

- **WHEN** 开发者创建 Agent 时同时提交了可用的模型、Prompt 与工具引用
- **THEN** 系统在创建成功时即存在版本 1，其快照与提交的引用一致

#### Scenario: 初始配置含不可用引用

- **WHEN** 创建请求中的 Prompt 版本已被停用
- **THEN** 创建失败并指出该资源，租户下不出现该编码的 Agent

#### Scenario: 未提供初始配置

- **WHEN** 开发者只提交身份信息创建 Agent
- **THEN** 系统创建 Agent，版本列表为空，状态为草稿

### Requirement: 复制 Agent

复制 SHALL 创建新的逻辑身份，并 MUST 使用新的唯一编码。当来源 Agent 存在版本时，系统 SHALL 以其最新版本的快照作为新 Agent 的版本 1，快照内容与配置摘要 MUST 与来源对应版本一致。复制 MUST NOT 复制发布记录、发布指针、运行记录与统计数据。来源 Agent 没有版本时，复制 SHALL 只创建身份信息。

#### Scenario: 复制带版本的 Agent

- **WHEN** 管理员以新编码复制一个最新版本为 5 的 Agent
- **THEN** 新 Agent 存在版本 1，其快照与摘要等同来源版本 5，且三个环境均未发布

#### Scenario: 复制后的独立性

- **WHEN** 来源 Agent 在复制之后发布新版本或修改配置
- **THEN** 新 Agent 的版本与配置保持不变

#### Scenario: 复制空壳 Agent

- **WHEN** 管理员复制一个尚无版本的 Agent
- **THEN** 新 Agent 被创建且没有版本，状态为草稿

#### Scenario: 编码重复

- **WHEN** 复制时提交的编码在本租户已存在
- **THEN** 系统拒绝复制并返回冲突错误

### Requirement: 启用与停用

系统 SHALL 支持把已发布过的 Agent 在启用与停用之间切换。停用 SHALL 立即阻断该 Agent 在所有环境的新调用，但 MUST NOT 删除或移动发布记录与发布指针；重新启用 SHALL 恢复调用。从未在任何环境成功发布过的 Agent MUST NOT 可被启用。对已归档 Agent 的启用请求 SHALL 被拒绝。进行中的执行 SHALL 不受状态切换影响。发布新版本 SHALL NOT 自动解除停用状态。

#### Scenario: 停用阻断新调用

- **WHEN** 管理员停用一个启用中的 Agent 后发起调用
- **THEN** 调用失败关闭并返回 Agent 已停用的原因，发布指针保持不变

#### Scenario: 重新启用

- **WHEN** 管理员对停用的 Agent 执行启用后发起调用
- **THEN** 调用按当前发布指针继续执行

#### Scenario: 草稿不可启用

- **WHEN** 管理员尝试启用一个从未发布过的 Agent
- **THEN** 操作被拒绝并提示先发布到环境

#### Scenario: 停用期间发布新版本

- **WHEN** 管理员在停用状态下把新版本发布到 PROD
- **THEN** 发布记录与指针更新，但 Agent 仍保持停用，直到显式启用

### Requirement: 归档与删除

系统 SHALL 支持归档 Agent，归档后该 Agent 只读，MUST NOT 被发布、启用或调用。系统 SHALL 仅在 Agent 从未产生版本、发布记录与运行记录时允许删除；其余情况 MUST 拒绝删除并提示改用归档。删除 SHALL 释放编码以便重新创建。归档与删除 SHALL 产生审计事件。

#### Scenario: 删除空壳 Agent

- **WHEN** 管理员删除一个从未生成版本、从未发布、从未被调用的 Agent
- **THEN** Agent 被删除，同一编码可再次用于创建

#### Scenario: 拒绝删除有历史的 Agent

- **WHEN** 管理员删除一个存在历史版本的 Agent
- **THEN** 系统拒绝删除并提示改为归档

#### Scenario: 归档后不可调用

- **WHEN** 调用方调用已归档的 Agent
- **THEN** 调用失败关闭并返回 Agent 已归档的原因

#### Scenario: 归档保留编码

- **WHEN** 管理员归档一个 Agent 后尝试用相同编码创建新 Agent
- **THEN** 创建因编码冲突被拒绝

### Requirement: 当前生效版本聚合

系统 SHALL 支持在查询单个 Agent 时返回各环境当前发布指针指向的版本号与发布时间，未发布的环境 SHALL 被明确标注。聚合结果 SHALL 与发布记录保持一致，不得返回指向不存在的发布记录的指针。

#### Scenario: 查询三个环境的生效版本

- **WHEN** 管理员查询一个在 DEV 与 PROD 已发布、TEST 未发布的 Agent
- **THEN** 返回 DEV 与 PROD 的版本号与发布时间，并标注 TEST 未发布

#### Scenario: 回滚后的聚合

- **WHEN** 某环境回滚到历史发布记录后再次查询
- **THEN** 该环境展示回滚后的版本号

### Requirement: 发布前体检

系统 SHALL 在发布前提供该版本的可用性报告，至少包含每个绑定资源的可用性与最近一次预览的结果摘要。存在不可用引用时发布 MUST 仍被拒绝并指出具体资源。体检报告 SHALL 可重复查询且不改变发布状态。

#### Scenario: 体检通过

- **WHEN** 管理员对引用全部可用的版本发起体检
- **THEN** 报告列出各资源可用，发布入口可用

#### Scenario: 体检发现问题

- **WHEN** 版本引用的工具对当前租户已撤销授权
- **THEN** 报告指出该工具，发布入口不可用

#### Scenario: 体检不产生副作用

- **WHEN** 管理员多次查询体检报告
- **THEN** 不产生发布记录，也不移动发布指针
