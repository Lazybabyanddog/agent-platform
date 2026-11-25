<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { describeError } from '@/api/client'
import {
  listSecrets,
  probeMcpTool,
  type McpProbeResult,
  type McpProbeTool,
  type Secret,
} from '@/api/endpoints'
import { useSessionStore } from '@/stores/session'

type Spec = Record<string, unknown>

const props = withDefaults(
  defineProps<{
    modelValue: Spec
    readonly?: boolean
    errors?: Record<string, string>
    /** 保存成功后的非阻塞提示，键为后端字段路径（如 spec.input_schema.properties.date） */
    warnings?: Record<string, string>
    /** 工具级描述：用来解释「留空继承」并判断两处都为空时保存会被拒绝 */
    toolDescription?: string
    type?: string
  }>(),
  {
    readonly: false,
    errors: () => ({}),
    warnings: () => ({}),
    toolDescription: '',
    type: 'HTTP',
  },
)

const emit = defineEmits<{
  'update:modelValue': [Spec]
  /** 探测选中远端工具：把用途描述交给上层，去填充工具级描述 */
  'remote-tool': [{ name: string; description: string | null }]
}>()

const session = useSessionStore()

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const AUTH_TYPES = [
  { value: 'none', label: '无鉴权' },
  { value: 'bearer', label: 'Bearer Token' },
  { value: 'header', label: '自定义 Header' },
]
const KNOWN_KEYS = new Set([
  'description',
  'method',
  'transport',
  'tool_name',
  'url',
  'timeout_s',
  'headers',
  'auth',
  'input_schema',
  'output_schema',
  'model_schema',
  'returns',
])
const UUID_PATTERN = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/
const MAX_TOOL_NAME_LENGTH = 128

/** 控制台只开放远程 streamable_http：平台不托管远端进程，stdio 不成立 */
const TRANSPORTS = [{ value: 'streamable_http', label: 'streamable_http（远程 HTTP）' }]
const VERDICT_LABELS: Record<string, string> = {
  OK: '探测成功',
  MCP_PROBE_AUTH_FAILED: '认证失败',
  MCP_PROBE_TIMEOUT: '请求超时',
  MCP_PROBE_UNREACHABLE: '无法连接',
  MCP_PROBE_PROTOCOL_ERROR: '远端未按 MCP 协议响应',
}

const isHttp = computed(() => props.type === 'HTTP')
const isMcp = computed(() => props.type === 'MCP')
const isRaw = computed(() => !isHttp.value && !isMcp.value)

const specHint = computed(() =>
  isHttp.value
    ? '请求方法、请求地址、超时、请求头与鉴权会写入版本快照；保存后生成不可变版本。'
    : '服务端地址、远端工具名、超时、请求头与鉴权会写入版本快照；保存后生成不可变版本。',
)

const method = ref('POST')
const transport = ref('streamable_http')
const toolName = ref('')
const url = ref('')
const timeout = ref(15)
const authType = ref('none')
const secretRef = ref('')
const headerName = ref('')
const headersText = ref('{}')
const inputSchemaText = ref('')
const outputSchemaText = ref('')
const rawText = ref('{}')
const jsonErrors = ref<Record<string, string>>({})

/** 模型侧只能改注解：结构一律由入参 Schema 派生，避免产出被后端拒绝的契约 */
const ANNOTATION_KEYS = ['description', 'title', 'format'] as const
type ParamNote = { description: string; title: string; format: string }
const descriptionText = ref('')
const terminalStatuses = ref<string[]>([])
const modelNotes = ref<Record<string, ParamNote>>({})

const probing = ref(false)
const probeResult = ref<McpProbeResult | null>(null)
const probeError = ref('')
const selectedRemote = ref('')

const secretOptions = ref<Secret[]>([])
const secretLoading = ref(false)
const secretLoadError = ref('')
const secretsLoaded = ref(false)
const canManageSecrets = computed(() => session.can('secret:manage'))

const extraKeys = computed(() =>
  Object.keys(props.modelValue).filter((key) => !KNOWN_KEYS.has(key)),
)

