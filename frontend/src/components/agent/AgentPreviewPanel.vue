<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { ElMessageBox } from 'element-plus'
import { Loading } from '@element-plus/icons-vue'

import { ApiError, describeError } from '@/api/client'
import {
  getRunTrace,
  previewAgent,
  type PreviewRequest,
  type RunTrace,
  type SessionHistoryMessage,
  type SessionHistoryTurn,
} from '@/api/endpoints'
import JsonBlock from '@/components/JsonBlock.vue'

interface ToolCallView {
  spanId: string
  code: string
  status: string
  latencyMs: number
}

interface ChatTurn {
  id: string
  role: 'user' | 'assistant'
  content: string
  runId?: string
  traceId?: string
  status?: string
  configHash?: string
  usage?: { steps: number; tokens: number }
  tools?: ToolCallView[]
  error?: boolean
}

interface PromptVariableView {
  name: string
  type?: string
  required?: boolean
  default?: unknown
}

const props = defineProps<{
  agentId: string
  buildRequest: (input: Record<string, unknown>, sessionId: string) => PreviewRequest
  variables?: PromptVariableView[]
  configDirty?: boolean
  disabled?: boolean
  /** 受控模式：父级选中的会话标识（即 `session_key`）；为空表示新会话。 */
  sessionKey?: string | null
  /** 受控模式：服务端归一化的历史轮次；未传时保持组件自持。 */
  historyTurns?: SessionHistoryTurn[]
  /** 历史会话已过期：只读回放，禁止继续发送。 */
  expired?: boolean
  historyLoading?: boolean
  /** 服务端以运行记录重建了轮次（缺少用户消息锚点），回答可能缺失。 */
  historyReconstructed?: boolean
}>()

const emit = defineEmits<{
  (
    e: 'turn-completed',
    payload: { sessionKey: string; at: string; status: string; runtimeContext?: string[] },
  ): void
  (e: 'request-new-session'): void
}>()

const primaryVariable = computed<PromptVariableView | null>(() => {
  const list = props.variables ?? []
  return list.find((item) => item.required) ?? list[0] ?? null
})

const extraVariables = computed<PromptVariableView[]>(() =>
  (props.variables ?? []).filter((item) => item.name !== primaryVariable.value?.name),
)

const variableValues = reactive<Record<string, string>>({})

watch(
  () => props.variables,
  (list) => {
    const seen = new Set<string>()
    for (const variable of list ?? []) {
      seen.add(variable.name)
      if (variableValues[variable.name] !== undefined) continue
      const fallback = variable.default
      variableValues[variable.name] =
        fallback === undefined || fallback === null
          ? ''
          : typeof fallback === 'string'
            ? fallback
            : JSON.stringify(fallback)
    }
    for (const key of Object.keys(variableValues)) {
      if (!seen.has(key)) delete variableValues[key]
    }
  },
  { immediate: true, deep: true },
)

function coerce(variable: PromptVariableView, raw: string): unknown {
  if (variable.type === 'number' && raw.trim() !== '') {
    const parsed = Number(raw)
    return Number.isNaN(parsed) ? raw : parsed
  }
  if (variable.type === 'boolean') return raw === 'true'
  return raw
}

/** 把对话输入映射为 Prompt 变量：必填变量承接正文，其余变量取自表单。 */
function buildInput(text: string): Record<string, unknown> {
  const input: Record<string, unknown> = {}
  for (const variable of extraVariables.value) {
    const raw = variableValues[variable.name] ?? ''
    if (raw === '' && !variable.required) continue
    input[variable.name] = coerce(variable, raw)
  }
  if (primaryVariable.value) {
    input[primaryVariable.value.name] = coerce(primaryVariable.value, text)
  }
  return input
}

