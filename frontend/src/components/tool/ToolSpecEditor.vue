<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { describeError } from '@/api/client'
import { listSecrets, type Secret } from '@/api/endpoints'
import { useSessionStore } from '@/stores/session'

type Spec = Record<string, unknown>

const props = withDefaults(
  defineProps<{
    modelValue: Spec
    readonly?: boolean
    errors?: Record<string, string>
    type?: string
  }>(),
  { readonly: false, errors: () => ({}), type: 'HTTP' },
)

const emit = defineEmits<{ 'update:modelValue': [Spec] }>()

const session = useSessionStore()

const METHODS = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']
const AUTH_TYPES = [
  { value: 'none', label: '无鉴权' },
  { value: 'bearer', label: 'Bearer Token' },
  { value: 'header', label: '自定义 Header' },
]
const KNOWN_KEYS = new Set([
  'method',
  'url',
  'timeout_s',
  'headers',
  'auth',
  'input_schema',
  'output_schema',
])
const UUID_PATTERN = /^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$/

const isHttp = computed(() => props.type === 'HTTP')

const method = ref('POST')
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

function buildSpec(): Spec {
  if (!isHttp.value) {
    const parsed = parseJson(rawText.value, 'spec')
    return isPlainObject(parsed) ? parsed : { ...props.modelValue }
  }

  // 以原 spec 为基底，表单只覆盖已知字段，未知键原样保留
  const spec: Spec = { ...props.modelValue }
  spec.method = method.value
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
  return spec
}

function emitUpdate(): void {
  emit('update:modelValue', buildSpec())
}

function syncFromModel(value: Spec): void {
  rawText.value = Object.keys(value).length ? textOf(value) : '{}'
  method.value = typeof value.method === 'string' ? value.method.toUpperCase() : 'POST'
  url.value = typeof value.url === 'string' ? value.url : ''
  timeout.value = typeof value.timeout_s === 'number' ? value.timeout_s : 15
  const authValue = isPlainObject(value.auth) ? value.auth : { type: 'none' }
  authType.value = typeof authValue.type === 'string' ? authValue.type : 'none'
  secretRef.value = typeof authValue.secret_ref_id === 'string' ? authValue.secret_ref_id : ''
  headerName.value = typeof authValue.name === 'string' ? authValue.name : ''
  headersText.value = isPlainObject(value.headers) ? textOf(value.headers) : '{}'
  inputSchemaText.value = isPlainObject(value.input_schema) ? textOf(value.input_schema) : ''
  outputSchemaText.value = isPlainObject(value.output_schema) ? textOf(value.output_schema) : ''
  jsonErrors.value = {}
}

/** 供父级保存前调用：返回字段级错误，为空表示可提交。 */
function validate(): Record<string, string> {
  const errors: Record<string, string> = {}

  if (!isHttp.value) {
    const parsed = parseJson(rawText.value, 'spec')
    if (jsonErrors.value['spec']) {
      errors['spec'] = jsonErrors.value['spec']
    } else if (!isPlainObject(parsed)) {
      errors['spec'] = '工具定义必须是 JSON 对象'
    }
    return errors
  }

  if (!METHODS.includes(method.value)) {
    errors['spec.method'] = '不支持的请求方法'
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
    <template v-if="!isHttp">
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
        <el-row :gutter="12">
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

        <el-alert
          v-if="extraKeys.length"
          :title="`未识别的定义字段将原样保留：${extraKeys.join('、')}`"
          type="info"
          show-icon
          :closable="false"
          class="notice"
        />
      </el-form>
      <p class="hint">
        请求方法、请求地址、超时、请求头与鉴权会写入版本快照；保存后生成不可变版本。
      </p>
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
</style>
