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

const sessionId = ref(newSessionId())
const turns = ref<ChatTurn[]>([])
const draft = ref('')
const sending = ref(false)
const listRef = ref<HTMLElement | null>(null)

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
  sessionId.value = newSessionId()
  turns.value = []
  draft.value = ''
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
  if (!text || sending.value || props.disabled) return
  const canSend = await confirmNewSessionIfDirty()
  if (!canSend) return

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
      props.buildRequest(buildInput(text), sessionId.value),
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
    void attachTrace(assistantTurn, response.run_id)
  } catch (err) {
    if (err instanceof ApiError && err.code === 'SESSION_BINDING_UNAVAILABLE') {
      startNewSession()
    }
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
        </div>
        <el-button size="small" :disabled="sending" @click="startNewSession">新会话</el-button>
      </div>
    </template>

    <div ref="listRef" class="chat-list">
      <el-empty v-if="!turns.length" description="发送一条消息开始预览；配置只影响新会话。" />
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
        :disabled="disabled"
        @keydown.enter.exact.prevent="send"
      />
      <div class="composer-actions">
        <span class="hint">同一会话沿用首轮配置</span>
        <el-button type="primary" :loading="sending" :disabled="disabled || !draft.trim()" @click="send">
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
