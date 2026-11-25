/**
 * 与后端 OpenAPI 契约一一对应的调用封装。
 * 所有请求/响应类型都来自 `schema.d.ts`，不手写接口模型。
 */
import { api, type Operation, type RequestBodyOf, type Schema } from './client'

export type Agent = Schema<'AgentResponse'>
export type AgentPage = Schema<'AgentListResponse'>
export type AgentConfigInput = Schema<'AgentConfigRequest'>
export type AgentVersionCreateInput = Schema<'AgentVersionCreateRequest'>
export type AgentEnvironmentState = Schema<'AgentEnvironmentState'>
export type AgentStatus = Schema<'AgentStatus'>
export type AgentType = Schema<'AgentType'>
export type AgentVersion = Schema<'AgentVersionResponse'>
export type AgentReadiness = Schema<'AgentReadinessResponse'>
export type BindingReadiness = Schema<'BindingReadiness'>
export type AgentRelease = Schema<'AgentReleaseResponse'>
export type AgentPointer = Schema<'AgentPointerResponse'>
export type AgentVersionDiff = Schema<'AgentVersionDiffResponse'>
export type Environment = Schema<'Environment'>
export type InvokeRequest = Schema<'InvokeRequest'>
export type InvokeResponse = Schema<'InvokeResponse'>
export type PreviewRequest = Schema<'PreviewRequest'>
export type PreviewResponse = Schema<'PreviewResponse'>
export type RunKind = Schema<'RunKind'>
export type SessionItem = Schema<'SessionItemResponse'>
export type SessionPage = Schema<'SessionPageResponse'>
export type SessionHistory = Schema<'SessionHistoryResponse'>
export type SessionHistoryTurn = Schema<'SessionHistoryTurnResponse'>
export type SessionHistoryTool = Schema<'SessionHistoryToolResponse'>
export type SessionHistoryMessage = Schema<'SessionHistoryMessageResponse'>
export type RunSummaryItem = Schema<'RunSummaryItemResponse'>
export type RunPage = Schema<'RunPageResponse'>
export type RunMetrics = Schema<'RunMetricsResponse'>
export type RunTrace = Schema<'RunTraceResponse'>
export type TraceEntry = Schema<'TraceEntryResponse'>
export type LoginResponse = Schema<'LoginResponse'>
export type MeResponse = Schema<'MeResponse'>
export type Model = Schema<'ModelResponse'>
export type ModelCreateInput = Schema<'ModelCreateRequest'>
export type ModelUpdateInput = Schema<'ModelUpdateRequest'>
export type Provider = Schema<'ProviderResponse'>
export type ProviderCreateInput = Schema<'ProviderCreateRequest'>
export type ProviderUpdateInput = Schema<'ProviderUpdateRequest'>
export type ModelProbe = Schema<'ProbeResponse'>
export type ProbeRecord = Schema<'ProbeRecordResponse'>
/** 结论取值由后端固定为六种字符串；前端只做展示映射，不做二次推断。 */
export type ProbeVerdict = ModelProbe['verdict']
export type Prompt = Schema<'PromptResponse'>
export type PromptStatus = Schema<'PromptStatus'>
export type PromptVersion = Schema<'PromptVersionResponse'>
export type PromptDiff = Schema<'PromptDiffResponse'>
export type PromptRenderResponse = Schema<'PromptRenderResponse'>
export type Tool = Schema<'ToolResponse'>
export type ToolVersion = Schema<'ToolVersionResponse'>
export type ToolGrant = Schema<'ToolGrantResponse'>
export type ToolExecution = Schema<'ToolExecuteResponse'>
export type ToolCallOverview = Schema<'ToolCallOverviewResponse'>
export type ToolCallItem = Schema<'ToolCallItemResponse'>
export type RiskLevel = Schema<'RiskLevel'>
export type ToolStatus = Schema<'ToolStatus'>
export type ToolType = Schema<'ToolType'>
export type McpProbeVerdict = Schema<'McpProbeVerdict'>
export type McpProbeTool = Schema<'McpProbeToolResponse'>
export type McpProbeResult = Schema<'McpProbeResponse'>

