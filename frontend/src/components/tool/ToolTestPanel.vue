<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { ApiError, describeError } from '@/api/client'
import { executeToolVersion, type ToolExecution, type ToolVersion } from '@/api/endpoints'
import JsonBlock from '@/components/JsonBlock.vue'

const props = defineProps<{
  versions: ToolVersion[]
  canManage: boolean
  granted: boolean
  active: boolean
}>()

const emit = defineEmits<{ executed: []; grant: []; enable: [] }>()

const versionId = ref('')
const argumentsText = ref('{\n  "text": "hello"\n}')
const allowHighRisk = ref(false)
const running = ref(false)
const error = ref<string | null>(null)
const errorCode = ref<string | null>(null)
const fieldErrors = ref<Array<{ field: string; reason: string }>>([])
const result = ref<ToolExecution | null>(null)

const sortedVersions = computed(() =>
  [...props.versions].sort((left, right) => right.version_no - left.version_no),
)

const selectedVersion = computed(
  () => props.versions.find((item) => item.id === versionId.value) ?? null,
)

const isHighRisk = computed(() => selectedVersion.value?.risk_level === 'HIGH')
const callable = computed(() => props.granted && props.active && props.versions.length > 0)

watch(
  sortedVersions,
  (list) => {
    if (!list.some((item) => item.id === versionId.value)) {
      versionId.value = list[0]?.id ?? ''
    }
  },
  { immediate: true },
)

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

/**
 * 按 JSON Schema 生成入参骨架：枚举取首项、有 default 用 default，
 * 嵌套对象只展开 required 字段，避免只为填一个入参就展开整棵树。
 */
function schemaSample(schema: unknown, depth: number): unknown {
  if (!isPlainObject(schema) || depth > 5) return null
  const enumValues = Array.isArray(schema.enum) ? schema.enum : []
  if (enumValues.length) return enumValues[0]
  if (schema.default !== undefined) return schema.default

  const rawType = schema.type
  const type = typeof rawType === 'string' ? rawType : Array.isArray(rawType) ? rawType[0] : undefined
  if (type === 'object' || isPlainObject(schema.properties)) return schemaObject(schema, depth)
  if (type === 'array') {
    const item = schemaSample(schema.items, depth + 1)
    return item === null || item === undefined ? [] : [item]
  }
  if (type === 'integer' || type === 'number') return 0
  if (type === 'boolean') return false
  if (type === 'string') return ''
  return null
}

function schemaObject(schema: Record<string, unknown>, depth: number): Record<string, unknown> {
  const properties = isPlainObject(schema.properties) ? schema.properties : {}
  const required = Array.isArray(schema.required)
    ? schema.required.filter((key): key is string => typeof key === 'string')
    : []
  const sample: Record<string, unknown> = {}
  for (const [key, child] of Object.entries(properties)) {
    if (depth > 0 && !required.includes(key)) continue
    sample[key] = schemaSample(child, depth + 1)
  }
  return sample
}

/** 入参模板只依赖版本快照里的 input_schema；没有契约时退回空对象。 */
function argumentsTemplate(version: ToolVersion): string {
  const schema = isPlainObject(version.spec.input_schema) ? version.spec.input_schema : null
  const sample = schema ? schemaObject(schema, 0) : {}
  return JSON.stringify(sample, null, 2)
}

const seededVersionId = ref('')

function resetArguments(): void {
  if (selectedVersion.value) argumentsText.value = argumentsTemplate(selectedVersion.value)
}

watch(
  selectedVersion,
  (version) => {
    // 只在切换版本时重填模板：同一版本内不打扰用户已经改过的入参
    if (!version || seededVersionId.value === version.id) return
    seededVersionId.value = version.id
    argumentsText.value = argumentsTemplate(version)
  },
  { immediate: true },
)

function statusType(status: string): 'success' | 'danger' {
  return status === 'SUCCESS' ? 'success' : 'danger'
}

async function execute(): Promise<void> {
  if (!versionId.value) return
  error.value = null
  errorCode.value = null
  fieldErrors.value = []
  result.value = null

  let args: unknown
  try {
    args = argumentsText.value.trim() ? JSON.parse(argumentsText.value) : {}
  } catch {
    error.value = '入参必须是合法 JSON'
    return
  }
  if (typeof args !== 'object' || args === null || Array.isArray(args)) {
    error.value = '入参必须是 JSON 对象'
    return
  }

  running.value = true
  try {
    result.value = await executeToolVersion(versionId.value, {
      arguments: args as Record<string, unknown>,
      allow_high_risk: allowHighRisk.value,
    })
    if (result.value.error_code) errorCode.value = result.value.error_code
    emit('executed')
  } catch (err) {
    if (err instanceof ApiError) {
      errorCode.value = err.code
      fieldErrors.value = err.details
        .filter((detail) => detail.field)
        .map((detail) => ({ field: detail.field as string, reason: detail.reason ?? '不合法' }))
    }
    error.value = describeError(err)
  } finally {
    running.value = false
  }
}
</script>

