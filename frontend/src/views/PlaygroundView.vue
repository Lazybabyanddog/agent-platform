<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, describeError } from '@/api/client'
import {
  ENVIRONMENTS,
  getSessionHistory,
  listAgentVersions,
  listAgents,
  listSessions,
  type Agent,
  type AgentVersion,
  type Environment,
  type PreviewRequest,
  type SessionHistoryTurn,
  type SessionItem,
} from '@/api/endpoints'
import AgentPreviewPanel from '@/components/agent/AgentPreviewPanel.vue'
import AgentSessionList from '@/components/agent/AgentSessionList.vue'
import { RUNTIME_CONTEXT_OPTIONS } from '@/composables/useAgentConfigEditor'

interface PromptVariableView {
  name: string
  type?: string
  required?: boolean
  default?: unknown
}

interface SnapshotRefs {
  model_id: string
  prompt_version_id: string
  tool_version_ids: string[]
  params: Record<string, unknown>
  policy: Record<string, unknown>
  runtime_context: string[]
}

const SESSION_PAGE_SIZE = 20
const HISTORY_TURNS_LIMIT = 50

const route = useRoute()
const router = useRouter()

/** URL 可能被手工改写，环境只接受合法枚举，否则回落到 DEV。 */
function normalizeEnv(value: unknown): Environment {
  const raw = typeof value === 'string' ? value : ''
  return (ENVIRONMENTS as readonly string[]).includes(raw) ? (raw as Environment) : 'DEV'
}

const agents = ref<Agent[]>([])
const agentId = ref<string>(String(route.query.agentId ?? ''))
const env = ref<Environment>(normalizeEnv(route.query.env))
const loadingAgents = ref(false)
const agentsError = ref<string | null>(null)

const refs = ref<SnapshotRefs | null>(null)
const variables = ref<PromptVariableView[]>([])
const basisVersion = ref<AgentVersion | null>(null)
const refsError = ref<string | null>(null)
const refsLoading = ref(false)

const sessions = ref<SessionItem[]>([])
const sessionTotal = ref(0)
const sessionPage = ref(1)
const sessionsLoading = ref(false)
const sessionsLoadingMore = ref(false)
const sessionsError = ref<string | null>(null)

const activeSession = ref<SessionItem | null>(null)
const historyTurns = ref<SessionHistoryTurn[]>([])
const historyLoading = ref(false)
const historyError = ref<string | null>(null)
/** 会话声明有轮次但历史解析为空：给出提示与重试，而不是空白对话区。 */
const historyNotice = ref<string | null>(null)
/** 轮次由服务端按运行记录重建，回答可能缺失。 */
const historyReconstructed = ref(false)
/** 递增用于强制重建预览面板，清掉上一个 Agent / 环境 / 会话的本地气泡。 */
const panelKey = ref(0)

let sessionsToken = 0
let historyToken = 0
/** 初始化（恢复 URL 上的会话）完成前，agent/env 的自动填充不触发清空。 */
let initialized = false

/** 连续同步调用时以累积值为基准，避免 router.replace 落地前相互覆盖。 */
let pendingQuery: Record<string, string> | null = null

/** 上一轮预览实际装配的上下文项；null 表示本次会话还没跑过，只能看版本声明。 */
const lastInjectedContext = ref<string[] | null>(null)

const declaredContext = computed(() => refs.value?.runtime_context ?? [])
const injectedContext = computed(() => lastInjectedContext.value ?? declaredContext.value)
const injectedContextSource = computed(() =>
  lastInjectedContext.value ? '本轮执行结果' : '版本声明（本轮尚未执行）',
)

function runtimeLabel(value: string): string {
  return RUNTIME_CONTEXT_OPTIONS.find((item) => item.value === value)?.label ?? value
}

function baseQuery(): Record<string, string> {
  if (pendingQuery) return pendingQuery
  const query: Record<string, string> = {}
  for (const [key, value] of Object.entries(route.query)) {
    if (typeof value === 'string' && value) query[key] = value
  }
  return query
}

/** 把 Agent / 环境 / 当前会话写回 URL，刷新后据此恢复选中会话与历史。 */
function updateRouteQuery(patch: Record<string, string | null>): void {
  const next: Record<string, string> = { ...baseQuery() }
  let changed = false
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) {
      if (key in next) {
        delete next[key]
        changed = true
      }
      continue
    }
    if (next[key] !== value) {
      next[key] = value
      changed = true
    }
  }
  if (!changed) return
  pendingQuery = next
  void router.replace({ query: next }).finally(() => {
    if (pendingQuery === next) pendingQuery = null
  })
}

function dropSessionInRoute(): void {
  updateRouteQuery({ sessionId: null })
}

