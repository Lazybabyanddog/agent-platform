/**
 * 模型参数元数据与前端校验。
 *
 * 键清单与后端 `SUPPORTED_MODEL_PARAMS` 一一对应（新增参数时两处一起改）；
 * 取值范围以模型的 `param_template` 为准，这里的 min/max 只用于展示兜底与
 * 步长选择，真正的门禁仍在后端 `normalize_params`。
 */

export const SUPPORTED_MODEL_PARAMS = [
  'temperature',
  'max_tokens',
  'max_completion_tokens',
  'top_p',
  'presence_penalty',
  'frequency_penalty',
  'stop',
  'seed',
  'timeout',
] as const

export type SupportedModelParam = (typeof SUPPORTED_MODEL_PARAMS)[number]
export type ModelParamType = 'number' | 'integer' | 'string' | 'boolean'

export interface ModelParamRule {
  type?: ModelParamType
  min?: number
  max?: number
  default?: unknown
  enum?: unknown[]
  required?: boolean
  description?: string
}

export type ModelParamTemplate = Record<string, ModelParamRule>

export interface ModelParamMeta {
  label: string
  hint: string
  /** 数值控件的推荐步长；模板未声明时使用。 */
  step?: number
}

export const MODEL_PARAM_META: Record<SupportedModelParam, ModelParamMeta> = {
  temperature: { label: '随机性 temperature', hint: '0 ~ 2，越大越发散', step: 0.1 },
  max_tokens: { label: '最大输出 token 数', hint: '正整数，不超过模型上下文窗口' },
  max_completion_tokens: {
    label: '最大输出 token 数（新键）',
    hint: '正整数，部分上游使用该键替代 max_tokens',
  },
  top_p: { label: '核采样 top_p', hint: '0 ~ 1，与 temperature 二选一调整', step: 0.05 },
  presence_penalty: {
    label: '话题新颖度 presence_penalty',
    hint: '-2 ~ 2，正值鼓励引入新话题',
    step: 0.1,
  },
  frequency_penalty: {
    label: '重复抑制 frequency_penalty',
    hint: '-2 ~ 2，正值抑制重复用词',
    step: 0.1,
  },
  stop: { label: '停止词 stop', hint: '命中后截断输出；单个字符串' },
  seed: { label: '随机种子 seed', hint: '整数，便于上游复现相同输出' },
  timeout: { label: '单次调用超时 timeout（秒）', hint: '实际取该值与运行预算的较小者' },
}

export function isSupportedModelParam(name: string): name is SupportedModelParam {
  return (SUPPORTED_MODEL_PARAMS as readonly string[]).includes(name)
}

export function paramLabel(name: string): string {
  return isSupportedModelParam(name) ? MODEL_PARAM_META[name].label : name
}

export function paramHint(name: string, rule?: ModelParamRule | null): string {
  const custom = rule?.description?.trim()
  if (custom) return custom
  return isSupportedModelParam(name) ? MODEL_PARAM_META[name].hint : ''
}

export function paramStep(name: string, rule?: ModelParamRule | null): number {
  if (rule?.type === 'integer') return 1
  if (isSupportedModelParam(name)) return MODEL_PARAM_META[name].step ?? 1
  return 1
}

/** 模板可能来自任意 JSON；只保留规则对象，避免脏数据让渲染报错。 */
export function normalizeTemplate(template: Record<string, unknown> | null | undefined): ModelParamTemplate {
  const result: ModelParamTemplate = {}
  for (const [name, rule] of Object.entries(template ?? {})) {
    if (rule && typeof rule === 'object' && !Array.isArray(rule)) {
      result[name] = rule as ModelParamRule
    }
  }
  return result
}

export function hasDefault(rule?: ModelParamRule | null): boolean {
  return Boolean(rule) && Object.prototype.hasOwnProperty.call(rule, 'default')
}

export function formatParamValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === null || value === undefined) return '—'
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

export function isSameParamValue(a: unknown, b: unknown): boolean {
  if (a === b) return true
  if (typeof a !== typeof b) return false
  if (a && b && typeof a === 'object') {
    try {
      return JSON.stringify(a) === JSON.stringify(b)
    } catch {
      return false
    }
  }
  return false
}

/**
 * 只保留需要覆盖模型默认值的项：与模板 default 相同的值视为“继承”，不提交。
 * 模板为空时原样返回，避免在模板尚未加载时丢掉用户已填的内容。
 */
export function pickOverrides(
  values: Record<string, unknown>,
  template: ModelParamTemplate,
): Record<string, unknown> {
  if (!Object.keys(template).length) return { ...values }
  const result: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(values)) {
    const rule = template[name]
    if (rule && hasDefault(rule) && isSameParamValue(rule.default, value)) continue
    result[name] = value
  }
  return result
}

/** 丢掉当前模型模板未声明的键，防止切换模型后提交必然失败的参数。 */
export function pruneToTemplate(
  values: Record<string, unknown>,
  template: ModelParamTemplate,
): Record<string, unknown> {
  if (!Object.keys(template).length) return { ...values }
  const result: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(values)) {
    if (template[name]) result[name] = value
  }
  return result
}

function typeError(type: ModelParamType): string {
  const text: Record<ModelParamType, string> = {
    number: '必须是数字',
    integer: '必须是整数',
    string: '必须是字符串',
    boolean: '必须是 true / false',
  }
  return text[type]
}

/**
 * 按模板校验参数取值，字段名与后端保持一致（`params.<name>`），
 * 便于把本地校验结果与接口返回的错误合并展示。
 */
export function validateParamValues(
  template: ModelParamTemplate,
  values: Record<string, unknown>,
): Record<string, string> {
  const issues: Record<string, string> = {}
  const templateEmpty = Object.keys(template).length === 0

  for (const [name, value] of Object.entries(values)) {
    if (templateEmpty || !template[name]) {
      issues[`params.${name}`] = `参数 ${name} 未在当前模型的参数模板中声明`
      continue
    }
    const rule = template[name] ?? {}
    const type = rule.type ?? 'number'
    if (type === 'number') {
      if (typeof value !== 'number' || !Number.isFinite(value)) {
        issues[`params.${name}`] = `参数 ${name} ${typeError(type)}`
        continue
      }
    } else if (type === 'integer') {
      if (typeof value !== 'number' || !Number.isInteger(value)) {
        issues[`params.${name}`] = `参数 ${name} ${typeError(type)}`
        continue
      }
    } else if (type === 'string') {
      if (typeof value !== 'string') {
        issues[`params.${name}`] = `参数 ${name} ${typeError(type)}`
        continue
      }
    } else if (type === 'boolean') {
      if (typeof value !== 'boolean') {
        issues[`params.${name}`] = `参数 ${name} ${typeError(type)}`
        continue
      }
    }

    if (Array.isArray(rule.enum) && rule.enum.length && !rule.enum.some((item) => item === value)) {
      issues[`params.${name}`] = `参数 ${name} 只能是 ${rule.enum.map(formatParamValue).join(' / ')}`
      continue
    }
    if (typeof value === 'number') {
      if (typeof rule.min === 'number' && value < rule.min) {
        issues[`params.${name}`] = `参数 ${name} 不能小于 ${rule.min}`
        continue
      }
      if (typeof rule.max === 'number' && value > rule.max) {
        issues[`params.${name}`] = `参数 ${name} 不能大于 ${rule.max}`
        continue
      }
    }
  }

  for (const [name, rule] of Object.entries(template)) {
    if (rule.required && !Object.prototype.hasOwnProperty.call(values, name)) {
      issues[`params.${name}`] = `参数 ${name} 为必填项`
    }
  }

  return issues
}
