<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'

import { ApiError, describeError } from '@/api/client'
import {
  ENVIRONMENTS,
  activateAgent,
  archiveAgent,
  copyAgent,
  deactivateAgent,
  deleteAgent,
  getAgent,
  getAgentVersionReadiness,
  listAgentVersions,
  listReleases,
  publishRelease,
  type Agent,
  type AgentEnvironmentState,
  type AgentReadiness,
  type AgentRelease,
  type AgentStatus,
  type AgentVersion,
  type Environment,
  type PreviewRequest,
} from '@/api/endpoints'
import AgentPreviewPanel from '@/components/agent/AgentPreviewPanel.vue'
import ModelParamsEditor from '@/components/agent/ModelParamsEditor.vue'
import JsonBlock from '@/components/JsonBlock.vue'
import { RUNTIME_CONTEXT_OPTIONS, useAgentConfigEditor } from '@/composables/useAgentConfigEditor'
import { useSessionStore } from '@/stores/session'

interface SnapshotModel {
  model_id?: string
  code?: string
  model_name?: string
  ctx_window?: number | null
  capabilities?: Record<string, unknown>
  params?: Record<string, unknown>
}

interface SnapshotPromptMessage {
  role?: string
  content?: unknown
}

interface SnapshotPrompt {
  prompt_id?: string
  prompt_code?: string
  prompt_version_id?: string
  version_no?: number
  messages?: SnapshotPromptMessage[]
  variables?: unknown[]
}

interface SnapshotTool {
  tool_id?: string
  tool_code?: string
  tool_version_id?: string
  version_no?: number
  risk_level?: string
  spec?: unknown
}

type SnapshotPolicy = Record<string, unknown>

interface Snapshot {
  model?: SnapshotModel
  prompt?: SnapshotPrompt
  tools?: SnapshotTool[]
  policy?: SnapshotPolicy
}

const route = useRoute()
const router = useRouter()
const agentId = computed(() => String(route.params.agentId ?? ''))
const editor = useAgentConfigEditor(agentId)

/** 清空声明会让模型失去平台时间/时区，属于会影响答非所问的破坏性编辑，先确认。 */
async function onRuntimeContextChange(next: unknown): Promise<void> {
  const values = Array.isArray(next) ? next.filter((item): item is string => typeof item === 'string') : []
  if (!values.length && editor.runtimeContext.value.length) {
    try {
      await ElMessageBox.confirm(
        '清空后模型看不到当前时间与时区，只能依据用户输入推断相对时间（如“明天”）。确定清空吗？',
        '清空运行时上下文',
        { type: 'warning', confirmButtonText: '清空', cancelButtonText: '保留' },
      )
    } catch {
      return
    }
  }
  editor.runtimeContext.value = values
}

const session = useSessionStore()
const canManage = computed(() => session.can('agent:manage'))

const lifecyclePending = ref<string | null>(null)
const deleting = ref(false)

const copyVisible = ref(false)
const copyCode = ref('')
const copyName = ref('')
const copyError = ref<string | null>(null)
const copying = ref(false)

async function runLifecycle(
  label: string,
  action: () => Promise<unknown>,
  options: { confirm?: string } = {},
): Promise<void> {
  if (options.confirm) {
    try {
      await ElMessageBox.confirm(options.confirm, `${label}确认`, {
        type: 'warning',
        confirmButtonText: label,
        cancelButtonText: '取消',
      })
    } catch {
      return
    }
  }
  lifecyclePending.value = label
  try {
    await action()
    ElMessage.success(`${label}成功`)
    await load()
  } catch (err) {
    ElMessage.error(describeError(err))
    await load()
  } finally {
    lifecyclePending.value = null
  }
}

function activate(): void {
  void runLifecycle('启用', () => activateAgent(agentId.value))
}

function deactivate(): void {
  void runLifecycle('停用', () => deactivateAgent(agentId.value), {
    confirm: '停用后所有环境的新调用都会被拒绝，发布指针保持不变。确定停用吗？',
  })
}

function archive(): void {
  void runLifecycle('归档', () => archiveAgent(agentId.value), {
    confirm: '归档为终态，之后不可编辑、发布、回滚或启用。确定归档吗？',
  })
}