const selectedAgent = computed(
  () => agents.value.find((item) => item.id === agentId.value) ?? null,
)

/** 会话绑定的是首轮快照；与当前版本 hash 不同即说明配置已变更，续聊前提示。 */
const configDirty = computed(() => {
  const bound = activeSession.value?.preview_config_hash
  return Boolean(bound && basisVersion.value && bound !== basisVersion.value.config_hash)
})

function buildRequest(input: Record<string, unknown>, sessionId: string): PreviewRequest {
  if (!refs.value) throw new Error('配置尚未加载')
  return {
    env: env.value,
    ...refs.value,
    input,
    preview_session_id: sessionId,
    timeout_s: null,
  }
}

async function loadAgents(): Promise<void> {
  loadingAgents.value = true
  agentsError.value = null
  try {
    const page = await listAgents({ page_size: 100 })
    agents.value = page.items
    if (!agentId.value && agents.value.length) {
      agentId.value = agents.value[0].id
    }
  } catch (err) {
    agentsError.value = describeError(err)
  } finally {
    loadingAgents.value = false
  }
}

/** 调试台复用详情页的预览能力：按最新版本快照解析引用后执行。 */
async function loadBasis(): Promise<void> {
  refs.value = null
  variables.value = []
  basisVersion.value = null
  lastInjectedContext.value = null
  refsError.value = null
  if (!agentId.value) return
  refsLoading.value = true
  try {
    const versionList = await listAgentVersions(agentId.value)
    const latest = [...versionList].sort((a, b) => b.version_no - a.version_no)[0]
    if (!latest) {
      refsError.value = '该 Agent 还没有版本；先在详情页配置并保存一个版本。'
      return
    }
    const snapshot = (latest.snapshot ?? {}) as {
      model?: { model_id?: string; params?: Record<string, unknown> }
      prompt?: { prompt_version_id?: string; variables?: PromptVariableView[] }
      tools?: { tool_version_id?: string }[]
      policy?: Record<string, unknown>
      runtime_context?: string[]
    }
    const modelId = snapshot.model?.model_id ?? ''
    const promptVersionId = snapshot.prompt?.prompt_version_id ?? ''
    if (!modelId || !promptVersionId) {
      refsError.value = '版本快照缺少模型或 Prompt 引用，请重新保存版本。'
      return
    }
    refs.value = {
      model_id: modelId,
      prompt_version_id: promptVersionId,
      tool_version_ids: (snapshot.tools ?? [])
        .map((item) => item.tool_version_id)
        .filter((value): value is string => Boolean(value)),
      params: snapshot.model?.params ?? {},
      policy: snapshot.policy ?? {},
      runtime_context: Array.isArray(snapshot.runtime_context)
        ? snapshot.runtime_context.filter((item): item is string => typeof item === 'string')
        : [],
    }
    variables.value = snapshot.prompt?.variables ?? []
    basisVersion.value = latest
  } catch (err) {
    refsError.value = describeError(err)
  } finally {
    refsLoading.value = false
  }
}

async function loadSessions(reset = false): Promise<void> {
  if (!agentId.value) {
    sessions.value = []
    sessionTotal.value = 0
    return
  }
  const token = ++sessionsToken
  if (reset) {
    sessionPage.value = 1
    sessionsError.value = null
    sessionsLoading.value = true
  } else {
    sessionsLoadingMore.value = true
  }
  try {
    const result = await listSessions({
      agent_id: agentId.value,
      env: env.value,
      mine_only: true,
      page: sessionPage.value,
      page_size: SESSION_PAGE_SIZE,
    })
    if (token !== sessionsToken) return
    if (reset) {
      sessions.value = result.items
    } else {
      const seen = new Set(sessions.value.map((item) => item.id))
      sessions.value = [
        ...sessions.value,
        ...result.items.filter((item) => !seen.has(item.id)),
      ]
    }
    sessionTotal.value = result.total
  } catch (err) {
    if (token === sessionsToken) sessionsError.value = describeError(err)
  } finally {
    if (token === sessionsToken) {
      sessionsLoading.value = false
      sessionsLoadingMore.value = false
    }
  }
}

function loadMoreSessions(): void {
  if (sessionsLoading.value || sessionsLoadingMore.value) return
  if (sessions.value.length >= sessionTotal.value) return
  sessionPage.value += 1
  void loadSessions(false)
}

function clearSessionState(): void {
  historyToken += 1
  activeSession.value = null
  historyTurns.value = []
  historyError.value = null
  historyNotice.value = null
  historyReconstructed.value = false
  historyLoading.value = false
  lastInjectedContext.value = null
  panelKey.value += 1
  dropSessionInRoute()
}