<template>
  <div>
    <el-alert
      title="测试调用不会进入工具调用记录，仅用于验证当前版本的连通性与契约。"
      type="info"
      show-icon
      :closable="false"
      class="notice"
    />

    <el-alert
      v-if="!active"
      title="该工具当前已停用，测试入口不可用。"
      type="warning"
      show-icon
      :closable="false"
      class="notice"
    >
      <template v-if="canManage" #default>
        <el-button size="small" type="warning" plain @click="emit('enable')">去启用</el-button>
      </template>
    </el-alert>

    <el-alert
      v-else-if="!granted"
      title="该工具尚未授权给本租户，无法测试调用。"
      type="warning"
      show-icon
      :closable="false"
      class="notice"
    >
      <template v-if="canManage" #default>
        <el-button size="small" type="warning" plain @click="emit('grant')">去授权</el-button>
      </template>
    </el-alert>

    <el-alert
      v-if="!versions.length"
      title="该工具还没有版本，先在「契约与版本」里保存一个版本再测试。"
      type="info"
      show-icon
      :closable="false"
    />

    <template v-else>
      <el-form label-width="96px" label-position="left">
        <el-form-item label="工具版本">
          <el-select v-model="versionId" class="version-select">
            <el-option
              v-for="item in sortedVersions"
              :key="item.id"
              :label="`v${item.version_no} · ${item.risk_level}`"
              :value="item.id"
            />
          </el-select>
        </el-form-item>
        <el-form-item label="调用入参">
          <el-input
            v-model="argumentsText"
            type="textarea"
            :autosize="{ minRows: 5, maxRows: 12 }"
            class="code-input"
            placeholder="{}"
          />
          <div class="arg-hint">
            <span>已按当前版本的 input_schema 生成骨架，可自行修改。</span>
            <el-button link type="primary" size="small" @click="resetArguments">
              按契约模板重置
            </el-button>
          </div>
        </el-form-item>
        <el-form-item v-if="isHighRisk" label="风险确认">
          <el-checkbox v-model="allowHighRisk">
            该版本标记为 HIGH 风险，我确认本次调用的副作用
          </el-checkbox>
        </el-form-item>
      </el-form>

      <div class="actions">
        <el-button
          type="primary"
          :loading="running"
          :disabled="!canManage || !callable || (isHighRisk && !allowHighRisk)"
          @click="execute"
        >
          执行调用
        </el-button>
        <span v-if="!canManage" class="hint">仅具备 tool:manage 权限的账号可以发起测试调用。</span>
      </div>

      <el-alert
        v-if="error"
        :title="errorCode ? `${errorCode}：${error}` : error"
        type="error"
        show-icon
        :closable="false"
        class="result"
      >
        <ul v-if="fieldErrors.length" class="details">
          <li v-for="item in fieldErrors" :key="`${item.field}-${item.reason}`">
            <code>{{ item.field }}</code> — {{ item.reason }}
          </li>
        </ul>
      </el-alert>

      <el-card v-if="result" shadow="never" class="result">
        <template #header>
          <div class="result-head">
            <el-tag :type="statusType(result.status)" size="small">{{ result.status }}</el-tag>
            <span class="meta">耗时 {{ result.latency_ms }} ms</span>
            <span v-if="result.error_code" class="meta">错误码 {{ result.error_code }}</span>
          </div>
        </template>
        <div class="digests">
          <span class="meta">request {{ result.request_digest.slice(0, 12) }}</span>
          <span v-if="result.response_digest" class="meta">
            response {{ result.response_digest.slice(0, 12) }}
          </span>
        </div>
        <JsonBlock :value="result.output" max-height="320px" />
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.notice {
  margin-bottom: 12px;
}

.version-select {
  width: 240px;
}

.code-input :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.arg-hint {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #8a93a5;
}

.actions {
  display: flex;
  align-items: center;
  gap: 12px;
}

.hint {
  font-size: 12px;
  color: #8a93a5;
}

.result {
  margin-top: 16px;
}

.result-head {
  display: flex;
  align-items: center;
  gap: 12px;
}

.digests {
  display: flex;
  gap: 16px;
  margin-bottom: 8px;
}

.meta {
  font-size: 12px;
  color: #6b7280;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.details {
  margin: 6px 0 0;
  padding-left: 18px;
  font-size: 12px;
}
</style>
