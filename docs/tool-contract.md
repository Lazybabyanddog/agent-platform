# 工具契约：让模型看得懂，也改得对

平台把工具分成两套契约，二者解决的问题不同：

| 契约 | 字段 | 谁在用 | 能否改变结构 |
| --- | --- | --- | --- |
| 调用契约 | `input_schema` / `output_schema` | 平台校验入参与执行结果 | 可以，决定真实协议 |
| 模型可见契约 | `model_schema` / `description` / `returns` | 模型看工具卡 | 不可以，只能加注解 |

`model_schema` 与 `input_schema` 必须结构等价（属性名、类型、必填项、嵌套层级一致），
只有 `description`、`format`、`examples`、`title` 这类注解可以不同。
结构不一致会拒绝保存，避免「模型看到的参数」和「平台实际校验的参数」分叉。

## 保存工具版本时的校验与提示

- 版本未写 `description` 时继承工具级描述；两者都为空返回 `422 spec.description`。
- 日期类参数（名称含 `date` / `time` / `day` 等）缺少 `format` 时不阻塞保存，
  但在响应 `warnings` 中提示 `spec.input_schema.properties.<name>`：
  模型只能靠参数名猜格式。
- 所有版本字段不可修改，只能新增版本。

工具卡示例：

```json
{
  "description": "查询指定城市某日天气",
  "input_schema": {
    "type": "object",
    "properties": {
      "city": {"type": "string", "description": "中文城市名，如 武汉"},
      "start_date": {"type": "string", "format": "date", "examples": ["2026-11-25"]}
    },
    "required": ["city", "start_date"]
  },
  "returns": {"terminal_statuses": ["no_data"]}
}
```

## 在控制台维护模型可见契约

工具详情页的契约编辑器把「结构」和「注解」分开：结构只能来自 `input_schema`，
作者只补注解。因此页面上产出的 `model_schema` 永远是结构等价的，不会被保存校验拒绝。

操作顺序：

1. **用途描述**：最上面的输入框写进 `spec.description`。留空则继承工具级描述；
   两处都为空时保存被拒绝，错误直接定位到该输入框。
2. **入参 Schema**：按真实协议写 JSON，属性的名称、类型、`required`、`items` 决定模型侧结构。
3. **模型可见入参契约**：每个参数一行，填 `description`、`title`、`format`。
   这些值叠加到由 `input_schema` 派生的骨架上生成 `model_schema`；
   三列全空的参数不留注解，所有参数都空时不写该键（等同不声明）。
4. **业务终态**：用标签输入框维护 `returns.terminal_statuses`，留空则删除 `returns`。

限制与边界：

- 结构类字段（`type`、`required`、`properties`、`items`、`anyOf`/`oneOf`/`allOf`）在页面上不可改，
  改它们要改「入参 Schema」。
- 参数含嵌套对象或组合分支时，页面不逐参数编辑注解，只提示通过接口维护 `model_schema`：
  派生不出结构一致的骨架，硬编辑只会产出被拒绝的契约。
- 保存成功但需要作者注意的问题走 `warnings`：缺少 `format` 的日期参数会回填到对应参数行，
  其余无法定位的提示汇总在编辑器顶部。它们不阻塞保存，与红色校验错误分开显示。

## 返回语义

`returns.terminal_statuses` 声明「业务终态」。当工具成功返回且结果落在这些状态里，
平台把它当作正常结束而不是错误：模型可以据此回答「没有数据」，
但**不会**因为没拿到数据就反复重试。

## 失败分类与纠错

失败结果分两类：可恢复的写回模型让它改，不可恢复的直接终止执行。

| 错误码 | 可恢复 | 模型该做什么 | 平台动作 |
| --- | --- | --- | --- |
| `INPUT_INVALID` | 是 | 按字段原因修正参数后重试 | 信封带上 `fields` |
| `RESOURCE_UNAVAILABLE` | 是 | 换资源或换查询条件 | 记录原因 |
| `TOOL_TIMEOUT` | 是 | 缩小查询范围或稍后重试 | 记录原因 |
| `TOOL_HTTP_ERROR` | 是 | 判断是否临时故障 | 记录原因 |
| `TOOL_MCP_ERROR` | 是 | 调整调用参数 | 远端摘要只进 `details` |
| `TOOL_OUTPUT_INVALID` | 否 | — | 终止执行（`TOOL_ERROR`） |
| `HIGH_RISK_BLOCKED` | 否 | — | 终止执行（`TOOL_ERROR`） |

默认值是不可恢复：新增错误码必须先想清楚是否能被模型修正，避免「可重试」被静默扩大。

失败回灌给模型的是结构化信封，而不是一段自然语言：

```json
{
  "status": "error",
  "recoverable": true,
  "code": "INPUT_INVALID",
  "message": "start_date: expected YYYY-MM-DD",
  "fields": [{"field": "start_date", "reason": "expected YYYY-MM-DD"}],
  "hint": "修正参数后可以重新调用。"
}
```

字段原因会同时进入 `message`：模型只读正文，原因留在 `details` 里等于没说。

同一个工具用完全相同的参数再次调用时，平台不再真的执行，
直接回 `status = "repeated_failure"`，避免模型原地打转。

## 观测

每次 LLM 调用的 `llm_call` 记录都带 `tool_count`，`request_body.tools` 保留工具卡摘要
（名称、描述、参数 schema）。排查「模型为什么没调工具」时，先看工具卡是否真的发出去了。
