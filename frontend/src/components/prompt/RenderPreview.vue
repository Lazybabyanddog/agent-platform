<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { ApiError, describeError } from '@/api/client'
import { renderPromptVersion, type PromptVersion } from '@/api/endpoints'
import {
  VARIABLE_TYPE_LABELS,
  hasDefault,
  type PromptVariable,
} from '@/utils/prompt'

const props = defineProps<{
  version: PromptVersion | null
  variables: PromptVariable[]
}>()

interface RenderedMessage {
  role: string
  content: string
}

const values = ref<Record<string, unknown>>({})
const touched = ref<string[]>([])
const fieldErrors = ref<Record<string, string>>({})
const results = ref<RenderedMessage[] | null>(null)
const error = ref<string | null>(null)
const rendering = ref(false)

const defaultedNames = computed(() =>
  props.variables
    .filter((variable) => hasDefault(variable) && !touched.value.includes(variable.name))
    .map((variable) => variable.name),
)

function reset(): void {
  const next: Record<string, unknown> = {}
  for (const variable of props.variables) {
    if (hasDefault(variable)) {
      next[variable.name] = variable.default
    } else if (variable.type === 'boolean') {
      // 开关没有"未填"状态，未设置默认值时按 false 处理。
      next[variable.name] = false
    }
  }
  values.value = next
  touched.value = []
  fieldErrors.value = {}
  results.value = null
  error.value = null
}

watch(() => props.version?.id, reset, { immediate: true })

function setValue(name: string, value: unknown): void {
  values.value = { ...values.value, [name]: value }
  if (!touched.value.includes(name)) touched.value = [...touched.value, name]
  const field = `variables.${name}`
  if (fieldErrors.value[field]) {
    const next = { ...fieldErrors.value }
    delete next[field]
    fieldErrors.value = next
  }
}

function textValue(name: string): string {
  const value = values.value[name]
  return typeof value === 'string' ? value : ''
}

function numberValue(name: string): number | undefined {
  const value = values.value[name]
  return typeof value === 'number' ? value : undefined
}

function booleanValue(name: string): boolean {
  return values.value[name] === true
}

function isDefaulted(variable: PromptVariable): boolean {
  return hasDefault(variable) && !touched.value.includes(variable.name)
}

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || (typeof value === 'string' && value.trim() === '')
}

function validate(): boolean {
  const next: Record<string, string> = {}
  for (const variable of props.variables) {
    const field = `variables.${variable.name}`
    const value = values.value[variable.name]
    if (variable.required && isEmpty(value)) {
      next[field] = '该变量必填'
      continue
    }
    if (isEmpty(value)) continue
    if (variable.type === 'integer' && !Number.isInteger(value)) {
      next[field] = '需要整数'
    } else if (variable.type === 'number' && (typeof value !== 'number' || Number.isNaN(value))) {
      next[field] = '需要数值'
    }
  }
  fieldErrors.value = next
  return Object.keys(next).length === 0
}

function buildPayload(): Record<string, unknown> {
  const payload: Record<string, unknown> = {}
  for (const variable of props.variables) {
    const value = values.value[variable.name]
    if (isEmpty(value)) continue
    payload[variable.name] = value
  }
  return payload
}

async function render(): Promise<void> {
  const version = props.version
  if (!version) return
  results.value = null
  error.value = null
  if (!validate()) {
    error.value = '请先修正标记的变量取值'
    return
  }
  rendering.value = true
  try {
    const response = await renderPromptVersion(version.id, { values: buildPayload() })
    results.value = response.messages.map((message) => ({
      role: typeof message.role === 'string' ? message.role : 'user',
      content: typeof message.content === 'string' ? message.content : '',
    }))
  } catch (err) {
    results.value = null
    if (err instanceof ApiError) {
      const next: Record<string, string> = {}
      const general: string[] = []
      for (const detail of err.details) {
        const field = detail.field ?? ''
        const matched = /^variables\.(.+)$/.exec(field)
        if (matched?.[1]) {
          next[`variables.${matched[1]}`] = detail.reason ?? '取值不合法'
        } else {
          general.push([field, detail.reason].filter(Boolean).join(': '))
        }
      }
      fieldErrors.value = next
      error.value = general.length ? general.join('；') : describeError(err)
    } else {
      fieldErrors.value = {}
      error.value = describeError(err)
    }
  } finally {
    rendering.value = false
  }
}