function newSessionId(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return `preview-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

const draftSessionId = ref(newSessionId())
/** 受控时用父级选中的会话标识续聊，自持时用本地生成的标识。 */
const sessionId = computed(() => props.sessionKey ?? draftSessionId.value)
const turns = ref<ChatTurn[]>([])
const draft = ref('')
const sending = ref(false)
const listRef = ref<HTMLElement | null>(null)
const readOnly = computed(() => props.expired === true)

const traceVisible = ref(false)
const traceLoading = ref(false)
const trace = ref<RunTrace | null>(null)
const traceError = ref<string | null>(null)

function short(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value
}

async function scrollToBottom(): Promise<void> {
  await nextTick()
  const el = listRef.value
  if (el) el.scrollTop = el.scrollHeight
}

function startNewSession(): void {
  draftSessionId.value = newSessionId()
  turns.value = []
  draft.value = ''
  if (props.historyTurns !== undefined) emit('request-new-session')
}

async function confirmNewSessionIfDirty(): Promise<boolean> {
  if (!props.configDirty || turns.value.length === 0) return true
  try {
    await ElMessageBox.confirm(
      '配置已修改，但当前会话仍固定使用首轮配置。要开启新会话并应用最新配置吗？',
      '配置已变更',
      { confirmButtonText: '开启新会话', cancelButtonText: '继续当前会话', type: 'warning' },
    )
    startNewSession()
    return true
  } catch {
    return false
  }
}

function messageText(
  message: SessionHistoryMessage | null | undefined,
  fallback: string,
): string {
  if (message?.content) return message.content
  return fallback
}

/** 历史轮次按「用户 → 助手」展开成气泡；工具条目挂在对应助手消息上。 */
function historyToTurns(history: SessionHistoryTurn[]): ChatTurn[] {
  const list: ChatTurn[] = []
  for (const turn of history) {
    list.push({
      id: `h-${turn.index}-u`,
      role: 'user',
      content: messageText(turn.user, '（内容为空）'),
    })
    const tools: ToolCallView[] = (turn.tools ?? []).map((tool, index) => ({
      spanId: `h-${turn.index}-t-${index}`,
      code: tool.name,
      status: tool.status,
      latencyMs: tool.latency_ms ?? 0,
    }))
    list.push({
      id: `h-${turn.index}-a`,
      role: 'assistant',
      content: messageText(turn.assistant, '（无回答）'),
      runId: turn.run_id ?? undefined,
      traceId: turn.trace_id ?? undefined,
      status: turn.status ?? undefined,
      error: Boolean(turn.error) || turn.status === 'FAILED',
      tools: tools.length ? tools : undefined,
    })
  }
  return list
}

/** 历史空但本地有轮次（刚发完未落库 / 服务端无法解析）时的提示。 */
const historyNotice = ref('')

// 只在会话标识真正切换时清空气泡与草稿；同会话的后续渲染一律不动本地内容。
watch(
  () => sessionId.value,
  (sid, previous) => {
    if (previous === undefined || sid === previous) return
    turns.value = []
    draft.value = ''
    historyNotice.value = ''
  },
  { immediate: true },
)

// 历史是"服务端视图"：收到非空轮次才覆盖本地；空历史保持既有气泡并说明原因。
watch(
  () => props.historyTurns,
  (history) => {
    if (history === undefined) return
    if (!history.length) {
      if (turns.value.length) historyNotice.value = '服务端暂未返回可回放的历史，当前展示本次会话内容。'
      return
    }
    historyNotice.value = ''
    turns.value = historyToTurns(history)
    void scrollToBottom()
  },
  { immediate: true },
)

function previewErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.code === 'SESSION_BUSY') return '该会话正在执行中，请稍后再试。'
    if (err.code === 'SESSION_BINDING_UNAVAILABLE') {
      return '会话绑定的引用已不可用，请开启新会话后重试。'
    }
  }
  return describeError(err)
}

async function attachTrace(turn: ChatTurn, runId: string): Promise<void> {
  try {
    const result = await getRunTrace(runId)
    turn.traceId = result.run.trace_id
    turn.configHash = result.run.config_hash ?? undefined
    turn.tools = result.entries
      .filter((entry) => entry.type === 'TOOL_CALL')
      .map((entry) => ({
        spanId: entry.span_id,
        code: entry.label,
        status: entry.status,
        latencyMs: entry.latency_ms,
      }))
  } catch {
    // 轨迹是辅助信息，失败不阻断对话
  }
}

async function send(): Promise<void> {
  const text = draft.value.trim()
  if (!text || sending.value || props.disabled || readOnly.value) return
  const canSend = await confirmNewSessionIfDirty()
  if (!canSend) return

  const activeKey = sessionId.value
  const userTurn: ChatTurn = {
    id: `u-${Date.now()}`,
    role: 'user',
    content: text,
  }
  turns.value.push(userTurn)
  draft.value = ''
  sending.value = true
  void scrollToBottom()

  try {
    const response = await previewAgent(
      props.agentId,
      props.buildRequest(buildInput(text), activeKey),
    )
    const errorText = response.error
      ? String((response.error as Record<string, unknown>).message ?? '执行失败')
      : null
    const assistantTurn: ChatTurn = {
      id: `a-${response.run_id}`,
      role: 'assistant',
      content: response.output ?? errorText ?? '（无输出）',
      runId: response.run_id,
      status: response.status,
      configHash: response.config_hash,
      usage: response.usage,
      error: response.status !== 'SUCCESS',
    }
    turns.value.push(assistantTurn)
    const injected = (response.preview_snapshot as { runtime_context?: unknown } | undefined)
      ?.runtime_context
    emit('turn-completed', {
      sessionKey: activeKey,
      at: new Date().toISOString(),
      status: response.status,
      runtimeContext: Array.isArray(injected)
        ? injected.filter((item): item is string => typeof item === 'string')
        : undefined,
    })
    void attachTrace(assistantTurn, response.run_id)
  } catch (err) {
    // 绑定失效不再静默换会话：保留当前回放内容，明确提示调用方新建。
    turns.value.push({
      id: `a-${Date.now()}`,
      role: 'assistant',
      content: previewErrorMessage(err),
      error: true,
    })
  } finally {
    sending.value = false
    void scrollToBottom()
  }
}

async function openTrace(runId: string | undefined): Promise<void> {
  if (!runId) return
  traceVisible.value = true
  traceLoading.value = true
  traceError.value = null
  trace.value = null
  try {
    trace.value = await getRunTrace(runId)
  } catch (err) {
    traceError.value = describeError(err)
  } finally {
    traceLoading.value = false
  }
}

function formatTime(value: string): string {
  return new Date(value).toLocaleTimeString()
}

defineExpose({ startNewSession })
</script>

<template>
  <el-card shadow="never" class="preview-card">
    <template #header>
      <div class="preview-head">
        <div class="head-left">
          <span class="panel-title">预览</span>
          <el-tag size="small" type="info" effect="plain">会话 {{ short(sessionId) }}</el-tag>
          <el-tag v-if="turns.length" size="small" type="warning" effect="plain">
            {{ Math.ceil(turns.length / 2) }} 轮
          </el-tag>
          <el-tag v-if="readOnly" size="small" type="danger" effect="plain">已过期</el-tag>
        </div>
        <el-button size="small" :disabled="sending" @click="startNewSession">新会话</el-button>
      </div>
    </template>

    <el-alert
      v-if="readOnly"
      type="warning"
      :closable="false"
      show-icon
      class="expired-alert"
      title="该会话已过期，只能回放历史；如需继续请新建会话。"
    >
      <el-button link type="primary" @click="startNewSession">新建会话</el-button>
    </el-alert>

    <el-alert
      v-if="historyReconstructed && turns.length"
      type="info"
      :closable="false"
      show-icon
      class="rebuild-alert"
      title="该会话缺少用户消息锚点，轮次由运行记录重建；缺失的回答显示为空。"
    />
    <el-alert
      v-if="historyNotice"
      type="info"
      :closable="false"
      show-icon
      class="rebuild-alert"
      :title="historyNotice"
    />

    <div ref="listRef" v-loading="historyLoading" class="chat-list">
      <el-empty
        v-if="!turns.length && !historyLoading"
        description="发送一条消息开始预览；配置只影响新会话。"
      />
      <div
        v-for="turn in turns"
        :key="turn.id"
        class="turn"
        :class="turn.role === 'user' ? 'turn-user' : 'turn-assistant'"
      >
        <div class="bubble" :class="{ 'bubble-error': turn.error }">
          <div class="bubble-content">{{ turn.content }}</div>
          <div v-if="turn.tools && turn.tools.length" class="tool-list">
            <el-tag
              v-for="tool in turn.tools"
              :key="tool.spanId"
              size="small"
              :type="tool.status === 'SUCCESS' ? 'success' : 'danger'"
              effect="plain"
            >
              {{ tool.code }} · {{ tool.latencyMs }}ms
            </el-tag>
          </div>
          <div v-if="turn.role === 'assistant'" class="turn-meta">
            <span v-if="turn.usage">步骤 {{ turn.usage.steps }} · {{ turn.usage.tokens }} tokens</span>
            <span v-if="turn.configHash" class="hash" :title="turn.configHash">
              {{ short(turn.configHash) }}
            </span>
            <el-button v-if="turn.runId" link size="small" type="primary" @click="openTrace(turn.runId)">
              查看轨迹
            </el-button>
          </div>
        </div>
      </div>
      <div v-if="sending" class="sending">
        <el-icon class="is-loading"><Loading /></el-icon>
        预览执行中…
      </div>
    </div>

    <div v-if="variables && variables.length" class="variable-bar">
      <span class="hint">Prompt 变量</span>
      <el-input
        v-for="variable in extraVariables"
        :key="variable.name"
        v-model="variableValues[variable.name]"
        size="small"
        class="var-input"
        :placeholder="`${variable.name}${variable.required ? ' *' : ''}`"
      />
      <span v-if="primaryVariable" class="hint">
        发送内容作为 {{ primaryVariable.name }}
      </span>
    </div>

    <div v-if="configDirty" class="dirty-hint">
      配置有未保存改动，将用于新会话；保存后可发布。
    </div>

    <div class="composer">
      <el-input
        v-model="draft"
        type="textarea"
        :rows="3"
        resize="none"
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        :disabled="disabled || readOnly"
        @keydown.enter.exact.prevent="send"
      />
      <div class="composer-actions">
        <span class="hint">{{ readOnly ? '过期会话只读' : '同一会话沿用首轮配置' }}</span>
        <el-button
          type="primary"
          :loading="sending"
          :disabled="disabled || readOnly || !draft.trim()"
          @click="send"
        >
          发送
        </el-button>
      </div>
    </div>

    <el-drawer v-model="traceVisible" title="运行轨迹" size="46%" append-to-body>
      <el-alert
        v-if="traceError"
        :title="traceError"
        type="error"
        show-icon
        :closable="false"
      />
      <template v-else-if="trace">
        <div class="trace-head">
          <el-tag size="small" :type="trace.run.status === 'SUCCESS' ? 'success' : 'danger'">
            {{ trace.run.status }}
          </el-tag>
          <span class="hint">
            {{ trace.run.kind }} · {{ trace.run.env }} · {{ trace.entries.length }} 个步骤
          </span>
        </div>
        <el-table :data="trace.entries" size="small" row-key="span_id">
          <el-table-column type="expand">
            <template #default="{ row }">
              <div class="trace-detail">
                <div class="config-sub">摘要</div>
                <JsonBlock :value="row.summary" max-height="140px" />
                <template v-if="row.content">
                  <div class="config-sub">请求</div>
                  <JsonBlock :value="row.content.request" max-height="140px" />
                  <div class="config-sub">响应</div>
                  <JsonBlock :value="row.content.response" max-height="140px" />
                </template>
              </div>
            </template>
          </el-table-column>
          <el-table-column prop="type" label="类型" width="100" />
          <el-table-column prop="label" label="步骤" min-width="130" show-overflow-tooltip />
          <el-table-column label="状态" width="90">
            <template #default="{ row }">
              <el-tag size="small" :type="row.status === 'SUCCESS' ? 'success' : 'danger'">
                {{ row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="90">
            <template #default="{ row }">
              <span :class="{ slowest: row.is_slowest }">{{ row.latency_ms }}ms</span>
            </template>
          </el-table-column>
          <el-table-column label="开始" width="100">
            <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
          </el-table-column>
        </el-table>
      </template>
      <div v-else v-loading="traceLoading" class="trace-loading" />
    </el-drawer>
  </el-card>
</template>

<style scoped>
.preview-card {
  border-radius: 10px;
}

.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.panel-title {
  font-weight: 600;
}

.expired-alert,
.rebuild-alert {
  margin-bottom: 8px;
}

.chat-list {
  height: 380px;
  overflow-y: auto;
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.turn {
  display: flex;
}

.turn-user {
  justify-content: flex-end;
}

.turn-assistant {
  justify-content: flex-start;
}

.bubble {
  max-width: 82%;
  padding: 9px 12px;
  border-radius: 10px;
  background: #f4f6fb;
}

.turn-user .bubble {
  background: #e8f0ff;
}

.bubble-error {
  background: #fef2f2;
}

.bubble-content {
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}

.tool-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: 8px;
}

.turn-meta {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 6px;
  font-size: 11px;
  color: #8a91a3;
}

.hash {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.sending {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #6b7280;
  padding-left: 4px;
}

.dirty-hint {
  margin-top: 8px;
  font-size: 12px;
  color: #b26a00;
}

.variable-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 10px;
}

.var-input {
  width: 150px;
}

.composer {
  margin-top: 10px;
}

.composer-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-top: 8px;
}

.hint {
  font-size: 12px;
  color: #9aa1b1;
}

.trace-head {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.config-sub {
  margin: 8px 0 4px;
  font-size: 12px;
  color: #6b7280;
}

.slowest {
  color: #d97706;
  font-weight: 600;
}

.trace-loading {
  height: 160px;
}
</style>