async function loadHistory(item: SessionItem): Promise<void> {
  const token = ++historyToken
  historyLoading.value = true
  historyError.value = null
  historyNotice.value = null
  try {
    const result = await getSessionHistory(item.id, HISTORY_TURNS_LIMIT)
    if (token !== historyToken) return
    historyTurns.value = result.turns
    historyReconstructed.value = result.reconstructed === true
    activeSession.value = result.session
    if (!result.turns.length && result.session.turn_count > 0) {
      historyNotice.value = `服务端记录到 ${result.session.turn_count} 轮，但未解析出可回放内容；可重试或查看运行记录。`
    }
  } catch (err) {
    // 读取失败不清空已有回放，避免把可读内容变成空白
    if (token === historyToken) historyError.value = describeError(err)
  } finally {
    if (token === historyToken) historyLoading.value = false
  }
}

function selectSession(item: SessionItem): void {
  if (activeSession.value?.id !== item.id) {
    // 切会话时先让出旧轮次，新历史到达前不展示上一个会话的内容
    historyTurns.value = []
    historyNotice.value = null
    historyReconstructed.value = false
  }
  updateRouteQuery({ sessionId: item.id })
  activeSession.value = item
  void loadHistory(item)
}

function startNewSession(): void {
  clearSessionState()
}

function retryHistory(): void {
  const item = activeSession.value
  if (item) {
    void loadHistory(item)
    return
  }
  // 恢复失败（activeSession 未建立）时按 URL 上的会话标识重试
  void restoreSessionFromRoute()
}

/**
 * 首轮或续聊后把会话纳入列表并同步选中态。
 * 本轮气泡已由面板本地渲染，服务端历史可能尚未落库；这里不重载历史，
 * 否则会用空历史覆盖刚产生的对话。选中态变化时面板按会话标识判断，不会重置气泡。
 */
async function adoptSession(sessionKey: string): Promise<void> {
  await loadSessions(true)
  const row = sessions.value.find((item) => item.session_key === sessionKey)
  if (!row) return
  updateRouteQuery({ sessionId: row.id })
  activeSession.value = row
}

/**
 * 刷新/深链进入时按 URL 上的 sessionId 恢复会话与历史。
 * 会话所属 Agent/环境以服务端返回为准，避免 URL 与数据不一致。
 */
async function restoreSessionFromRoute(): Promise<void> {
  const target = String(route.query.sessionId ?? '')
  if (!target) return
  const token = ++historyToken
  historyLoading.value = true
  historyError.value = null
  historyNotice.value = null
  try {
    const result = await getSessionHistory(target, HISTORY_TURNS_LIMIT)
    if (token !== historyToken) return
    agentId.value = result.session.agent_id
    env.value = result.session.env
    historyTurns.value = result.turns
    historyReconstructed.value = result.reconstructed === true
    activeSession.value = result.session
    if (!result.turns.length && result.session.turn_count > 0) {
      historyNotice.value = `服务端记录到 ${result.session.turn_count} 轮，但未解析出可回放内容；可重试或查看运行记录。`
    }
  } catch (err) {
    if (token !== historyToken) return
    historyError.value = describeError(err)
    // 会话不存在或无权访问时清掉失效标识；其他错误保留标识，刷新或重试仍可恢复
    if (err instanceof ApiError && (err.status === 404 || err.status === 403)) {
      dropSessionInRoute()
    }
  } finally {
    if (token === historyToken) historyLoading.value = false
  }
}

/** 续聊成功后只回填该行的轮数与活动时间，避免整表刷新丢失当前选中态。 */
function onTurnCompleted(payload: {
  sessionKey: string
  at: string
  runtimeContext?: string[]
}): void {
  // 优先展示执行实际装配的结果；缺字段说明后端未回传，退回版本声明
  if (payload.runtimeContext) lastInjectedContext.value = payload.runtimeContext
  const row = sessions.value.find((item) => item.session_key === payload.sessionKey)
  if (!row) {
    void adoptSession(payload.sessionKey)
    return
  }
  row.turn_count += 1
  row.last_active_at = payload.at
  if (activeSession.value?.id === row.id) {
    activeSession.value = {
      ...activeSession.value,
      turn_count: row.turn_count,
      last_active_at: payload.at,
    }
  }
}

watch(
  agentId,
  () => {
    void loadBasis()
  },
  { immediate: true },
)

/** 切换 Agent / 环境：丢弃当前会话，按新范围重新拉取列表。 */
function resetScope(): void {
  sessions.value = []
  sessionTotal.value = 0
  clearSessionState()
  void loadSessions(true)
}

watch([agentId, env], () => {
  if (!initialized) return
  updateRouteQuery({ agentId: agentId.value, env: env.value })
  resetScope()
})