/** 软删除：删完页面就 404 了，所以不复用 runLifecycle 的"成功后再 load"。 */
async function remove(): Promise<void> {
  try {
    await ElMessageBox.confirm(
      '删除为软删除：该 Agent 立即从列表消失，所有环境的新调用被拒绝（AGENT_DELETED）；版本、发布与运行历史保留，编码可重新使用。确定删除吗？',
      '删除确认',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deleting.value = true
  try {
    await deleteAgent(agentId.value)
    ElMessage.success('已删除')
    await router.push('/agents')
  } catch (err) {
    ElMessage.error(describeError(err))
  } finally {
    deleting.value = false
  }
}

function openCopy(): void {
  if (!agent.value) return
  copyCode.value = `${agent.value.code}_copy`
  copyName.value = `${agent.value.name} (copy)`
  copyError.value = null
  copyVisible.value = true
}

async function submitCopy(): Promise<void> {
  copyError.value = null
  copying.value = true
  try {
    const created = await copyAgent(agentId.value, {
      code: copyCode.value.trim(),
      name: copyName.value.trim() || null,
    })
    copyVisible.value = false
    ElMessage.success('已复制 Agent')
    await router.push(`/agents/${created.id}`)
  } catch (err) {
    copyError.value = describeError(err)
  } finally {
    copying.value = false
  }
}

function buildPreviewRequest(input: Record<string, unknown>, sessionId: string): PreviewRequest {
  if (paramsEditorRef.value && !paramsEditorRef.value.commit()) {
    throw new Error('模型参数未通过校验，请先修正 JSON 再预览')
  }
  return editor.toPreviewPayload(input, {
    env: 'DEV',
    previewSessionId: sessionId,
    timeoutS: editor.timeoutS.value,
  })
}

const agent = ref<Agent | null>(null)
const versions = ref<AgentVersion[]>([])
const releases = ref<AgentRelease[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const versionsError = ref<string | null>(null)
const releasesError = ref<string | null>(null)

const selectedVersionId = ref('')
const readiness = ref<AgentReadiness | null>(null)
const readinessLoading = ref(false)
const readinessError = ref<string | null>(null)
const readinessVisible = ref(false)

const publishEnv = ref<Environment>('DEV')
const publishVersionId = ref('')
const publishNote = ref('')
const publishing = ref(false)
const publishError = ref<string | null>(null)

const sortedVersions = computed(() =>
  [...versions.value].sort((a, b) => b.version_no - a.version_no),
)

const selectedVersion = computed(
  () => versions.value.find((item) => item.id === selectedVersionId.value) ?? null,
)

const snapshot = computed<Snapshot>(
  () => (selectedVersion.value?.snapshot ?? {}) as Snapshot,
)

const sortedTools = computed(() => snapshot.value.tools ?? [])

const envStates = computed<Record<Environment, AgentEnvironmentState | null>>(() => {
  const source = agent.value?.environments ?? {}
  return {
    DEV: source.DEV ?? null,
    TEST: source.TEST ?? null,
    PROD: source.PROD ?? null,
  }
})

function statusType(status: AgentStatus): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'ACTIVE') return 'success'
  if (status === 'DRAFT') return 'warning'
  if (status === 'ARCHIVED') return 'danger'
  return 'info'
}

function statusLabel(status: AgentStatus): string {
  if (status === 'ACTIVE') return '启用中'
  if (status === 'DRAFT') return '草稿'
  if (status === 'ARCHIVED') return '已归档'
  return '已停用'
}

function formatTime(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : '—'
}

function short(value: string | null | undefined): string {
  if (!value) return '—'
  return value.length > 16 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value
}

function messageRole(message: SnapshotPromptMessage): string {
  return message.role ?? 'user'
}

function messageContent(message: SnapshotPromptMessage): string {
  const content = message.content
  if (typeof content === 'string') return content
  if (content === null || content === undefined) return ''
  try {
    return JSON.stringify(content)
  } catch {
    return String(content)
  }
}

function policyValue(key: string): string {
  const value = snapshot.value.policy?.[key]
  return value === undefined || value === null ? '默认' : String(value)
}

async function load(resetEditor = false): Promise<void> {
  loading.value = true
  error.value = null
  versionsError.value = null
  releasesError.value = null
  const [detailResult, versionResult, releaseResult] = await Promise.allSettled([
    getAgent(agentId.value),
    listAgentVersions(agentId.value),
    listReleases(agentId.value),
  ])
  if (detailResult.status === 'fulfilled') {
    agent.value = detailResult.value
  } else {
    error.value = describeError(detailResult.reason)
  }
  if (releaseResult.status === 'fulfilled') {
    releases.value = releaseResult.value
  } else {
    releases.value = []
    releasesError.value = describeError(releaseResult.reason)
  }
  if (versionResult.status === 'fulfilled') {
    const versionList = versionResult.value
    versions.value = versionList
    if (versionList.length) {
      const latest = [...versionList].sort((a, b) => b.version_no - a.version_no)[0]
      if (!versionList.some((item) => item.id === selectedVersionId.value)) {
        selectedVersionId.value = latest.id
      }
      if (!versionList.some((item) => item.id === publishVersionId.value)) {
        publishVersionId.value = latest.id
      }
      if (resetEditor) editor.loadFromVersion(latest)
    } else {
      publishVersionId.value = ''
      if (resetEditor) editor.loadFromVersion(null)
    }
  } else {
    versions.value = []
    publishVersionId.value = ''
    versionsError.value = describeError(versionResult.reason)
  }
  loading.value = false
}

function focusConfigEditor(): void {
  document
    .getElementById('config-editor-card')
    ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

const saveError = ref<string | null>(null)
const paramErrors = ref<Record<string, string>>({})
const paramsEditorRef = ref<InstanceType<typeof ModelParamsEditor> | null>(null)

function useVersionAsBase(version: AgentVersion): void {
  editor.loadFromVersion(version)
  saveError.value = null
  paramErrors.value = {}
}

function changeModel(): void {
  editor.applyModelTemplate()
  paramErrors.value = {}
}

// 用户一旦改动参数，上一轮后端返回的字段错误就不再适用。
watch(
  () => editor.params.value,
  () => {
    paramErrors.value = {}
  },
  { deep: true },
)

async function saveConfig(): Promise<void> {
  saveError.value = null
  paramErrors.value = {}
  if (paramsEditorRef.value && !paramsEditorRef.value.commit()) return
  try {
    const version = await editor.save()
    selectedVersionId.value = version.id
    ElMessage.success(`已保存为 v${version.version_no}`)
    await load()
  } catch (err) {
    if (err instanceof ApiError && err.code === 'AGENT_VERSION_CONFLICT' && editor.conflict.value) {
      await resolveConflict()
      return
    }
    if (err instanceof ApiError) {
      paramErrors.value = Object.fromEntries(
        err.details
          .filter((item) => item.field?.startsWith('params.') && item.reason)
          .map((item) => [item.field as string, item.reason as string]),
      )
    }
    saveError.value = describeError(err)
  }
}

async function resolveConflict(): Promise<void> {
  const info = editor.conflict.value
  if (!info) return
  const diff = info.diff ? JSON.stringify(info.diff).slice(0, 400) : '无差异摘要'
  try {
    await ElMessageBox.confirm(
      `当前编辑基准为 v${info.baseVersionNo ?? '?'}，服务端最新为 v${
        info.latestVersionNo ?? '?'
      }。差异摘要：${diff}`,
      '版本冲突',
      {
        type: 'warning',
        confirmButtonText: '以最新为基准继续保存',
        cancelButtonText: '取消',
      },
    )
  } catch {
    saveError.value = '已取消保存：请先查看最新版本后再提交'
    return
  }
  try {
    const version = await editor.save({ ignoreBase: true })
    selectedVersionId.value = version.id
    ElMessage.success(`已保存为 v${version.version_no}`)
    await load()
  } catch (err) {
    saveError.value = describeError(err)
  }
}

async function checkReadiness(version: AgentVersion): Promise<void> {
  readinessError.value = null
  readiness.value = null
  readinessVisible.value = true
  readinessLoading.value = true
  try {
    readiness.value = await getAgentVersionReadiness(agentId.value, version.id)
  } catch (err) {
    readinessError.value = describeError(err)
  } finally {
    readinessLoading.value = false
  }
}

function openPublish(version: AgentVersion): void {
  publishVersionId.value = version.id
  publishError.value = null
}

async function publish(): Promise<void> {
  publishError.value = null
  if (versionsError.value) {
    publishError.value = `版本列表加载失败：${versionsError.value}`
    return
  }
  if (!versions.value.length) {
    publishError.value =
      '该 Agent 还没有任何版本，请先在「配置编辑」中选择模型与 Prompt 版本并保存，生成 v1 后再发布'
    return
  }
  if (!publishVersionId.value) {
    publishError.value = '请选择要发布的版本'
    return
  }
  const version = versions.value.find((item) => item.id === publishVersionId.value)
  if (!version) {
    publishError.value = '所选版本不存在，请刷新后重试'
    return
  }
  publishing.value = true
  try {
    const check = await getAgentVersionReadiness(agentId.value, version.id)
    if (!check.ready) {
      readiness.value = check
      readinessVisible.value = true
      publishError.value = '该版本存在不可用引用，先修复后再发布'
      return
    }
    await publishRelease(agentId.value, {
      agent_version_id: version.id,
      env: publishEnv.value,
      note: publishNote.value.trim() || null,
    })
    ElMessage.success(`已发布 v${version.version_no} 到 ${publishEnv.value}`)
    publishNote.value = ''
    await load()
  } catch (err) {
    if (err instanceof ApiError && err.code === 'AGENT_STATUS_TRANSITION') {
      await load()
    }
    publishError.value = describeError(err)
  } finally {
    publishing.value = false
  }
}

onMounted(() => {
  void load(true)
  void editor.loadCatalog()
})
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <el-button link type="primary" @click="router.push('/agents')">← 返回列表</el-button>
        <h2 class="page-title">
          Agent 详情
          <span v-if="agent" class="agent-chip">{{ agent.code }} · {{ agent.name }}</span>
          <el-tag v-if="agent" :type="statusType(agent.status)" size="small" class="status">
            {{ statusLabel(agent.status) }}
          </el-tag>
        </h2>
      </div>
      <div class="actions">
        <el-button v-if="agent" @click="router.push(`/agents/${agent.id}/versions`)">
          版本与快照
        </el-button>
        <el-button v-if="agent" type="primary" @click="router.push(`/playground?agentId=${agent.id}`)">
          预览调试
        </el-button>
        <template v-if="agent && canManage">
          <el-button
            v-if="agent.status === 'INACTIVE'"
            type="success"
            :loading="lifecyclePending === '启用'"
            @click="activate"
          >
            启用
          </el-button>
          <el-button
            v-if="agent.status === 'ACTIVE'"
            :loading="lifecyclePending === '停用'"
            @click="deactivate"
          >
            停用
          </el-button>
          <el-button @click="openCopy">复制</el-button>
          <el-button
            v-if="agent.status !== 'ARCHIVED'"
            :loading="lifecyclePending === '归档'"
            @click="archive"
          >
            归档
          </el-button>
          <el-button
            type="danger"
            plain
            :loading="deleting"
            @click="remove"
          >
            删除
          </el-button>
        </template>
        <el-button :loading="loading" @click="load">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="error" type="error" show-icon :closable="false" class="error-box">
      <template #title>
        <div class="error-title">
          <span>{{ error }}</span>
          <el-button size="small" @click="load">重试</el-button>
        </div>
      </template>
    </el-alert>

    <template v-if="agent">
      <el-row :gutter="16">
        <el-col :span="14">
          <el-card shadow="never" class="panel">
            <template #header>
              <span class="panel-title">身份概览</span>
            </template>
            <el-descriptions :column="2" size="small" border label-width="92px">
              <el-descriptions-item label="编码">{{ agent.code }}</el-descriptions-item>
              <el-descriptions-item label="名称">{{ agent.name }}</el-descriptions-item>
              <el-descriptions-item label="类型">{{ agent.type }}</el-descriptions-item>
              <el-descriptions-item label="状态">
                <el-tag :type="statusType(agent.status)" size="small">{{ agent.status }}</el-tag>
              </el-descriptions-item>
              <el-descriptions-item label="描述" :span="2">
                {{ agent.description ?? '—' }}
              </el-descriptions-item>
              <el-descriptions-item label="标签" :span="2">
                <el-tag v-for="tag in agent.tags" :key="tag" size="small" type="info" class="tag">
                  {{ tag }}
                </el-tag>
                <span v-if="!agent.tags.length">—</span>
              </el-descriptions-item>
              <el-descriptions-item label="创建时间">
                {{ formatTime(agent.created_at) }}
              </el-descriptions-item>
              <el-descriptions-item label="更新时间">
                {{ formatTime(agent.updated_at) }}
              </el-descriptions-item>
            </el-descriptions>
          </el-card>
        </el-col>

        <el-col :span="10">
          <el-card shadow="never" class="panel">
            <template #header>
              <span class="panel-title">当前生效版本</span>
            </template>
            <div class="env-grid">
              <div v-for="env in ENVIRONMENTS" :key="env" class="env-item">
                <div class="env-name">{{ env }}</div>
                <template v-if="envStates[env]">
                  <el-tag type="success" size="small">v{{ envStates[env]!.version_no }}</el-tag>
                  <div class="env-time">{{ formatTime(envStates[env]!.released_at) }}</div>
                </template>
                <template v-else>
                  <el-tag type="info" size="small" effect="plain">未发布</el-tag>
                  <div class="env-time">—</div>
                </template>
              </div>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="panel">
        <template #header>
          <div class="panel-head">
            <span class="panel-title">配置</span>
            <el-select v-model="selectedVersionId" placeholder="选择版本" class="version-select">
              <el-option
                v-for="item in sortedVersions"
                :key="item.id"
                :label="`v${item.version_no}`"
                :value="item.id"
              />
            </el-select>
          </div>
        </template>

        <el-empty v-if="!selectedVersion" description="该 Agent 还没有版本" />

        <template v-else>
          <div class="config-meta">
            <el-tag size="small" type="info">v{{ selectedVersion.version_no }}</el-tag>
            <span class="hash" :title="selectedVersion.config_hash">
              config_hash {{ short(selectedVersion.config_hash) }}
            </span>
            <span class="page-hint">创建于 {{ formatTime(selectedVersion.created_at) }}</span>
            <el-button size="small" @click="checkReadiness(selectedVersion)">发布体检</el-button>
            <el-button size="small" type="primary" @click="openPublish(selectedVersion)">
              发布此版本
            </el-button>
            <el-button size="small" @click="useVersionAsBase(selectedVersion)">
              以此为编辑基准
            </el-button>
          </div>

          <el-row :gutter="16">
            <el-col :span="12">
              <div class="config-block">
                <div class="config-title">模型</div>
                <el-descriptions :column="1" size="small" border label-width="88px">
                  <el-descriptions-item label="编码">
                    {{ snapshot.model?.code ?? '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="模型名">
                    {{ snapshot.model?.model_name ?? '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="上下文窗口">
                    {{ snapshot.model?.ctx_window ?? '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="模型 ID">
                    <code>{{ short(snapshot.model?.model_id) }}</code>
                  </el-descriptions-item>
                </el-descriptions>
                <div class="config-sub">参数</div>
                <JsonBlock :value="snapshot.model?.params" max-height="150px" />
              </div>

              <div class="config-block">
                <div class="config-title">Prompt</div>
                <el-descriptions :column="1" size="small" border label-width="88px">
                  <el-descriptions-item label="编码">
                    {{ snapshot.prompt?.prompt_code ?? '—' }}
                  </el-descriptions-item>
                  <el-descriptions-item label="版本">
                    v{{ snapshot.prompt?.version_no ?? '—' }}
                  </el-descriptions-item>
                </el-descriptions>
                <div class="config-sub">消息</div>
                <div class="message-list">
                  <div
                    v-for="(message, index) in snapshot.prompt?.messages ?? []"
                    :key="index"
                    class="message"
                  >
                    <el-tag size="small" type="info">{{ messageRole(message) }}</el-tag>
                    <span class="message-text">{{ messageContent(message) }}</span>
                  </div>
                  <span v-if="!(snapshot.prompt?.messages ?? []).length" class="page-hint">
                    无消息
                  </span>
                </div>
                <div class="config-sub">变量</div>
                <JsonBlock :value="snapshot.prompt?.variables" max-height="110px" />
              </div>
            </el-col>

            <el-col :span="12">
              <div class="config-block">
                <div class="config-title">工具</div>
                <el-table :data="sortedTools" size="small" row-key="tool_version_id">
                  <el-table-column prop="tool_code" label="编码" min-width="120" />
                  <el-table-column label="版本" width="70">
                    <template #default="{ row }">v{{ row.version_no }}</template>
                  </el-table-column>
                  <el-table-column label="风险" width="90">
                    <template #default="{ row }">
                      <el-tag size="small" :type="row.risk_level === 'HIGH' ? 'danger' : 'info'">
                        {{ row.risk_level ?? '—' }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column type="expand">
                    <template #default="{ row }">
                      <JsonBlock :value="row.spec" max-height="180px" />
                    </template>
                  </el-table-column>
                </el-table>
                <p v-if="!sortedTools.length" class="page-hint">未绑定工具。</p>
              </div>

              <div class="config-block">
                <div class="config-title">执行策略</div>
                <el-descriptions :column="1" size="small" border label-width="110px">
                  <el-descriptions-item label="最大步骤">
                    {{ policyValue('max_steps') }}
                  </el-descriptions-item>
                  <el-descriptions-item label="超时（秒）">
                    {{ policyValue('timeout_s') }}
                  </el-descriptions-item>
                  <el-descriptions-item label="Token 预算">
                    {{ policyValue('token_budget') }}
                  </el-descriptions-item>
                  <el-descriptions-item label="会话 TTL（时）">
                    {{ policyValue('session_ttl_h') }}
                  </el-descriptions-item>
                  <el-descriptions-item label="历史 Token 上限">
                    {{ policyValue('history_token_limit') }}
                  </el-descriptions-item>
                </el-descriptions>
              </div>
            </el-col>
          </el-row>
        </template>
      </el-card>

      <el-card id="config-editor-card" shadow="never" class="panel">
        <template #header>
          <div class="panel-head">
            <div class="editor-title">
              <span class="panel-title">配置编辑</span>
              <el-tag v-if="editor.baseVersionNo.value" size="small" type="info">
                基准 v{{ editor.baseVersionNo.value }}
              </el-tag>
              <el-tag v-if="editor.dirty.value" size="small" type="warning">未保存</el-tag>
            </div>
            <div class="actions">
              <el-button size="small" :disabled="!editor.dirty.value" @click="editor.reset()">
                重置
              </el-button>
              <el-button
                size="small"
                type="primary"
                :loading="editor.saving.value"
                :disabled="!canManage || agent.status === 'ARCHIVED' || !editor.canSave.value"
                @click="saveConfig"
              >
                保存为新版本
              </el-button>
            </div>
          </div>
        </template>

        <el-alert
          v-if="agent.status === 'ARCHIVED'"
          title="已归档的 Agent 不可再保存新版本，以下内容只读。"
          type="info"
          show-icon
          :closable="false"
          class="publish-error"
        />
        <el-alert
          v-else-if="!canManage"
          title="当前账号缺少 AGENT_MANAGE 权限，配置只读。"
          type="info"
          show-icon
          :closable="false"
          class="publish-error"
        />
        <el-alert
          v-if="editor.catalogError.value"
          :title="editor.catalogError.value"
          type="error"
          show-icon
          :closable="false"
          class="publish-error"
        >
          <el-button size="small" @click="editor.loadCatalog()">重试加载引用资源</el-button>
        </el-alert>
        <el-alert
          v-if="saveError"
          :title="saveError"
          type="error"
          show-icon
          :closable="false"
          class="publish-error"
        />

        <el-form label-width="110px" label-position="left" v-loading="editor.catalogLoading.value">
          <el-row :gutter="16">
            <el-col :span="12">
              <el-form-item label="模型">
                <el-select
                  v-model="editor.modelId.value"
                  placeholder="选择模型"
                  filterable
                  class="full"
                  @change="changeModel"
                >
                  <el-option
                    v-for="model in editor.models.value"
                    :key="model.id"
                    :label="`${model.code} · ${model.model_name}`"
                    :value="model.id"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="12">
              <el-form-item label="Prompt 版本">
                <el-select
                  v-model="editor.promptVersionId.value"
                  placeholder="选择 Prompt 版本"
                  filterable
                  class="full"
                >
                  <el-option
                    v-for="option in editor.promptOptions.value"
                    :key="option.value"
                    :label="option.label"
                    :value="option.value"
                  />
                </el-select>
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item label="工具">
            <el-select
              v-model="editor.toolVersionIds.value"
              multiple
              filterable
              placeholder="选择工具版本（按执行顺序）"
              class="full"
            >
              <el-option
                v-for="option in editor.toolOptions.value"
                :key="option.value"
                :label="option.label"
                :value="option.value"
                :disabled="!option.active || !option.granted"
              >
                <span>{{ option.label }}</span>
                <span class="option-hint">
                  {{ option.active ? '' : '已停用' }}
                  {{ option.granted ? '' : '未授权' }}
                  {{ option.risk ? `风险 ${option.risk}` : '' }}
                </span>
              </el-option>
            </el-select>
          </el-form-item>

          <el-form-item label="模型参数">
            <ModelParamsEditor
              ref="paramsEditorRef"
              v-model="editor.params.value"
              :template="editor.activeParamTemplate.value"
              :errors="paramErrors"
            />
          </el-form-item>

          <el-row :gutter="12">
            <el-col :span="5">
              <el-form-item label="最大步骤" label-width="80px">
                <el-input-number v-model="editor.maxSteps.value" :min="1" :max="20" class="full" />
              </el-form-item>
            </el-col>
            <el-col :span="5">
              <el-form-item label="超时(秒)" label-width="80px">
                <el-input-number v-model="editor.timeoutS.value" :min="1" :max="120" class="full" />
              </el-form-item>
            </el-col>
            <el-col :span="5">
              <el-form-item label="Token 预算" label-width="100px">
                <el-input-number
                  v-model="editor.tokenBudget.value"
                  :min="1"
                  :step="500"
                  class="full"
                />
              </el-form-item>
            </el-col>
            <el-col :span="4">
              <el-form-item label="会话TTL" label-width="80px">
                <el-input-number
                  v-model="editor.sessionTtlH.value"
                  :min="1"
                  :max="720"
                  class="full"
                />
              </el-form-item>
            </el-col>
            <el-col :span="5">
              <el-form-item label="历史上限" label-width="84px">
                <el-input-number
                  v-model="editor.historyTokenLimit.value"
                  :min="256"
                  :max="32000"
                  :step="256"
                  class="full"
                />
              </el-form-item>
            </el-col>
          </el-row>

          <el-form-item label="运行时上下文">
            <div class="runtime-context">
              <el-checkbox-group
                :model-value="editor.runtimeContext.value"
                @change="onRuntimeContextChange"
              >
                <el-checkbox
                  v-for="item in RUNTIME_CONTEXT_OPTIONS"
                  :key="item.value"
                  :value="item.value"
                  :title="item.hint"
                >
                  {{ item.label }}
                </el-checkbox>
              </el-checkbox-group>
              <p class="hint">
                注入为系统消息之后的独立消息：不写入会话历史、不占历史预算，每轮按当时时间重新生成。清空后模型只能依据用户输入推断相对时间。
              </p>
            </div>
          </el-form-item>

          <el-form-item label="备注">
            <el-input v-model="editor.note.value" placeholder="本次变更说明（可选）" />
          </el-form-item>
        </el-form>
      </el-card>

      <AgentPreviewPanel
        :agent-id="agentId"
        :build-request="buildPreviewRequest"
        :variables="editor.activeVariables.value"
        :config-dirty="editor.dirty.value"
        :disabled="!editor.canSave.value || agent.status === 'ARCHIVED'"
        class="panel"
      />

      <el-row :gutter="16">
        <el-col :span="14">
          <el-card shadow="never" class="panel">
            <template #header>
              <span class="panel-title">版本列表</span>
            </template>
            <el-table :data="sortedVersions" size="small" row-key="id" highlight-current-row>
              <el-table-column label="版本" width="70">
                <template #default="{ row }">v{{ row.version_no }}</template>
              </el-table-column>
              <el-table-column label="配置哈希" width="130">
                <template #default="{ row }">
                  <span :title="row.config_hash">{{ short(row.config_hash) }}</span>
                </template>
              </el-table-column>
              <el-table-column prop="note" label="备注" min-width="140" show-overflow-tooltip />
              <el-table-column label="创建时间" width="170">
                <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
              </el-table-column>
              <el-table-column label="操作" width="150">
                <template #default="{ row }">
                  <el-button link type="primary" @click="selectedVersionId = row.id">查看</el-button>
                  <el-button link type="primary" @click="checkReadiness(row)">体检</el-button>
                </template>
              </el-table-column>
            </el-table>
            <el-alert
              v-if="versionsError"
              :title="`版本列表加载失败：${versionsError}`"
              type="error"
              show-icon
              :closable="false"
              class="publish-error"
            >
              <el-button size="small" @click="load()">重试</el-button>
            </el-alert>
            <p v-else-if="!sortedVersions.length" class="page-hint">
              该 Agent 还没有版本，先在「配置编辑」中保存一次生成 v1。
            </p>
          </el-card>
        </el-col>

        <el-col :span="10">
          <el-card shadow="never" class="panel">
            <template #header>
              <span class="panel-title">发布面板</span>
            </template>
            <el-alert
              v-if="publishError"
              :title="publishError"
              type="error"
              show-icon
              :closable="false"
              class="publish-error"
            />
            <el-alert
              v-else-if="versionsError"
              :title="`版本列表加载失败：${versionsError}`"
              type="error"
              show-icon
              :closable="false"
              class="publish-error"
            >
              <el-button size="small" @click="load()">重试</el-button>
            </el-alert>
            <el-alert
              v-else-if="!versions.length"
              title="该 Agent 还没有任何版本，保存一次配置后即可发布。"
              type="info"
              show-icon
              :closable="false"
              class="publish-error"
            >
              <el-button
                v-if="canManage && agent.status !== 'ARCHIVED'"
                size="small"
                @click="focusConfigEditor"
              >
                去配置区保存
              </el-button>
            </el-alert>
            <el-form label-width="72px" label-position="left">
              <el-form-item label="环境">
                <el-select v-model="publishEnv" class="full">
                  <el-option v-for="env in ENVIRONMENTS" :key="env" :label="env" :value="env" />
                </el-select>
              </el-form-item>
              <el-form-item label="版本">
                <el-select
                  v-model="publishVersionId"
                  placeholder="选择版本"
                  class="full"
                  :no-data-text="versionsError ? '版本列表加载失败' : '暂无版本，请先保存一次配置'"
                >
                  <el-option
                    v-for="item in sortedVersions"
                    :key="item.id"
                    :label="`v${item.version_no}`"
                    :value="item.id"
                  />
                </el-select>
              </el-form-item>
              <el-form-item label="备注">
                <el-input v-model="publishNote" placeholder="可选" />
              </el-form-item>
              <el-form-item>
                <el-button
                  type="primary"
                  :loading="publishing"
                  :disabled="!canManage || agent.status === 'ARCHIVED' || !versions.length"
                  @click="publish"
                >
                  体检并发布
                </el-button>
              </el-form-item>
            </el-form>

            <div class="config-sub">发布记录</div>
            <el-alert
              v-if="releasesError"
              :title="`发布记录加载失败：${releasesError}`"
              type="error"
              show-icon
              :closable="false"
              class="publish-error"
            >
              <el-button size="small" @click="load()">重试</el-button>
            </el-alert>
            <el-table :data="releases" size="small" row-key="release_id">
              <el-table-column prop="env" label="环境" width="70" />
              <el-table-column label="版本" width="80">
                <template #default="{ row }">
                  {{ row.version_no ?? short(row.agent_version_id) }}
                </template>
              </el-table-column>
              <el-table-column label="发布时间" min-width="140">
                <template #default="{ row }">{{ formatTime(row.released_at) }}</template>
              </el-table-column>
            </el-table>
            <p v-if="!releasesError && !releases.length" class="page-hint">还没有发布记录。</p>
          </el-card>
        </el-col>
      </el-row>
    </template>

    <el-dialog v-model="readinessVisible" title="发布体检" width="620px">
      <el-alert
        v-if="readinessError"
        :title="readinessError"
        type="error"
        show-icon
        :closable="false"
      />
      <template v-else-if="readiness">
        <div class="readiness-head">
          <el-tag :type="readiness.ready ? 'success' : 'danger'" size="small">
            {{ readiness.ready ? '全部可用' : '存在不可用引用' }}
          </el-tag>
          <span class="page-hint">v{{ readiness.version_no }} · {{ short(readiness.config_hash) }}</span>
        </div>
        <el-table :data="readiness.bindings" size="small" row-key="resource_id">
          <el-table-column prop="role" label="角色" width="90" />
          <el-table-column prop="resource_type" label="资源类型" width="130" />
          <el-table-column label="资源 ID" min-width="150">
            <template #default="{ row }"><code>{{ short(row.resource_id) }}</code></template>
          </el-table-column>
          <el-table-column label="可用" width="80">
            <template #default="{ row }">
              <el-tag :type="row.available ? 'success' : 'danger'" size="small">
                {{ row.available ? '是' : '否' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="reason" label="原因" min-width="150" show-overflow-tooltip />
        </el-table>
        <div v-if="readiness.preview" class="readiness-preview">
          最近预览：{{ readiness.preview.status }} · {{ formatTime(readiness.preview.started_at) }}
        </div>
      </template>
      <div v-else v-loading="readinessLoading" class="readiness-loading" />
    </el-dialog>

    <el-dialog v-model="copyVisible" title="复制 Agent" width="440px">
      <el-alert
        v-if="copyError"
        :title="copyError"
        type="error"
        show-icon
        :closable="false"
        class="publish-error"
      />
      <el-form label-width="80px">
        <el-form-item label="新编码">
          <el-input v-model="copyCode" placeholder="租户内唯一" />
        </el-form-item>
        <el-form-item label="名称">
          <el-input v-model="copyName" placeholder="留空则使用来源名称 (copy)" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="copyVisible = false">取消</el-button>
        <el-button
          type="primary"
          :loading="copying"
          :disabled="!copyCode.trim()"
          @click="submitCopy"
        >
          复制
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0 0;
}

.agent-chip {
  font-size: 12px;
  font-weight: 400;
  color: #6b7280;
}

.status {
  margin-left: 4px;
}

.actions {
  display: flex;
  gap: 8px;
}

.error-box {
  margin-bottom: 16px;
}

.error-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.panel {
  border-radius: 10px;
  margin-bottom: 16px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title {
  font-weight: 600;
}

.tag {
  margin-right: 4px;
}

.env-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
}

.env-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 14px 8px;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  background: #fafbff;
}

.env-name {
  font-size: 12px;
  font-weight: 600;
  color: #5a6478;
}

.env-time {
  font-size: 11px;
  color: #9aa1b1;
}

.version-select {
  width: 120px;
}

.config-meta {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
  margin-bottom: 14px;
}

.hash {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #5a6478;
}

.config-block {
  margin-bottom: 16px;
}

.config-title {
  font-weight: 600;
  font-size: 13px;
  margin-bottom: 8px;
}

.config-sub {
  margin: 10px 0 6px;
  font-size: 12px;
  color: #6b7280;
}

.message-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 180px;
  overflow: auto;
}

.message {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #f7f8fc;
}

.message-text {
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}

.publish-error {
  margin-bottom: 12px;
}

.full {
  width: 100%;
}

.runtime-context {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.runtime-context .hint {
  margin: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

.editor-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.option-hint {
  margin-left: 10px;
  font-size: 11px;
  color: #9aa1b1;
}

.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.readiness-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.readiness-preview {
  margin-top: 10px;
  font-size: 12px;
  color: #5a6478;
}

.readiness-loading {
  height: 120px;
}
</style>