export const ENVIRONMENTS: Environment[] = ['DEV', 'TEST', 'PROD']

type LoginBody = RequestBodyOf<Operation<'/api/v1/auth/login', 'post'>>
type CreateAgentBody = RequestBodyOf<Operation<'/api/v1/agents', 'post'>>
type CopyAgentBody = RequestBodyOf<Operation<'/api/v1/agents/{agent_id}/copy', 'post'>>
type CreateAgentVersionBody = RequestBodyOf<
  Operation<'/api/v1/agents/{agent_id}/versions', 'post'>
>
type PreviewBody = RequestBodyOf<Operation<'/api/v1/agents/{agent_id}/preview', 'post'>>
type InvokeBody = RequestBodyOf<Operation<'/api/v1/agents/{agent_id}/invoke', 'post'>>
type ReleaseBody = RequestBodyOf<Operation<'/api/v1/agents/{agent_id}/releases', 'post'>>
type RollbackBody = RequestBodyOf<Operation<'/api/v1/agents/{agent_id}/rollback', 'post'>>
type CreatePromptBody = RequestBodyOf<Operation<'/api/v1/prompts', 'post'>>
type CreatePromptVersionBody = RequestBodyOf<
  Operation<'/api/v1/prompts/{prompt_id}/versions', 'post'>
>
type RenderPromptBody = RequestBodyOf<
  Operation<'/api/v1/prompt-versions/{version_id}/render', 'post'>
>
type CreateProviderBody = RequestBodyOf<Operation<'/api/v1/model-providers', 'post'>>
type UpdateProviderBody = RequestBodyOf<
  Operation<'/api/v1/model-providers/{provider_id}', 'patch'>
>
type CreateModelBody = RequestBodyOf<Operation<'/api/v1/models', 'post'>>
type UpdateModelBody = RequestBodyOf<Operation<'/api/v1/models/{model_id}', 'patch'>>
type UpdateModelStatusBody = RequestBodyOf<
  Operation<'/api/v1/models/{model_id}/status', 'patch'>
>
type CreateToolBody = RequestBodyOf<Operation<'/api/v1/tools', 'post'>>
type ProbeMcpBody = RequestBodyOf<Operation<'/api/v1/tools/probe-mcp', 'post'>>
type CreateToolVersionBody = RequestBodyOf<Operation<'/api/v1/tools/{tool_id}/versions', 'post'>>
type UpdateToolStatusBody = RequestBodyOf<Operation<'/api/v1/tools/{tool_id}/status', 'patch'>>
type ExecuteToolBody = RequestBodyOf<
  Operation<'/api/v1/tool-versions/{version_id}/execute', 'post'>
>

export function login(body: LoginBody): Promise<LoginResponse> {
  return api.post<LoginResponse>('/auth/login', body)
}

export function fetchMe(): Promise<MeResponse> {
  return api.get<MeResponse>('/auth/me')
}

export interface AgentListQuery {
  page?: number
  page_size?: number
  status?: AgentStatus | null
  type?: AgentType | null
  tag?: string | null
  q?: string | null
}

/** 服务端分页列表；每项自带 DEV/TEST/PROD 当前生效版本（未发布为 null）。 */
export function listAgents(query: AgentListQuery = {}): Promise<AgentPage> {
  return api.get<AgentPage>('/agents', { ...query })
}

export function createAgent(body: CreateAgentBody): Promise<Agent> {
  return api.post<Agent>('/agents', body)
}

export function copyAgent(agentId: string, body: CopyAgentBody): Promise<Agent> {
  return api.post<Agent>(`/agents/${agentId}/copy`, body)
}

export function deleteAgent(agentId: string): Promise<void> {
  return api.delete<void>(`/agents/${agentId}`)
}