onMounted(async () => {
  try {
    await loadAgents()
    // 先按 URL 恢复会话（可能修正所属 Agent / 环境），再按最终范围拉列表
    await restoreSessionFromRoute()
    updateRouteQuery({ agentId: agentId.value, env: env.value })
    await loadSessions(true)
  } finally {
    initialized = true
  }
})
</script>

<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">调试台</h2>
      <span class="page-hint">预览执行：按引用装配，不产生版本、不改发布指针。</span>
    </div>

    <el-alert v-if="agentsError" :title="agentsError" type="error" show-icon :closable="false" />

    <el-card shadow="never" class="panel">
      <div class="run-form">
        <el-select
          v-model="agentId"
          placeholder="选择 Agent"
          class="agent-select"
          filterable
          :loading="loadingAgents"
        >
          <el-option
            v-for="item in agents"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>

        <el-radio-group v-model="env">
          <el-radio-button v-for="item in ENVIRONMENTS" :key="item" :value="item">
            {{ item }}
          </el-radio-button>
        </el-radio-group>

        <el-button link type="primary" :disabled="!selectedAgent" @click="selectedAgent && $router.push(`/agents/${selectedAgent.id}`)">
          在详情页编辑配置
        </el-button>
      </div>

      <div v-loading="refsLoading" class="basis">
        <template v-if="basisVersion">
          <el-tag size="small" type="info">按 v{{ basisVersion.version_no }} 配置预览</el-tag>
          <span class="hash" :title="basisVersion.config_hash">
            config_hash {{ basisVersion.config_hash.slice(0, 12) }}…
          </span>
          <span class="runtime">
            <span class="runtime-label">运行时上下文</span>
            <template v-if="injectedContext.length">
              <el-tag
                v-for="item in injectedContext"
                :key="`ctx-${item}`"
                size="small"
                type="success"
                effect="plain"
              >
                {{ runtimeLabel(item) }}
              </el-tag>
              <span class="hash">来源：{{ injectedContextSource }}</span>
            </template>
            <span v-else class="page-hint">未注入：当前时间与时区不会出现在 Prompt 里</span>
          </span>
        </template>
        <el-alert
          v-else-if="refsError"
          :title="refsError"
          type="warning"
          show-icon
          :closable="false"
        />
        <span v-else class="page-hint">选择 Agent 后加载最新版本配置。</span>
      </div>
    </el-card>

    <el-row :gutter="16">
      <el-col :xs="24" :md="8" :lg="7">
        <AgentSessionList
          :sessions="sessions"
          :total="sessionTotal"
          :loading="sessionsLoading"
          :loading-more="sessionsLoadingMore"
          :error="sessionsError"
          :selected-id="activeSession?.id ?? null"
          class="panel"
          @select="selectSession"
          @load-more="loadMoreSessions"
          @retry="loadSessions(true)"
          @new-session="startNewSession"
        />
      </el-col>

      <el-col :xs="24" :md="16" :lg="17">
        <el-alert
          v-if="historyError"
          :title="historyError"
          type="error"
          show-icon
          :closable="false"
          class="panel"
        >
          <el-button link type="primary" @click="retryHistory">重试</el-button>
        </el-alert>

        <el-alert
          v-if="historyNotice"
          :title="historyNotice"
          type="warning"
          show-icon
          :closable="false"
          class="panel"
        >
          <el-button link type="primary" @click="retryHistory">重试</el-button>
        </el-alert>

        <AgentPreviewPanel
          :key="`${agentId}:${env}:${panelKey}`"
          v-if="refs || activeSession"
          :agent-id="agentId"
          :build-request="buildRequest"
          :variables="variables"
          :disabled="!refs"
          :config-dirty="configDirty"
          :session-key="activeSession?.session_key ?? null"
          :history-turns="historyTurns"
          :expired="activeSession?.expired ?? false"
          :history-loading="historyLoading"
          :history-reconstructed="historyReconstructed"
          class="panel"
          @turn-completed="onTurnCompleted"
          @request-new-session="startNewSession"
        />

        <el-card v-else v-loading="refsLoading" shadow="never" class="panel empty-panel">
          <el-empty description="选择有可用版本的 Agent 后开始预览。" :image-size="72" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-head .page-title {
  margin: 0;
}

.panel {
  border-radius: 10px;
  margin-bottom: 16px;
}

.run-form {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.agent-select {
  width: 280px;
}

.basis {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 32px;
}

.hash {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #5a6478;
}

.runtime {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-left: 4px;
}

.runtime-label {
  font-size: 12px;
  color: #6b7280;
}

.empty-panel {
  min-height: 320px;
}
</style>
