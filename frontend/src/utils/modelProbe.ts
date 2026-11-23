import type { ModelProbe, ProbeRecord } from '@/api/endpoints'

export interface VerdictMeta {
  label: string
  hint: string
  tag: 'success' | 'info' | 'warning' | 'danger'
  tone: 'success' | 'info' | 'warning' | 'danger'
}

/** 后端结论 → 界面文案。界面只做映射，不根据 status_code 等字段二次推断。 */
const VERDICTS: Record<string, VerdictMeta> = {
  OK: {
    label: '连通',
    hint: '上游可达、凭据有效，且模型清单包含目标模型。',
    tag: 'success',
    tone: 'success',
  },
  MODEL_NOT_FOUND: {
    label: '模型不在清单',
    hint: '上游可达、凭据有效，但返回的模型清单不含该模型，请核对模型标识。',
    tag: 'danger',
    tone: 'danger',
  },
  CREDENTIAL_OK_MODEL_UNVERIFIED: {
    label: '凭据有效 · 模型未验证',
    hint: '请求成功但无法解析模型清单，只能确认凭据可用，无法确认模型是否存在。',
    tag: 'warning',
    tone: 'warning',
  },
  MODEL_PROBE_AUTH_FAILED: {
    label: '凭据无效',
    hint: '上游返回 401/403，请检查凭据引用与密钥内容。',
    tag: 'danger',
    tone: 'danger',
  },
  MODEL_PROBE_TIMEOUT: {
    label: '超时',
    hint: '探测在平台超时时间内未返回，请检查网络与上游服务状态。',
    tag: 'warning',
    tone: 'warning',
  },
  MODEL_PROBE_UNREACHABLE: {
    label: '无法连通',
    hint: '连接失败或上游返回其它 HTTP 错误，请核对 Base URL 与网关配置。',
    tag: 'danger',
    tone: 'danger',
  },
}

export function verdictMeta(verdict: string | null | undefined): VerdictMeta {
  if (verdict && VERDICTS[verdict]) return VERDICTS[verdict]
  return {
    label: verdict || '未探测',
    hint: '暂无可用结论，可先执行一次探测。',
    tag: 'info',
    tone: 'info',
  }
}

export type ProbeLike = Pick<
  ProbeRecord,
  'verdict' | 'observed_models' | 'observed_model_name' | 'latency_ms' | 'status_code'
>

/** 上游清单里是否命中了目标模型（后端已给出 observed_model_name，此处用于高亮）。 */
export function listedModel(record: ProbeLike): string | null {
  return record.observed_model_name ?? null
}

export function probeSummary(result: ModelProbe): string {
  const meta = verdictMeta(result.verdict)
  const parts = [meta.label]
  parts.push(`${result.latency_ms} ms`)
  if (result.status_code) parts.push(`HTTP ${result.status_code}`)
  if (result.observed_model_name) parts.push(`命中 ${result.observed_model_name}`)
  return parts.join(' · ')
}

export function formatProbeTime(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString()
}