export function activateAgent(agentId: string): Promise<Agent> {
  return api.post<Agent>(`/agents/${agentId}/activate`)
}

export function deactivateAgent(agentId: string): Promise<Agent> {
  return api.post<Agent>(`/agents/${agentId}/deactivate`)
}

export function archiveAgent(agentId: string): Promise<Agent> {
  return api.post<Agent>(`/agents/${agentId}/archive`)
}

export function getAgent(agentId: string): Promise<Agent> {
  return api.get<Agent>(`/agents/${agentId}`)
}

export function listAgentVersions(agentId: string): Promise<AgentVersion[]> {
  return api.get<AgentVersion[]>(`/agents/${agentId}/versions`)
}

export function createAgentVersion(
  agentId: string,
  body: CreateAgentVersionBody,
): Promise<AgentVersion> {
  return api.post<AgentVersion>(`/agents/${agentId}/versions`, body)
}

/** 发布前体检：逐个绑定给出可用性，附最近同 config_hash 的预览摘要；只读。 */
export function getAgentVersionReadiness(
  agentId: string,
  versionId: string,
): Promise<AgentReadiness> {
  return api.get<AgentReadiness>(`/agents/${agentId}/versions/${versionId}/readiness`)
}

/** 预览：按引用即时装配执行，不产生版本、不改指针；`preview_session_id` 串联多轮。 */
export function previewAgent(agentId: string, body: PreviewBody): Promise<PreviewResponse> {
  return api.post<PreviewResponse>(`/agents/${agentId}/preview`, body)
}

export function getAgentVersion(versionId: string): Promise<AgentVersion> {
  return api.get<AgentVersion>(`/agent-versions/${versionId}`)
}

export function diffAgentVersions(
  baseVersionId: string,
  targetVersionId: string,
): Promise<AgentVersionDiff> {
  return api.get<AgentVersionDiff>('/agent-versions/diff', {
    base_version_id: baseVersionId,
    target_version_id: targetVersionId,
  })
}

export function listReleases(agentId: string, env?: Environment): Promise<AgentRelease[]> {
  return api.get<AgentRelease[]>(`/agents/${agentId}/releases`, { env })
}

export function getPointer(agentId: string, env: Environment): Promise<AgentPointer> {
  return api.get<AgentPointer>(`/agents/${agentId}/pointers/${env}`)
}

export function publishRelease(agentId: string, body: ReleaseBody): Promise<AgentRelease> {
  return api.post<AgentRelease>(`/agents/${agentId}/releases`, body)
}

export function rollbackRelease(agentId: string, body: RollbackBody): Promise<AgentRelease> {
  return api.post<AgentRelease>(`/agents/${agentId}/rollback`, body)
}

export function invokeAgent(agentId: string, body: InvokeBody): Promise<InvokeResponse> {
  return api.post<InvokeResponse>(`/agents/${agentId}/invoke`, body)
}

export interface SessionListQuery {
  agent_id: string
  kind?: RunKind | null
  env?: Environment | null
  start?: string | null
  end?: string | null
  /** 默认 true：只列出本人创建（或未记录创建者）的会话；false 可见同租户其他用户会话。 */
  mine_only?: boolean
  /** 默认 true：过期会话仍返回并标记 `expired=true`。 */
  include_expired?: boolean
  page?: number
  page_size?: number
}

/** 会话列表：按 Agent 与活跃时间范围分页，含标题、轮数、归属与过期标记。 */
export function listSessions(query: SessionListQuery): Promise<SessionPage> {
  return api.get<SessionPage>('/sessions', { ...query })
}

/** 会话历史：只读回放服务端归一化的轮次，不产生运行。 */
export function getSessionHistory(
  sessionId: string,
  turnsLimit?: number,
): Promise<SessionHistory> {
  return api.get<SessionHistory>(`/sessions/${sessionId}/history`, {
    turns_limit: turnsLimit,
  })
}