function roleClass(role: string): string {
  if (role === 'system' || role === 'assistant' || role === 'user') return `role-${role}`
  return 'role-unknown'
}
</script>

<template>
  <div class="render-preview">
    <div class="preview-head">
      <span class="page-hint">
        <template v-if="version">基于 v{{ version.version_no }} 的模板渲染</template>
        <template v-else>保存第一个版本后即可预览</template>
      </span>
      <el-button
        type="primary"
        size="small"
        :loading="rendering"
        :disabled="!version"
        @click="render"
      >
        渲染预览
      </el-button>
    </div>

    <p v-if="version && !variables.length" class="page-hint">
      该版本没有变量声明，直接渲染即可。
    </p>

    <div v-if="variables.length" class="value-form">
      <div v-for="variable in variables" :key="variable.name" class="value-row">
        <div class="value-label">
          <code>{{ variable.name }}</code>
          <el-tag v-if="variable.required" size="small" type="danger">必填</el-tag>
          <el-tag v-if="isDefaulted(variable)" size="small" type="info">默认值</el-tag>
          <span class="value-type">{{ VARIABLE_TYPE_LABELS[variable.type] }}</span>
        </div>

        <el-input
          v-if="variable.type === 'string'"
          :model-value="textValue(variable.name)"
          type="textarea"
          :autosize="{ minRows: 1, maxRows: 4 }"
          size="small"
          placeholder="输入取值"
          @update:model-value="setValue(variable.name, $event)"
        />
        <el-input-number
          v-else-if="variable.type === 'number' || variable.type === 'integer'"
          :model-value="numberValue(variable.name)"
          size="small"
          :step="variable.type === 'integer' ? 1 : 0.1"
          :precision="variable.type === 'integer' ? 0 : undefined"
          placeholder="输入数值"
          class="value-number"
          @update:model-value="setValue(variable.name, $event)"
        />
        <el-switch
          v-else
          :model-value="booleanValue(variable.name)"
          @update:model-value="setValue(variable.name, $event)"
        />

        <p v-if="fieldErrors[`variables.${variable.name}`]" class="field-error">
          {{ fieldErrors[`variables.${variable.name}`] }}
        </p>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <div v-if="results" class="result-list">
      <div class="result-head">
        <span class="panel-title">渲染结果</span>
        <span v-if="defaultedNames.length" class="page-hint">
          默认值填充：{{ defaultedNames.join('、') }}
        </span>
      </div>
      <div v-for="(message, index) in results" :key="index" class="result-item">
        <div class="result-role" :class="roleClass(message.role)">
          <span>{{ message.role }}</span>
          <span class="result-index">#{{ index + 1 }}</span>
        </div>
        <pre class="result-content">{{ message.content }}</pre>
      </div>
      <p v-if="!results.length" class="page-hint">该版本没有消息。</p>
    </div>
  </div>
</template>

<style scoped>
.preview-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.value-form {
  margin-bottom: 12px;
}

.value-row {
  padding: 8px 0;
  border-bottom: 1px dashed #eceef4;
}

.value-label {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.value-label code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

.value-type {
  font-size: 11px;
  color: #8b93a5;
}

.value-number {
  width: 100%;
}

.field-error {
  margin: 4px 0 0;
  color: #d93025;
  font-size: 12px;
}

.result-list {
  margin-top: 12px;
}

.result-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 8px;
}

.panel-title {
  font-weight: 600;
  font-size: 13px;
}

.result-item {
  border: 1px solid #e8eaf0;
  border-radius: 8px;
  overflow: hidden;
  margin-bottom: 8px;
}

.result-role {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.result-index {
  font-weight: 400;
  opacity: 0.7;
}

.role-system {
  background: #eef1f7;
  color: #4a5568;
}

.role-user {
  background: #e8efff;
  color: #2b4fbf;
}

.role-assistant {
  background: #e6f6ec;
  color: #1e7a43;
}

.role-unknown {
  background: #fdeaea;
  color: #b42318;
}

.result-content {
  margin: 0;
  padding: 10px 12px;
  font-family: inherit;
  font-size: 13px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  background: #ffffff;
}
</style>