const auth = computed<Record<string, unknown>>(() => {
  const value: Record<string, unknown> = { type: authType.value }
  if (authType.value !== 'none') value.secret_ref_id = secretRef.value.trim()
  if (authType.value === 'header') value.name = headerName.value.trim()
  return value
})

function textOf(value: unknown): string {
  return value === undefined || value === null ? '' : JSON.stringify(value, null, 2)
}

function parseJson(text: string, field: string): unknown {
  if (!text.trim()) {
    delete jsonErrors.value[field]
    return undefined
  }
  try {
    const parsed = JSON.parse(text) as unknown
    delete jsonErrors.value[field]
    return parsed
  } catch {
    jsonErrors.value[field] = 'JSON 格式不合法'
    return undefined
  }
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/** 安静解析：编辑过程中不写 jsonErrors，供派生用 */
function readJsonQuiet(text: string): unknown {
  if (!text.trim()) return undefined
  try {
    return JSON.parse(text) as unknown
  } catch {
    return undefined
  }
}

const inputSchemaValue = computed<Record<string, unknown> | null>(() => {
  const parsed = readJsonQuiet(inputSchemaText.value)
  return isPlainObject(parsed) ? parsed : null
})

const requiredNames = computed<string[]>(() => {
  const schema = inputSchemaValue.value
  if (!schema || !Array.isArray(schema.required)) return []
  return schema.required.filter((item): item is string => typeof item === 'string')
})

const inputProperties = computed<Record<string, Record<string, unknown>>>(() => {
  const schema = inputSchemaValue.value
  const properties = schema && isPlainObject(schema.properties) ? schema.properties : null
  if (!properties) return {}
  const result: Record<string, Record<string, unknown>> = {}
  for (const [name, value] of Object.entries(properties)) {
    if (!isPlainObject(value)) return {}
    result[name] = value
  }
  return result
})

/**
 * 嵌套对象与 anyOf/oneOf/allOf 无法逐参数镜像：这类契约只在页面上只读，
 * 否则派生出的 model_schema 会与入参 Schema 结构不一致并被后端拒绝。
 */
const modelContractBlocked = computed(() => {
  const schema = inputSchemaValue.value
  const properties = schema && isPlainObject(schema.properties) ? schema.properties : null
  if (!properties) return false
  return Object.values(properties).some((value) => {
    if (!isPlainObject(value)) return true
    if (isPlainObject(value.properties)) return true
    if (value.items !== undefined) return true
    return ['anyOf', 'oneOf', 'allOf'].some((key) => Array.isArray(value[key]))
  })
})

const paramRows = computed(() =>
  Object.entries(inputProperties.value).map(([name, schema]) => ({
    name,
    type: typeof schema.type === 'string' ? schema.type : 'any',
    required: requiredNames.value.includes(name),
  })),
)

const descriptionPlaceholder = computed(() =>
  props.toolDescription.trim()
    ? `留空则继承工具级描述：${props.toolDescription.trim()}`
    : '两处都为空时无法保存：模型靠这段描述判断何时使用该工具',
)

function noteFor(name: string): ParamNote {
  return modelNotes.value[name] ?? { description: '', title: '', format: '' }
}

function updateNote(name: string, key: keyof ParamNote, value: string): void {
  const next: ParamNote = { ...noteFor(name), [key]: value }
  if (!next.description.trim() && !next.title.trim() && !next.format.trim()) {
    delete modelNotes.value[name]
  } else {
    modelNotes.value[name] = next
  }
  emitUpdate()
}

function notesFromModelSchema(value: unknown): Record<string, ParamNote> {
  const notes: Record<string, ParamNote> = {}
  if (!isPlainObject(value) || !isPlainObject(value.properties)) return notes
  for (const [name, raw] of Object.entries(value.properties)) {
    if (!isPlainObject(raw)) continue
    const note: ParamNote = {
      description: typeof raw.description === 'string' ? raw.description : '',
      title: typeof raw.title === 'string' ? raw.title : '',
      format: typeof raw.format === 'string' ? raw.format : '',
    }
    if (note.description || note.title || note.format) notes[name] = note
  }
  return notes
}

/** 告警按参数名回填：后端给的字段路径是 spec.input_schema.properties.<name> */
function warningFor(name: string): string {
  return props.warnings[`spec.input_schema.properties.${name}`] ?? ''
}

type MirrorResult =
  | { action: 'keep' }
  | { action: 'delete' }
  | { action: 'set'; value: Record<string, unknown> }

/**
 * 结构镜像：骨架完全来自入参 Schema，页面只把注解叠加回去。
 * keep = 无法镜像或结构不可拆，保留原 model_schema；delete = 作者清空了全部注解，回退到不声明。
 */
function mirrorModelSchema(inputSchema: Record<string, unknown>): MirrorResult {
  const properties = isPlainObject(inputSchema.properties) ? inputSchema.properties : null
  if (!properties || modelContractBlocked.value) return { action: 'keep' }

  const activeNotes = Object.fromEntries(
    Object.entries(modelNotes.value).filter(([name]) => name in properties),
  ) as Record<string, ParamNote>
  // 没有任何注解时模型侧与入参 Schema 完全等价，无需声明 model_schema；
  // 这里必须 delete 而不是保留旧值，否则作者清空全部注解后旧说明会随版本固化。
  if (!Object.keys(activeNotes).length) return { action: 'delete' }

  const mirrored: Record<string, unknown> = { ...inputSchema }
  const nextProperties: Record<string, unknown> = {}
  for (const [name, raw] of Object.entries(properties)) {
    if (!isPlainObject(raw)) return { action: 'keep' }
    const paramSchema: Record<string, unknown> = {}
    for (const [key, value] of Object.entries(raw)) {
      if (!(ANNOTATION_KEYS as readonly string[]).includes(key)) paramSchema[key] = value
    }
    const note = activeNotes[name]
    if (note) {
      for (const key of ANNOTATION_KEYS) {
        const text = note[key].trim()
        if (text) paramSchema[key] = text
      }
    }
    nextProperties[name] = paramSchema
  }
  mirrored.properties = nextProperties
  return { action: 'set', value: mirrored }
}

function buildSpec(): Spec {
  if (isRaw.value) {
    const parsed = parseJson(rawText.value, 'spec')
    return isPlainObject(parsed) ? parsed : { ...props.modelValue }
  }

  // 以原 spec 为基底，表单只覆盖已知字段，未知键原样保留
  const spec: Spec = { ...props.modelValue }
  if (isHttp.value) {
    // 后端对 spec 是浅合并，不属于该类型的关键键必须显式删掉，否则会跟着版本固化
    delete spec.transport
    delete spec.tool_name
    spec.method = method.value
  } else {
    delete spec.method
    spec.transport = transport.value
    spec.tool_name = toolName.value.trim()
  }
  spec.url = url.value.trim()
  spec.timeout_s = timeout.value
  spec.headers = parseJson(headersText.value, 'spec.headers') ?? {}
  spec.auth = auth.value

  const inputSchema = parseJson(inputSchemaText.value, 'spec.input_schema')
  if (inputSchema === undefined) {
    delete spec.input_schema
  } else {
    spec.input_schema = inputSchema
  }

  const outputSchema = parseJson(outputSchemaText.value, 'spec.output_schema')
  if (outputSchema === undefined) {
    delete spec.output_schema
  } else {
    spec.output_schema = outputSchema
  }

  const description = descriptionText.value.trim()
  if (description) {
    spec.description = description
  } else {
    delete spec.description
  }

  const statuses = terminalStatuses.value.map((item) => item.trim()).filter(Boolean)
  if (statuses.length) {
    spec.returns = { terminal_statuses: [...new Set(statuses)] }
  } else {
    delete spec.returns
  }

  if (isPlainObject(spec.input_schema)) {
    const mirrored = mirrorModelSchema(spec.input_schema)
    if (mirrored.action === 'set') {
      spec.model_schema = mirrored.value
    } else if (mirrored.action === 'delete') {
      delete spec.model_schema
    }
  }
  return spec
}

function emitUpdate(): void {
  emit('update:modelValue', buildSpec())
}

function syncFromModel(value: Spec): void {
  rawText.value = Object.keys(value).length ? textOf(value) : '{}'
  method.value = typeof value.method === 'string' ? value.method.toUpperCase() : 'POST'
  transport.value =
    typeof value.transport === 'string' && value.transport ? value.transport : 'streamable_http'
  toolName.value = typeof value.tool_name === 'string' ? value.tool_name : ''
  url.value = typeof value.url === 'string' ? value.url : ''
  timeout.value = typeof value.timeout_s === 'number' ? value.timeout_s : 15
  const authValue = isPlainObject(value.auth) ? value.auth : { type: 'none' }
  authType.value = typeof authValue.type === 'string' ? authValue.type : 'none'
  secretRef.value = typeof authValue.secret_ref_id === 'string' ? authValue.secret_ref_id : ''
  headerName.value = typeof authValue.name === 'string' ? authValue.name : ''
  headersText.value = isPlainObject(value.headers) ? textOf(value.headers) : '{}'
  inputSchemaText.value = isPlainObject(value.input_schema) ? textOf(value.input_schema) : ''
  outputSchemaText.value = isPlainObject(value.output_schema) ? textOf(value.output_schema) : ''
  descriptionText.value = typeof value.description === 'string' ? value.description : ''
  terminalStatuses.value =
    isPlainObject(value.returns) && Array.isArray(value.returns.terminal_statuses)
      ? value.returns.terminal_statuses.filter((item): item is string => typeof item === 'string')
      : []
  modelNotes.value = notesFromModelSchema(value.model_schema)
  jsonErrors.value = {}
}

/** 供父级保存前调用：返回字段级错误，为空表示可提交。 */
function validate(): Record<string, string> {
  const errors: Record<string, string> = {}

  if (isRaw.value) {
    const parsed = parseJson(rawText.value, 'spec')
    if (jsonErrors.value['spec']) {
      errors['spec'] = jsonErrors.value['spec']
    } else if (!isPlainObject(parsed)) {
      errors['spec'] = '工具定义必须是 JSON 对象'
    }
    return errors
  }

  if (isHttp.value) {
    if (!METHODS.includes(method.value)) {
      errors['spec.method'] = '不支持的请求方法'
    }
  } else {
    if (!TRANSPORTS.some((item) => item.value === transport.value)) {
      errors['spec.transport'] = 'MCP 传输方式仅支持 streamable_http'
    }
    const remoteName = toolName.value.trim()
    if (!remoteName) {
      errors['spec.tool_name'] = '请填写远端工具名'
    } else if (remoteName.length > MAX_TOOL_NAME_LENGTH) {
      errors['spec.tool_name'] = `远端工具名最多 ${MAX_TOOL_NAME_LENGTH} 个字符`
    }
  }

  const target = url.value.trim()
  if (!target) {
    errors['spec.url'] = '请填写请求地址'
  } else {
    try {
      const parsed = new URL(target)
      if (!['http:', 'https:'].includes(parsed.protocol)) {
        errors['spec.url'] = '仅支持 http(s) 地址'
      }
    } catch {
      errors['spec.url'] = '请输入合法的绝对地址'
    }
  }

  if (!Number.isFinite(timeout.value) || timeout.value <= 0 || timeout.value > 60) {
    errors['spec.timeout_s'] = '超时需在 (0, 60] 秒之间'
  }

  if (jsonErrors.value['spec.headers']) {
    errors['spec.headers'] = jsonErrors.value['spec.headers']
  } else if (!isPlainObject(parseJson(headersText.value, 'spec.headers') ?? {})) {
    errors['spec.headers'] = '请求头必须是 JSON 对象'
  }

  if (authType.value !== 'none') {
    if (!UUID_PATTERN.test(secretRef.value.trim())) {
      errors['spec.auth.secret_ref_id'] = '请填写 Secret 的 UUID'
    }
    if (authType.value === 'header' && !headerName.value.trim()) {
      errors['spec.auth.name'] = '请填写 Header 名称'
    }
  }

  const inputSchema = parseJson(inputSchemaText.value, 'spec.input_schema')
  if (jsonErrors.value['spec.input_schema']) {
    errors['spec.input_schema'] = jsonErrors.value['spec.input_schema']
  } else if (inputSchema !== undefined) {
    if (!isPlainObject(inputSchema)) {
      errors['spec.input_schema'] = '入参 Schema 必须是 JSON 对象'
    } else if (inputSchema.type !== undefined && inputSchema.type !== 'object') {
      errors['spec.input_schema'] = "入参 Schema 的 type 必须是 'object'"
    }
  }

  const outputSchema = parseJson(outputSchemaText.value, 'spec.output_schema')
  if (jsonErrors.value['spec.output_schema']) {
    errors['spec.output_schema'] = jsonErrors.value['spec.output_schema']
  } else if (outputSchema !== undefined) {
    if (!isPlainObject(outputSchema)) {
      errors['spec.output_schema'] = '出参 Schema 必须是 JSON 对象'
    } else if (outputSchema.type !== undefined && outputSchema.type !== 'object') {
      errors['spec.output_schema'] = "出参 Schema 的 type 必须是 'object'"
    }
  }

  if (!descriptionText.value.trim() && !props.toolDescription.trim()) {
    errors['spec.description'] = '工具级描述也为空：请在这里填写用途描述，模型靠它判断何时使用该工具'
  }

  return errors
}

function fieldError(field: string): string {
  return props.errors[field] ?? jsonErrors.value[field] ?? ''
}

function onJsonInput(field: string, value: string): void {
  if (field === 'spec.headers') {
    headersText.value = value
  } else if (field === 'spec.input_schema') {
    inputSchemaText.value = value
  } else if (field === 'spec.output_schema') {
    outputSchemaText.value = value
  } else {
    rawText.value = value
  }
  parseJson(value, field)
  emitUpdate()
}

const canProbe = computed(() => isMcp.value && !props.readonly)

async function runProbe(): Promise<void> {
  probeError.value = ''
  probeResult.value = null
  selectedRemote.value = ''

  const target = url.value.trim()
  if (!target) {
    // 地址为空时不发请求：没必要让服务端替用户验证一个显然不合格的输入
    probeError.value = '请先填写服务端地址'
    return
  }
  const headers = parseJson(headersText.value, 'spec.headers')
  if (jsonErrors.value['spec.headers']) {
    probeError.value = '请求头不是合法 JSON，请先修正'
    return
  }

  probing.value = true
  try {
    probeResult.value = await probeMcpTool({
      transport: transport.value,
      url: target,
      auth: auth.value,
      headers: isPlainObject(headers) ? headers : {},
      timeout_s: timeout.value,
    })
  } catch (err) {
    probeError.value = describeError(err)
  } finally {
    probing.value = false
  }
}

function applyRemoteTool(tool: McpProbeTool): void {
  selectedRemote.value = tool.name
  toolName.value = tool.name
  // 只填探测能拿到的字段，使用者已填的地址、鉴权、出参一律不动
  if (isPlainObject(tool.input_schema)) {
    inputSchemaText.value = textOf(tool.input_schema)
    delete jsonErrors.value['spec.input_schema']
  }
  emit('remote-tool', { name: tool.name, description: tool.description ?? null })
  emitUpdate()
}

async function loadSecrets(): Promise<void> {
  if (!canManageSecrets.value || secretsLoaded.value) return
  secretLoading.value = true
  secretLoadError.value = ''
  try {
    const page = await listSecrets()
    secretOptions.value = page.items
    secretsLoaded.value = true
  } catch (err) {
    secretLoadError.value = describeError(err)
  } finally {
    secretLoading.value = false
  }
}

watch(authType, (value) => {
  if (value !== 'none') void loadSecrets()
})

watch(
  () => props.type,
  () => {
    probeResult.value = null
    probeError.value = ''
    selectedRemote.value = ''
  },
)

watch(
  () => props.modelValue,
  (value) => {
    if (JSON.stringify(value) !== JSON.stringify(buildSpec())) syncFromModel(value)
  },
  { deep: true },
)

onMounted(() => {
  syncFromModel(props.modelValue)
  if (authType.value !== 'none') void loadSecrets()
})

defineExpose({ validate, getSpec: buildSpec })
</script>

<template>
  <div class="spec-editor">
    <template v-if="isRaw">
      <el-alert
        :title="`${type} 类型工具暂不支持结构化表单，以下为完整定义 JSON`"
        type="info"
        show-icon
        :closable="false"
        class="notice"
      />
      <el-input
        :model-value="rawText"
        type="textarea"
        :autosize="{ minRows: 12, maxRows: 24 }"
        :disabled="readonly"
        class="code-input"
        @update:model-value="onJsonInput('spec', $event)"
      />
      <p v-if="fieldError('spec')" class="field-error">{{ fieldError('spec') }}</p>
    </template>

    <template v-else>
      <el-form label-width="104px" label-position="left">
        <el-form-item label="用途描述" :error="fieldError('spec.description')">
          <el-input
            v-model="descriptionText"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            maxlength="500"
            show-word-limit
            :disabled="readonly"
            :placeholder="descriptionPlaceholder"
            @input="emitUpdate"
          />
        </el-form-item>
        <p class="hint hint-inline">
          版本内描述优先；留空则继承工具级描述，两处都为空时保存会被拒绝。
        </p>

        <el-row v-if="isHttp" :gutter="12">
          <el-col :span="7">
            <el-form-item label="请求方法" :error="fieldError('spec.method')">
              <el-select v-model="method" :disabled="readonly" @change="emitUpdate">
                <el-option v-for="item in METHODS" :key="item" :label="item" :value="item" />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col :span="17">
            <el-form-item label="请求地址" :error="fieldError('spec.url')">
              <el-input
                v-model="url"
                :disabled="readonly"
                placeholder="https://api.example.com/v1/action"
                @input="emitUpdate"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <template v-else>
          <el-row :gutter="12">
            <el-col :span="8">
              <el-form-item label="传输方式" :error="fieldError('spec.transport')">
                <el-select v-model="transport" :disabled="readonly" @change="emitUpdate">
                  <el-option
                    v-for="item in TRANSPORTS"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-select>
              </el-form-item>
            </el-col>
            <el-col :span="16">
              <el-form-item label="服务端地址" :error="fieldError('spec.url')">
                <el-input
                  v-model="url"
                  :disabled="readonly"
                  placeholder="https://mcp.example.com/mcp"
                  @input="emitUpdate"
                />
              </el-form-item>
            </el-col>
          </el-row>
          <p class="hint hint-inline">
            只支持远程 streamable_http；平台不托管本地进程，因此没有 stdio 传输。
          </p>

          <el-row :gutter="12">
            <el-col :span="16">
              <el-form-item label="远端工具名" :error="fieldError('spec.tool_name')">
                <el-input
                  v-model="toolName"
                  :disabled="readonly"
                  maxlength="128"
                  placeholder="如 search_documents"
                  @input="emitUpdate"
                />
              </el-form-item>
            </el-col>
            <el-col :span="8">
              <el-form-item label-width="0">
                <el-button :loading="probing" :disabled="!canProbe" @click="runProbe">
                  探测远端工具
                </el-button>
              </el-form-item>
            </el-col>
          </el-row>

          <el-alert
            v-if="probeError"
            :title="probeError"
            type="warning"
            show-icon
            :closable="false"
            class="notice"
          />

          <template v-if="probeResult">
            <el-alert
              v-if="probeResult.verdict !== 'OK'"
              :title="`${VERDICT_LABELS[probeResult.verdict] ?? probeResult.verdict}：${probeResult.error_summary ?? ''}`"
              type="error"
              show-icon
              :closable="false"
              class="notice"
            />
            <el-alert
              v-else-if="!probeResult.tools.length"
              title="远端连接成功，但没有声明任何工具"
              type="info"
              show-icon
              :closable="false"
              class="notice"
            />
            <div v-else class="probe-list">
              <p class="probe-title">
                远端可用工具（{{ probeResult.tools.length }}），点击条目填充契约：
              </p>
              <div
                v-for="tool in probeResult.tools"
                :key="tool.name"
                class="probe-item"
                :class="{ 'is-active': selectedRemote === tool.name }"
                @click="applyRemoteTool(tool)"
              >
                <span class="probe-name">{{ tool.name }}</span>
                <span class="probe-desc">{{ tool.description ?? '无描述' }}</span>
              </div>
            </div>
          </template>
        </template>

        <el-row :gutter="12">
          <el-col :span="7">
            <el-form-item label="超时（秒）" :error="fieldError('spec.timeout_s')">
              <el-input-number
                v-model="timeout"
                :min="0.1"
                :max="60"
                :step="0.5"
                :disabled="readonly"
                controls-position="right"
                class="timeout"
                @change="emitUpdate"
              />
            </el-form-item>
          </el-col>
          <el-col :span="7">
            <el-form-item label="鉴权方式">
              <el-select v-model="authType" :disabled="readonly" @change="emitUpdate">
                <el-option
                  v-for="item in AUTH_TYPES"
                  :key="item.value"
                  :label="item.label"
                  :value="item.value"
                />
              </el-select>
            </el-form-item>
          </el-col>
          <el-col v-if="authType !== 'none'" :span="10">
            <el-form-item label="Secret ID" :error="fieldError('spec.auth.secret_ref_id')">
              <el-select
                v-if="canManageSecrets"
                v-model="secretRef"
                filterable
                allow-create
                default-first-option
                :loading="secretLoading"
                :disabled="readonly"
                placeholder="选择或输入 Secret ID"
                @change="emitUpdate"
              >
                <el-option
                  v-for="item in secretOptions"
                  :key="item.id"
                  :label="`${item.name}（${item.id.slice(0, 8)}）`"
                  :value="item.id"
                />
              </el-select>
              <el-input
                v-else
                v-model="secretRef"
                :disabled="readonly"
                placeholder="secret_ref_id（UUID）"
                @input="emitUpdate"
              />
            </el-form-item>
          </el-col>
        </el-row>

        <el-alert
          v-if="authType !== 'none' && !canManageSecrets"
          title="当前账号没有 secret:manage 权限，无法列出凭据；可向管理员索取凭据 ID 后手工填写。"
          type="warning"
          show-icon
          :closable="false"
          class="notice"
        />
        <el-alert
          v-else-if="secretLoadError"
          :title="`凭据列表加载失败：${secretLoadError}，可手工填写凭据 ID。`"
          type="warning"
          show-icon
          :closable="false"
          class="notice"
        />

        <el-form-item
          v-if="authType === 'header'"
          label="Header 名称"
          :error="fieldError('spec.auth.name')"
        >
          <el-input
            v-model="headerName"
            :disabled="readonly"
            placeholder="如 X-API-Key"
            @input="emitUpdate"
          />
        </el-form-item>

        <el-form-item label="请求头" :error="fieldError('spec.headers')">
          <el-input
            :model-value="headersText"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 5 }"
            :disabled="readonly"
            class="code-input"
            placeholder='{"X-Trace": "demo"}'
            @update:model-value="onJsonInput('spec.headers', $event)"
          />
        </el-form-item>

        <el-form-item label="入参 Schema" :error="fieldError('spec.input_schema')">
          <el-input
            :model-value="inputSchemaText"
            type="textarea"
            :autosize="{ minRows: 4, maxRows: 10 }"
            :disabled="readonly"
            class="code-input"
            placeholder='{"type": "object", "properties": {"text": {"type": "string"}}}'
            @update:model-value="onJsonInput('spec.input_schema', $event)"
          />
        </el-form-item>

        <el-form-item label="出参 Schema" :error="fieldError('spec.output_schema')">
          <el-input
            :model-value="outputSchemaText"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 6 }"
            :disabled="readonly"
            class="code-input"
            placeholder="可选；JSON Schema 对象"
            @update:model-value="onJsonInput('spec.output_schema', $event)"
          />
        </el-form-item>

        <div class="model-contract">
          <div class="model-contract-head">
            <span class="model-contract-title">模型可见入参契约</span>
            <span class="hint">参数结构随「入参 Schema」同步，这里只补说明、标题与格式</span>
          </div>
          <p class="hint">
            说明与格式写进 model_schema 供模型构造参数，校验仍按「入参 Schema」；两者结构由页面保证一致，不会出现模型按
            A 构造、校验按 B 拒绝。
          </p>
          <el-alert
            v-if="modelContractBlocked"
            title="入参 Schema 含嵌套对象或组合分支（anyOf/oneOf/allOf）：页面不逐参数编辑模型侧注解，以免结构不一致，请通过接口维护 model_schema。"
            type="info"
            show-icon
            :closable="false"
            class="notice"
          />
          <p v-else-if="!paramRows.length" class="hint">
            「入参 Schema」还没有声明属性，无需补充模型侧说明。
          </p>
          <div v-else class="model-params">
            <div v-for="row in paramRows" :key="row.name" class="model-param">
              <div class="model-param-head">
                <span class="model-param-name">{{ row.name }}</span>
                <el-tag size="small" type="info" effect="plain">{{ row.type }}</el-tag>
                <el-tag v-if="row.required" size="small" type="warning" effect="plain">必填</el-tag>
                <span v-if="warningFor(row.name)" class="model-param-warning">
                  {{ warningFor(row.name) }}
                </span>
              </div>
              <el-input
                :model-value="noteFor(row.name).description"
                :disabled="readonly"
                placeholder="模型读到的说明，如：城市名，如 武汉"
                @update:model-value="updateNote(row.name, 'description', $event)"
              />
              <el-row :gutter="8" class="model-param-extra">
                <el-col :span="12">
                  <el-input
                    :model-value="noteFor(row.name).title"
                    :disabled="readonly"
                    placeholder="标题（可选）"
                    @update:model-value="updateNote(row.name, 'title', $event)"
                  />
                </el-col>
                <el-col :span="12">
                  <el-input
                    :model-value="noteFor(row.name).format"
                    :disabled="readonly"
                    placeholder="格式，如 date（可选）"
                    @update:model-value="updateNote(row.name, 'format', $event)"
                  />
                </el-col>
              </el-row>
            </div>
          </div>
        </div>

        <el-form-item label="业务终态" :error="fieldError('spec.returns.terminal_statuses')">
          <el-select
            v-model="terminalStatuses"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            :disabled="readonly"
            placeholder="输入返回值后回车，如 no_data"
            class="terminal-select"
            @change="emitUpdate"
          >
            <el-option v-for="item in terminalStatuses" :key="item" :label="item" :value="item" />
          </el-select>
        </el-form-item>
        <p class="hint hint-inline">
          命中条件：工具调用成功、且返回体 `status` 等于这里声明的值；命中后本步结束并直接回复，不再让模型重试。失败与待补全的返回不算终态。
        </p>

        <el-alert
          v-if="extraKeys.length"
          :title="`未识别的定义字段将原样保留：${extraKeys.join('、')}`"
          type="info"
          show-icon
          :closable="false"
          class="notice"
        />
      </el-form>
      <p class="hint">{{ specHint }}</p>
    </template>
  </div>