/**
 * 删除会话：返回即从列表与历史中消失，会话标识随即释放；
 * 会话内容由后台进程异步回收，不可恢复。执行中的会话返回 409 `SESSION_BUSY`。
 */
export function deleteSession(sessionId: string): Promise<void> {
  return api.delete<void>(`/sessions/${sessionId}`)
}

export interface RunListQuery {
  agent_id?: string | null
  kind?: RunKind | null
  start?: string | null
  end?: string | null
  page?: number
  page_size?: number
}

/** 运行列表：默认包含全部种类，可用 `kind` 只看正式或预览运行。 */
export function listRuns(query: RunListQuery = {}): Promise<RunPage> {
  return api.get<RunPage>('/runs', { ...query })
}

export interface RunMetricsQuery {
  agent_id?: string | null
  start?: string | null
  end?: string | null
  kind?: RunKind | null
  include_preview?: boolean
}

/** 指标默认只统计正式运行（kind=INVOKE）；`include_preview` 或 `kind=PREVIEW` 可显式纳入。 */
export function getRunMetrics(query: RunMetricsQuery = {}): Promise<RunMetrics> {
  return api.get<RunMetrics>('/metrics/runs', { ...query })
}

export function getRunTrace(runId: string): Promise<RunTrace> {
  return api.get<RunTrace>(`/runs/${runId}/trace`)
}

export function listModels(): Promise<Model[]> {
  return api.get<Model[]>('/models')
}

export function createModel(body: CreateModelBody): Promise<Model> {
  return api.post<Model>('/models', body)
}

export function updateModel(modelId: string, body: UpdateModelBody): Promise<Model> {
  return api.patch<Model>(`/models/${modelId}`, body)
}

export type ModelStatusValue = Schema<'ModelStatus'>

export function updateModelStatus(
  modelId: string,
  body: UpdateModelStatusBody,
): Promise<Model> {
  return api.patch<Model>(`/models/${modelId}/status`, body)
}

export function listProviders(): Promise<Provider[]> {
  return api.get<Provider[]>('/model-providers')
}

export function createProvider(body: CreateProviderBody): Promise<Provider> {
  return api.post<Provider>('/model-providers', body)
}

export function updateProvider(
  providerId: string,
  body: UpdateProviderBody,
): Promise<Provider> {
  return api.patch<Provider>(`/model-providers/${providerId}`, body)
}

/** 供应商级探测：只验证可达与凭据有效，不比对具体模型。 */
export function probeProvider(providerId: string): Promise<ModelProbe> {
  return api.post<ModelProbe>(`/model-providers/${providerId}/probe`)
}

export function listProviderProbes(providerId: string, limit = 20): Promise<ProbeRecord[]> {
  return api.get<ProbeRecord[]>(`/model-providers/${providerId}/probes`, { limit })
}

export function probeModel(modelId: string): Promise<ModelProbe> {
  return api.post<ModelProbe>(`/models/${modelId}/probe`)
}

export function listModelProbes(modelId: string, limit = 20): Promise<ProbeRecord[]> {
  return api.get<ProbeRecord[]>(`/models/${modelId}/probes`, { limit })
}

export function listPrompts(): Promise<Prompt[]> {
  return api.get<Prompt[]>('/prompts')
}

export function getPrompt(promptId: string): Promise<Prompt> {
  return api.get<Prompt>(`/prompts/${promptId}`)
}

export function createPrompt(body: CreatePromptBody): Promise<Prompt> {
  return api.post<Prompt>('/prompts', body)
}

/** 删除 Prompt 及其全部版本；仍被 Agent 版本引用时后端返回 409 `PROMPT_IN_USE`。 */
export function deletePrompt(promptId: string): Promise<void> {
  return api.delete<void>(`/prompts/${promptId}`)
}

export function listPromptVersions(promptId: string): Promise<PromptVersion[]> {
  return api.get<PromptVersion[]>(`/prompts/${promptId}/versions`)
}

export function createPromptVersion(
  promptId: string,
  body: CreatePromptVersionBody,
): Promise<PromptVersion> {
  return api.post<PromptVersion>(`/prompts/${promptId}/versions`, body)
}

