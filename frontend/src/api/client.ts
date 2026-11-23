import type { components, paths } from './schema'

/** 由后端 OpenAPI 契约生成的类型别名；后端改字段时这里会立即编译失败。 */
export type Schema<K extends keyof components['schemas']> = components['schemas'][K]
export type Operation<P extends keyof paths, M extends keyof paths[P]> = paths[P][M]
export type JsonBody<Op> = Op extends { content: { 'application/json': infer Body } } ? Body : never
export type RequestBodyOf<Op> = Op extends {
  requestBody: { content: { 'application/json': infer Body } }
}
  ? Body
  : never

export interface ApiErrorDetail {
  field?: string
  reason?: string
  type?: string
}

export class ApiError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
    readonly details: ApiErrorDetail[] = [],
    readonly traceId?: string,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 后端错误码 → 用户可读文案。未知错误码回落到后端 message。 */
const ERROR_MESSAGES: Record<string, string> = {
  UNAUTHORIZED: '登录已失效，请重新登录',
  FORBIDDEN: '当前账号没有该操作权限',
  NOT_FOUND: '资源不存在或已被删除',
  VALIDATION_FAILED: '请求参数校验失败',
  RESOURCE_UNAVAILABLE: '依赖资源不可用，请检查模型 / Prompt / 工具状态',
  CONFLICT: '资源已被占用或状态冲突',
  RATE_LIMITED: '请求过于频繁，请稍后重试',
  MODEL_ERROR: '模型调用失败',
  TOOL_ERROR: '工具调用失败',
  INTERNAL_ERROR: '服务内部错误，请携带 trace_id 联系管理员',
  HTTP_ERROR: '请求失败',
}

export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const text = ERROR_MESSAGES[error.code] ?? error.message
    const detail = error.details
      .map((item) => [item.field, item.reason].filter(Boolean).join(': '))
      .filter(Boolean)
      .join('；')
    const suffix = detail ? `（${detail}）` : ''
    const trace = error.traceId ? ` trace_id=${error.traceId}` : ''
    return `${text}${suffix}${trace}`
  }
  return error instanceof Error ? error.message : String(error)
}

const BASE_URL = '/api/v1'

let accessToken: string | null = null

export function setAccessToken(token: string | null): void {
  accessToken = token
  if (token) {
    localStorage.setItem('ap_token', token)
  } else {
    localStorage.removeItem('ap_token')
  }
}

export function getAccessToken(): string | null {
  if (accessToken) return accessToken
  accessToken = localStorage.getItem('ap_token')
  return accessToken
}

/** trace_id 观察者：任意响应都会把 X-Trace-Id 推给订阅方，用于界面统一展示。 */
let lastTraceId: string | null = null
const traceListeners = new Set<(traceId: string) => void>()

export function onTraceId(listener: (traceId: string) => void): () => void {
  traceListeners.add(listener)
  if (lastTraceId) listener(lastTraceId)
  return () => traceListeners.delete(listener)
}

export function getLastTraceId(): string | null {
  return lastTraceId
}

function publishTrace(response: Response, payload: unknown): void {
  const fromBody =
    payload && typeof payload === 'object' && 'error' in payload
      ? (payload as { error?: { trace_id?: string } }).error?.trace_id
      : undefined
  const traceId = response.headers.get('X-Trace-Id') ?? fromBody ?? null
  if (!traceId || traceId === lastTraceId) return
  lastTraceId = traceId
  traceListeners.forEach((listener) => listener(traceId))
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
  signal?: AbortSignal
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = new URL(`${BASE_URL}${path}`, window.location.origin)
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined && value !== null && value !== '') {
        url.searchParams.set(key, String(value))
      }
    }
  }
  return url.pathname + url.search
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  const token = getAccessToken()
  if (token) headers.Authorization = `Bearer ${token}`
  if (options.body !== undefined) headers['Content-Type'] = 'application/json'

  const response = await fetch(buildUrl(path, options.query), {
    method: options.method ?? 'GET',
    headers,
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    signal: options.signal,
  })

  if (response.status === 204) {
    publishTrace(response, null)
    return undefined as T
  }

  const payload = await response.json().catch(() => null)
  publishTrace(response, payload)

  if (!response.ok) {
    const error = (
      payload as {
        error?: {
          code?: string
          message?: string
          details?: ApiErrorDetail[]
          trace_id?: string
        }
      } | null
    )?.error
    throw new ApiError(
      error?.code ?? 'HTTP_ERROR',
      error?.message ?? response.statusText,
      response.status,
      error?.details ?? [],
      error?.trace_id ?? response.headers.get('X-Trace-Id') ?? undefined,
    )
  }

  return payload as T
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query']) => request<T>(path, { query }),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', body }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
