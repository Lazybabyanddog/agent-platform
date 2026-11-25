# Spec Delta

## ADDED Requirements

### Requirement: 调试台执行与版本声明一致

调试台按所选 Agent 的最新版本快照发起预览时 SHALL 携带该版本的运行时上下文声明，使调试执行与正式调用在同一份上下文下进行；切换 Agent 或环境后 SHALL 按新版本重新装载声明，MUST NOT 沿用上一个 Agent 的声明。面板 SHALL 标出本次执行注入了哪些上下文项；未声明时 SHALL 明确显示未注入，而不是让使用者以为能力未生效。

#### Scenario: 版本已声明时调试

- **WHEN** 所选 Agent 的最新版本声明了「当前时间」与「时区」，用户在调试台发送一条消息
- **THEN** 该轮执行注入了这两项事实，面板标出已注入的上下文项

#### Scenario: 未声明时调试

- **WHEN** 所选 Agent 的最新版本没有任何运行时上下文声明
- **THEN** 面板显示本次未注入上下文，执行正常完成

#### Scenario: 切换 Agent 后重新装载

- **WHEN** 用户把调试台从声明了上下文的 Agent 切到未声明的 Agent
- **THEN** 新一轮执行不带任何上下文，面板随之更新