export function getPromptVersion(versionId: string): Promise<PromptVersion> {
  return api.get<PromptVersion>(`/prompt-versions/${versionId}`)
}

/** Prompt 的 diff 查询参数是 `base` / `target`（与 Agent 版本 diff 的 `base_version_id` 不同）。 */
export function diffPromptVersions(
  baseVersionId: string,
  targetVersionId: string,
): Promise<PromptDiff> {
  return api.get<PromptDiff>('/prompt-versions/diff', {
    base: baseVersionId,
    target: targetVersionId,
  })
}

export function renderPromptVersion(
  versionId: string,
  body: RenderPromptBody,
): Promise<PromptRenderResponse> {
  return api.post<PromptRenderResponse>(`/prompt-versions/${versionId}/render`, body)
}

export function listTools(): Promise<Tool[]> {
  return api.get<Tool[]>('/tools')
}

export function getTool(toolId: string): Promise<Tool> {
  return api.get<Tool>(`/tools/${toolId}`)
}

export function createTool(body: CreateToolBody): Promise<Tool> {
  return api.post<Tool>('/tools', body)
}

export function updateToolStatus(toolId: string, body: UpdateToolStatusBody): Promise<Tool> {
  return api.patch<Tool>(`/tools/${toolId}/status`, body)
}

/** 探测只读：连远端取工具清单，用于注册前填充契约；不创建工具也不出版本。 */
export function probeMcpTool(body: ProbeMcpBody): Promise<McpProbeResult> {
  return api.post<McpProbeResult>('/tools/probe-mcp', body)
}

export function listToolVersions(toolId: string): Promise<ToolVersion[]> {
  return api.get<ToolVersion[]>(`/tools/${toolId}/versions`)
}

export function addToolVersion(
  toolId: string,
  body: CreateToolVersionBody,
): Promise<ToolVersion> {
  return api.post<ToolVersion>(`/tools/${toolId}/versions`, body)
}

export function getToolGrant(toolId: string): Promise<ToolGrant> {
  return api.get<ToolGrant>(`/tools/${toolId}/grant`)
}

export function grantTool(toolId: string): Promise<void> {
  return api.post<void>(`/tools/${toolId}/grant`)
}

export function revokeTool(toolId: string): Promise<void> {
  return api.delete<void>(`/tools/${toolId}/grant`)
}

export function executeToolVersion(
  versionId: string,
  body: ExecuteToolBody,
): Promise<ToolExecution> {
  return api.post<ToolExecution>(`/tool-versions/${versionId}/execute`, body)
}

export interface ToolCallQuery {
  start?: string
  end?: string
  tool_version_id?: string
  limit?: number
}

export function listToolCalls(
  toolId: string,
  query: ToolCallQuery = {},
): Promise<ToolCallOverview> {
  return api.get<ToolCallOverview>('/tool-calls', { tool_id: toolId, ...query })
}

export type Secret = Schema<'SecretResponse'>
export type SecretCreateInput = Schema<'SecretCreateRequest'>
export type SecretRotateInput = Schema<'SecretRotateRequest'>

export interface SecretPage {
  items: Secret[]
  total: number
}

export function listSecrets(): Promise<SecretPage> {
  return api.get<SecretPage>('/secrets')
}

/** 同名视为轮换：引用 ID 不变、密文与 key_version 更新，不会报冲突。 */
export function createSecret(body: SecretCreateInput): Promise<Secret> {
  return api.post<Secret>('/secrets', body)
}

export function rotateSecret(secretId: string, body: SecretRotateInput): Promise<Secret> {
  return api.post<Secret>(`/secrets/${secretId}/rotate`, body)
}

/** 软停用：引用该凭据的调用会因凭据不可用而失败，记录本身保留。 */
export function disableSecret(secretId: string): Promise<void> {
  return api.delete<void>(`/secrets/${secretId}`)
}