</template>

<style scoped>
.spec-editor {
  width: 100%;
}

.timeout {
  width: 100%;
}

.code-input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.notice {
  margin-bottom: 12px;
}

.hint {
  margin: 0;
  font-size: 12px;
  color: #8a93a5;
}

.field-error {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--el-color-danger);
}

.hint-inline {
  margin: -4px 0 12px;
}

.model-contract {
  margin: 4px 0 16px;
  padding: 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
}

.model-contract-head {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin-bottom: 4px;
}

.model-contract-title {
  font-size: 13px;
  font-weight: 600;
}

.model-params {
  margin-top: 8px;
}

.model-param {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 8px 0;
}

.model-param + .model-param {
  border-top: 1px dashed var(--el-border-color-lighter);
}

.model-param-head {
  display: flex;
  align-items: center;
  gap: 6px;
}

.model-param-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  font-weight: 600;
}

.model-param-warning {
  font-size: 12px;
  color: var(--el-color-warning);
}

.terminal-select {
  width: 100%;
}

.probe-list {
  margin-bottom: 12px;
}

.probe-title {
  margin: 0 0 8px;
  font-size: 12px;
  color: #8a93a5;
}

.probe-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 6px;
  cursor: pointer;
}

.probe-item + .probe-item {
  margin-top: 6px;
}

.probe-item:hover {
  border-color: var(--el-color-primary-light-5);
}

.probe-item.is-active {
  border-color: var(--el-color-primary);
  background: var(--el-color-primary-light-9);
}

.probe-name {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.probe-desc {
  font-size: 12px;
  color: #8a93a5;
}
</style>
