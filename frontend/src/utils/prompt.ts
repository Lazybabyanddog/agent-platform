/**
 * Prompt 编辑器的本地模型：把后端返回的 `Record<string, unknown>` 收敛成强类型草稿，
 * 并提供占位符解析、规范化签名（用于脏检测）等纯函数。所有规则与后端保持一致：
 * - role 仅 system / user / assistant
 * - 变量名 `[A-Za-z_][A-Za-z0-9_]*`，类型 string / number / integer / boolean
 * - 占位符语法 `{{ name }}`
 */
import type { PromptVersion } from '@/api/endpoints'

export const PROMPT_ROLES = ['system', 'user', 'assistant'] as const
export type PromptRole = (typeof PROMPT_ROLES)[number]

export const PROMPT_VARIABLE_TYPES = ['string', 'number', 'integer', 'boolean'] as const
export type PromptVariableType = (typeof PROMPT_VARIABLE_TYPES)[number]

export interface PromptMessage {
  role: PromptRole
  content: string
}

export interface PromptVariable {
  name: string
  type: PromptVariableType
  required: boolean
  /** 未设置时不带该键，避免与 `default: ''` 混淆。 */
  default?: unknown
}

export const VARIABLE_TYPE_LABELS: Record<PromptVariableType, string> = {
  string: '字符串',
  number: '数值',
  integer: '整数',
  boolean: '布尔',
}

export const PROMPT_CODE_PATTERN = /^[A-Za-z0-9_.-]{1,64}$/
export const VARIABLE_NAME_PATTERN = /^[A-Za-z_][A-Za-z0-9_]*$/

const PLACEHOLDER_SOURCE = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isPromptRole(value: unknown): value is PromptRole {
  return typeof value === 'string' && (PROMPT_ROLES as readonly string[]).includes(value)
}

export function isVariableType(value: unknown): value is PromptVariableType {
  return typeof value === 'string' && (PROMPT_VARIABLE_TYPES as readonly string[]).includes(value)
}

export function toMessage(value: unknown): PromptMessage | null {
  if (!isRecord(value)) return null
  const content = value.content
  return {
    role: isPromptRole(value.role) ? value.role : 'user',
    content: typeof content === 'string' ? content : '',
  }
}

export function toVariable(value: unknown): PromptVariable | null {
  if (!isRecord(value)) return null
  const name = typeof value.name === 'string' ? value.name : ''
  const variable: PromptVariable = {
    name,
    type: isVariableType(value.type) ? value.type : 'string',
    required: value.required === true,
  }
  if (value.default !== undefined) variable.default = value.default
  return variable
}

export function toMessages(raw: Record<string, unknown>[]): PromptMessage[] {
  const messages: PromptMessage[] = []
  for (const item of raw) {
    const message = toMessage(item)
    if (message) messages.push(message)
  }
  return messages
}

export function toVariables(raw: Record<string, unknown>[]): PromptVariable[] {
  const variables: PromptVariable[] = []
  for (const item of raw) {
    const variable = toVariable(item)
    if (variable) variables.push(variable)
  }
  return variables
}

/** 不可变地复制，避免草稿与基线共享引用。 */
export function cloneMessages(messages: PromptMessage[]): PromptMessage[] {
  return messages.map((message) => ({ ...message }))
}

export function cloneVariables(variables: PromptVariable[]): PromptVariable[] {
  return variables.map((variable) => ({ ...variable }))
}

export function toMessagePayload(messages: PromptMessage[]): Record<string, unknown>[] {
  return messages.map((message) => ({ role: message.role, content: message.content }))
}

export function toVariablePayload(variables: PromptVariable[]): Record<string, unknown>[] {
  return variables.map((variable) => {
    const payload: Record<string, unknown> = {
      name: variable.name,
      type: variable.type,
      required: variable.required,
    }
    if (variable.default !== undefined) payload.default = variable.default
    return payload
  })
}

export function hasDefault(variable: PromptVariable): boolean {
  return variable.default !== undefined
}

export function defaultForType(type: PromptVariableType): unknown {
  switch (type) {
    case 'number':
    case 'integer':
      return 0
    case 'boolean':
      return false
    default:
      return ''
  }
}

export interface ContentSegment {
  text: string
  /** 非空表示这一段是占位符，值为变量名。 */
  placeholder: string | null
}

/** 把消息内容切成「普通文本 / 占位符」片段，用于轻量高亮。 */
export function splitPlaceholders(content: string): ContentSegment[] {
  const segments: ContentSegment[] = []
  const pattern = /\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}/g
  let cursor = 0
  let match: RegExpExecArray | null = pattern.exec(content)
  while (match) {
    if (match.index > cursor) {
      segments.push({ text: content.slice(cursor, match.index), placeholder: null })
    }
    segments.push({ text: match[0], placeholder: match[1] ?? null })
    cursor = match.index + match[0].length
    match = pattern.exec(content)
  }
  if (cursor < content.length) {
    segments.push({ text: content.slice(cursor), placeholder: null })
  }
  return segments
}

/** 提取内容里出现的占位符名称，按出现顺序去重。 */
export function placeholderNames(content: string): string[] {
  const names: string[] = []
  for (const match of content.matchAll(PLACEHOLDER_SOURCE)) {
    const name = match[1]
    if (name && !names.includes(name)) names.push(name)
  }
  return names
}

export interface UndeclaredPlaceholder {
  messageIndex: number
  name: string
}

/** 模板中出现但变量表未声明的占位符（保存仍由后端裁决）。 */
export function undeclaredPlaceholders(
  messages: PromptMessage[],
  declared: string[],
): UndeclaredPlaceholder[] {
  const found: UndeclaredPlaceholder[] = []
  messages.forEach((message, messageIndex) => {
    for (const name of placeholderNames(message.content)) {
      if (!declared.includes(name)) found.push({ messageIndex, name })
    }
  })
  return found
}

function normalizeVariable(variable: PromptVariable): Record<string, unknown> {
  const normalized: Record<string, unknown> = {
    name: variable.name,
    type: variable.type,
    required: variable.required,
  }
  if (variable.default !== undefined) normalized.default = variable.default
  return normalized
}

/**
 * 脏检测签名：变量按 name 排序后序列化，避免表达式顺序造成假差异；
 * 不依赖后端 `config_hash`（复算需要与后端序列化完全一致，过于脆弱）。
 */
export function draftSignature(messages: PromptMessage[], variables: PromptVariable[]): string {
  const normalizedVariables = variables
    .map(normalizeVariable)
    .sort((left, right) => String(left.name).localeCompare(String(right.name)))
  return JSON.stringify({
    messages: messages.map((message) => ({ role: message.role, content: message.content })),
    variables: normalizedVariables,
  })
}

export function versionSignature(version: PromptVersion | null): string | null {
  if (!version) return null
  return draftSignature(toMessages(version.messages), toVariables(version.variables))
}

export function hasDraftContent(messages: PromptMessage[], variables: PromptVariable[]): boolean {
  return (
    messages.some((message) => message.content.trim() !== '') ||
    variables.some((variable) => variable.name.trim() !== '')
  )
}

/** 从后端 `details[].field` 收集字段级错误，键形如 `messages.1.content`。 */
export function fieldErrorsFrom(
  details: { field?: string; reason?: string }[],
): Record<string, string> {
  const errors: Record<string, string> = {}
  for (const detail of details) {
    if (!detail.field) continue
    errors[detail.field] = detail.reason ?? '校验未通过'
  }
  return errors
}
