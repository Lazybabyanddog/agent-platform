import { computed, onUnmounted, ref, type Ref } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { ElMessageBox } from 'element-plus'

import { ApiError, describeError } from '@/api/client'
import {
  createAgentVersion,
  listModels,
  listPromptVersions,
  listPrompts,
  listToolVersions,
  listTools,
  type AgentVersion,
  type AgentVersionCreateInput,
  type Model,
  type PreviewRequest,
} from '@/api/endpoints'

export interface PromptVersionOption {
  value: string
  label: string
  promptCode: string
}

export interface ToolVersionOption {
  value: string
  label: string
  risk: string | null
  granted: boolean
  active: boolean
}

export interface PromptVariableView {
  name: string
  type?: string
  required?: boolean
  default?: unknown
}

export interface VersionConflict {
  reason: string
  baseVersionNo: number | null
  latestVersionNo: number | null
  diff: unknown
}

interface SnapshotShape {
  model?: { model_id?: string; params?: Record<string, unknown> }
  prompt?: { prompt_version_id?: string; variables?: PromptVariableView[] }
  tools?: { tool_version_id?: string }[]
  policy?: Record<string, unknown>
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

/**
 * Agent 配置编辑态：保存版本与预览共用同一组引用，基准版本随保存滚动更新。
 * 存在未保存改动时离开路由或关闭页面会先确认。
 */
export function useAgentConfigEditor(agentId: Ref<string>) {
  const modelId = ref('')
  const promptVersionId = ref('')
  const toolVersionIds = ref<string[]>([])
  const paramsText = ref('{}')
  const note = ref('')
  const maxSteps = ref(6)
  const timeoutS = ref(30)
  const tokenBudget = ref(8000)
  const sessionTtlH = ref(168)
  const historyTokenLimit = ref(4000)

  const baseVersionId = ref('')
  const baseVersionNo = ref<number | null>(null)
  const baseVersion = ref<AgentVersion | null>(null)
  const initial = ref('')

  const saving = ref(false)
  const conflict = ref<VersionConflict | null>(null)

  const models = ref<Model[]>([])
  const promptOptions = ref<PromptVersionOption[]>([])
  const promptVariables = ref<Record<string, PromptVariableView[]>>({})
  const snapshotVariables = ref<PromptVariableView[]>([])
  const toolOptions = ref<ToolVersionOption[]>([])
  const catalogLoading = ref(false)
  const catalogError = ref<string | null>(null)

  /** 预览入参按当前 Prompt 版本声明的变量生成。 */
  const activeVariables = computed<PromptVariableView[]>(
    () => promptVariables.value[promptVersionId.value] ?? snapshotVariables.value,
  )

  function serialize(): string {
    return JSON.stringify({
      modelId: modelId.value,
      promptVersionId: promptVersionId.value,
      toolVersionIds: [...toolVersionIds.value],
      paramsText: paramsText.value,
      note: note.value,
      maxSteps: maxSteps.value,
      timeoutS: timeoutS.value,
      tokenBudget: tokenBudget.value,
      sessionTtlH: sessionTtlH.value,
      historyTokenLimit: historyTokenLimit.value,
    })
  }

  const dirty = computed(() => initial.value !== '' && serialize() !== initial.value)
  const canSave = computed(
    () => Boolean(modelId.value && promptVersionId.value) && !saving.value,
  )

  function loadFromVersion(version: AgentVersion | null): void {
    baseVersion.value = version
    baseVersionId.value = version?.id ?? ''
    baseVersionNo.value = version?.version_no ?? null
    if (!version) {
      initial.value = serialize()
      return
    }
    const snapshot = (version.snapshot ?? {}) as SnapshotShape
    modelId.value = snapshot.model?.model_id ?? ''
    promptVersionId.value = snapshot.prompt?.prompt_version_id ?? ''
    snapshotVariables.value = snapshot.prompt?.variables ?? []
    toolVersionIds.value = (snapshot.tools ?? [])
      .map((item) => item.tool_version_id)
      .filter((value): value is string => Boolean(value))
    paramsText.value = JSON.stringify(snapshot.model?.params ?? {}, null, 2)
    note.value = version.note ?? ''
    const policy = snapshot.policy ?? {}
    maxSteps.value = asNumber(policy.max_steps, 6)
    timeoutS.value = asNumber(policy.timeout_s, 30)
    tokenBudget.value = asNumber(policy.token_budget, 8000)
    sessionTtlH.value = asNumber(policy.session_ttl_h, 168)
    historyTokenLimit.value = asNumber(policy.history_token_limit, 4000)
    initial.value = serialize()
  }

  function reset(): void {
    loadFromVersion(baseVersion.value)
  }

  function paramsObject(): Record<string, unknown> {
    const text = paramsText.value.trim()
    if (!text) return {}
    let parsed: unknown
    try {
      parsed = JSON.parse(text)
    } catch {
      throw new Error('模型参数必须是合法 JSON')
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      throw new Error('模型参数必须是 JSON 对象')
    }
    return parsed as Record<string, unknown>
  }

  function policyPayload(): Record<string, unknown> {
    return {
      max_steps: maxSteps.value,
      timeout_s: timeoutS.value,
      token_budget: tokenBudget.value,
      session_ttl_h: sessionTtlH.value,
      history_token_limit: historyTokenLimit.value,
    }
  }

  /** 保存与预览共同的引用集合。 */
  function referencePayload(): Pick<
    AgentVersionCreateInput,
    'model_id' | 'prompt_version_id' | 'tool_version_ids' | 'params' | 'policy'
  > {
    if (!modelId.value) throw new Error('请选择模型')
    if (!promptVersionId.value) throw new Error('请选择 Prompt 版本')
    return {
      model_id: modelId.value,
      prompt_version_id: promptVersionId.value,
      tool_version_ids: [...toolVersionIds.value],
      params: paramsObject(),
      policy: policyPayload(),
    }
  }

  function toVersionPayload(base?: string | null): AgentVersionCreateInput {
    return {
      ...referencePayload(),
      note: note.value.trim() || null,
      ...(base ? { base_version_id: base } : {}),
    }
  }

  function toPreviewPayload(
    input: Record<string, unknown>,
    options: { env?: PreviewRequest['env']; previewSessionId?: string | null; timeoutS?: number | null } = {},
  ): PreviewRequest {
    return {
      env: options.env ?? 'DEV',
      ...referencePayload(),
      input,
      preview_session_id: options.previewSessionId ?? null,
      timeout_s: options.timeoutS ?? null,
    }
  }

  async function save(options: { ignoreBase?: boolean } = {}): Promise<AgentVersion> {
    saving.value = true
    conflict.value = null
    try {
      const version = await createAgentVersion(
        agentId.value,
        toVersionPayload(options.ignoreBase ? null : baseVersionId.value),
      )
      loadFromVersion(version)
      return version
    } catch (err) {
      if (err instanceof ApiError && err.code === 'AGENT_VERSION_CONFLICT') {
        const detail = (err.details[0] ?? {}) as {
          reason?: string
          base_version_no?: number
          latest_version_no?: number | null
          diff?: unknown
        }
        conflict.value = {
          reason: detail.reason ?? '基准版本已不是最新版本',
          baseVersionNo: detail.base_version_no ?? baseVersionNo.value,
          latestVersionNo: detail.latest_version_no ?? null,
          diff: detail.diff ?? null,
        }
      }
      throw err
    } finally {
      saving.value = false
    }
  }

  async function loadCatalog(): Promise<void> {
    catalogLoading.value = true
    catalogError.value = null
    try {
      const [modelList, prompts, tools] = await Promise.all([
        listModels(),
        listPrompts(),
        listTools(),
      ])
      models.value = modelList
      const promptVersions = await Promise.all(
        prompts.map(async (prompt) => ({
          prompt,
          versions: await listPromptVersions(prompt.id),
        })),
      )
      promptOptions.value = promptVersions.flatMap(({ prompt, versions }) =>
        versions.map((version) => {
          promptVariables.value[version.id] = (version.variables ??
            []) as unknown as PromptVariableView[]
          return {
            value: version.id,
            label: `${prompt.code} v${version.version_no}`,
            promptCode: prompt.code,
          }
        }),
      )
      const toolVersions = await Promise.all(
        tools.map(async (tool) => ({ tool, versions: await listToolVersions(tool.id) })),
      )
      toolOptions.value = toolVersions.flatMap(({ tool, versions }) =>
        versions.map((version) => ({
          value: version.id,
          label: `${tool.code} v${version.version_no}`,
          risk: version.risk_level,
          granted: tool.granted,
          active: tool.status === 'ACTIVE',
        })),
      )
    } catch (err) {
      catalogError.value = describeError(err)
    } finally {
      catalogLoading.value = false
    }
  }

  onBeforeRouteLeave(async () => {
    if (!dirty.value) return true
    try {
      await ElMessageBox.confirm('配置有未保存的改动，确定离开吗？', '未保存的改动', {
        type: 'warning',
        confirmButtonText: '离开',
        cancelButtonText: '继续编辑',
      })
      return true
    } catch {
      return false
    }
  })

  function beforeUnload(event: BeforeUnloadEvent): void {
    if (!dirty.value) return
    event.preventDefault()
    event.returnValue = ''
  }

  window.addEventListener('beforeunload', beforeUnload)
  onUnmounted(() => {
    window.removeEventListener('beforeunload', beforeUnload)
  })

  return {
    modelId,
    promptVersionId,
    toolVersionIds,
    paramsText,
    note,
    maxSteps,
    timeoutS,
    tokenBudget,
    sessionTtlH,
    historyTokenLimit,
    baseVersionId,
    baseVersionNo,
    baseVersion,
    saving,
    dirty,
    canSave,
    conflict,
    models,
    promptOptions,
    activeVariables,
    toolOptions,
    catalogLoading,
    catalogError,
    loadFromVersion,
    reset,
    save,
    loadCatalog,
    toPreviewPayload,
  }
}
