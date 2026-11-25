<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'

import {
  formatParamValue,
  hasDefault,
  MODEL_PARAM_META,
  normalizeTemplate,
  paramHint,
  paramLabel,
  paramStep,
  SUPPORTED_MODEL_PARAMS,
  validateParamValues,
  type ModelParamRule,
  type ModelParamTemplate,
} from '@/utils/modelParams'

/**
 * Agent 模型参数编辑：默认按当前模型的参数模板渲染表单，
 * 留空表示继承模型默认值；JSON 模式留给批量粘贴等高级场景。
 */
const props = withDefaults(
  defineProps<{
    modelValue: Record<string, unknown>
    /** 当前模型 `param_template`；未选模型时为空。 */
    template?: Record<string, unknown> | null
    /** 后端返回的参数错误，键形如 `params.temperature`。 */
    errors?: Record<string, string>
  }>(),
  { template: null, errors: () => ({}) },
)

const emit = defineEmits<{ 'update:modelValue': [value: Record<string, unknown>] }>()

type Mode = 'form' | 'json'

const mode = ref<Mode>('form')
const userPickedMode = ref(false)
const jsonText = ref('{}')
const jsonError = ref<string | null>(null)
let internalUpdate = false

const template = computed<ModelParamTemplate>(() => normalizeTemplate(props.template))

const templateKeys = computed(() => {
  const declared = Object.keys(template.value)
  const ordered = SUPPORTED_MODEL_PARAMS.filter((name) => declared.includes(name))
  const extra = declared.filter((name) => !(SUPPORTED_MODEL_PARAMS as readonly string[]).includes(name))
  return [...ordered, ...extra]
})

const templateEmpty = computed(() => templateKeys.value.length === 0)

/** 模板外的历史键也要展示，否则用户删不掉，保存时必然被后端拒绝。 */
const undeclaredKeys = computed(() =>
  Object.keys(props.modelValue).filter((name) => !template.value[name]),
)

const rows = computed(() => [...templateKeys.value, ...undeclaredKeys.value])

const localIssues = computed(() => validateParamValues(template.value, props.modelValue))
const issues = computed<Record<string, string>>(() => ({ ...localIssues.value, ...props.errors }))

const overriddenCount = computed(
  () => rows.value.filter((name) => hasValue(name)).length,
)

const helpRows = computed(() => {
  if (templateEmpty.value) {
    return SUPPORTED_MODEL_PARAMS.map((name) => ({
      name,
      label: paramLabel(name),
      range: '未启用',
      defaultText: '—',
      hint: MODEL_PARAM_META[name].hint,
    }))
  }
  return templateKeys.value.map((name) => {
    const rule = template.value[name] ?? {}
    return {
      name,
      label: paramLabel(name),
      range: rangeText(rule),
      defaultText: hasDefault(rule) ? formatParamValue(rule.default) : '—',
      hint: paramHint(name, rule),
    }
  })
})

watch(
  templateEmpty,
  (empty) => {
    if (userPickedMode.value) return
    mode.value = empty ? 'json' : 'form'
    if (empty) syncJsonFromValue()
  },
  { immediate: true },
)

watch(
  () => props.modelValue,
  () => {
    if (internalUpdate) {
      internalUpdate = false
      return
    }
    if (mode.value === 'json') syncJsonFromValue()
  },
  { deep: true },
)

function ruleOf(name: string): ModelParamRule {
  return template.value[name] ?? {}
}

function hasValue(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(props.modelValue, name)
}

function valueOf(name: string): unknown {
  return props.modelValue[name]
}

function emitValue(next: Record<string, unknown>): void {
  internalUpdate = true
  emit('update:modelValue', next)
  // 自身回写引起的 props 变更不再覆盖 JSON 文本；下一帧复位，避免吞掉外部重置。
  void nextTick(() => {
    internalUpdate = false
  })
}

function setValue(name: string, value: unknown): void {
  const next = { ...props.modelValue }
  if (value === undefined || value === null || value === '') delete next[name]
  else next[name] = value
  emitValue(next)
}

function resetOne(name: string): void {
  const next = { ...props.modelValue }
  delete next[name]
  emitValue(next)
}

function resetAll(): void {
  emitValue({})
}

function syncJsonFromValue(): void {
  jsonText.value = JSON.stringify(props.modelValue, null, 2)
  jsonError.value = null
}

function applyJson(): boolean {
  const text = jsonText.value.trim()
  if (!text) {
    jsonError.value = null
    emitValue({})
    return true
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    jsonError.value = 'JSON 语法错误，请检查引号、括号与逗号'
    return false
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
    jsonError.value = '模型参数必须是 JSON 对象，例如 { "temperature": 0.3 }'
    return false
  }
  const values = parsed as Record<string, unknown>
  const problem = Object.entries(validateParamValues(template.value, values))[0]
  if (problem) {
    jsonError.value = problem[1]
    return false
  }
  jsonError.value = null
  emitValue(values)
  return true
}

function changeMode(next: string | number | boolean | undefined): void {
  const target = next === 'json' ? 'json' : 'form'
  userPickedMode.value = true
  if (target === 'json') {
    syncJsonFromValue()
    mode.value = 'json'
    return
  }
  if (!applyJson()) return
  mode.value = 'form'
}

function rangeText(rule: ModelParamRule): string {
  if (Array.isArray(rule.enum) && rule.enum.length) {
    return rule.enum.map(formatParamValue).join(' / ')
  }
  if (typeof rule.min === 'number' && typeof rule.max === 'number') {
    return `${rule.min} ~ ${rule.max}`
  }
  if (typeof rule.min === 'number') return `≥ ${rule.min}`
  if (typeof rule.max === 'number') return `≤ ${rule.max}`
  if (rule.type === 'boolean') return 'true / false'
  if (rule.type === 'string') return '字符串'
  if (rule.type === 'integer') return '整数'
  return '—'
}

function numericValue(name: string): number | undefined {
  const value = valueOf(name)
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function stringValue(name: string): string | undefined {
  const value = valueOf(name)
  return typeof value === 'string' ? value : undefined
}

function enumOptions(name: string): unknown[] {
  const values = ruleOf(name).enum
  return Array.isArray(values) ? values : []
}

function errorOf(name: string): string | null {
  return issues.value[`params.${name}`] ?? null
}

/** 保存前调用：JSON 模式需要先把文本落成对象，非法时阻止提交。 */
function commit(): boolean {
  return mode.value === 'json' ? applyJson() : true
}

defineExpose({ commit })
</script>

<template>
  <div class="params-editor">
    <div class="params-head">
      <div class="params-summary">
        <el-tag size="small" type="info">已覆盖 {{ overriddenCount }} 项</el-tag>
        <span class="params-hint">留空即继承模型默认值，只需填需要偏离默认的项</span>
      </div>
      <el-radio-group
        :model-value="mode"
        size="small"
        @update:model-value="changeMode"
      >
        <el-radio-button value="form">表单</el-radio-button>
        <el-radio-button value="json">JSON</el-radio-button>
      </el-radio-group>
    </div>

    <el-alert
      v-if="templateEmpty"
      title="当前模型未配置参数模板"
      description="任何参数都会被后端拒绝；如需调整，请先在「模型配置 → 参数模板」中声明可用参数与取值范围。"
      type="warning"
      show-icon
      :closable="false"
      class="params-alert"
    />

    <template v-if="mode === 'form'">
      <p v-if="!rows.length" class="params-empty">该模型没有可编辑的参数，全部使用平台默认行为。</p>
      <div v-for="name in rows" :key="name" class="param-row">
        <div class="param-name">
          <div class="param-label">{{ paramLabel(name) }}</div>
          <code class="param-key">{{ name }}</code>
        </div>

        <div class="param-control">
          <el-select
            v-if="enumOptions(name).length"
            :model-value="valueOf(name)"
            clearable
            placeholder="继承默认值"
            class="full"
            @update:model-value="setValue(name, $event)"
          >
            <el-option
              v-for="option in enumOptions(name)"
              :key="String(option)"
              :label="formatParamValue(option)"
              :value="option"
            />
          </el-select>

          <el-switch
            v-else-if="ruleOf(name).type === 'boolean'"
            :model-value="valueOf(name) === true"
            @update:model-value="setValue(name, $event)"
          />

          <el-input-number
            v-else-if="(ruleOf(name).type ?? 'number') !== 'string'"
            :model-value="numericValue(name)"
            :min="ruleOf(name).min"
            :max="ruleOf(name).max"
            :step="paramStep(name, ruleOf(name))"
            :precision="ruleOf(name).type === 'integer' ? 0 : undefined"
            controls-position="right"
            placeholder="继承默认值"
            class="full"
            @update:model-value="setValue(name, $event)"
          />

          <el-input
            v-else
            :model-value="stringValue(name)"
            clearable
            :placeholder="hasDefault(ruleOf(name)) ? `继承默认值 ${formatParamValue(ruleOf(name).default)}` : '不设置则不传'"
            @update:model-value="setValue(name, $event)"
          />
        </div>

        <div class="param-tail">
          <span class="param-default">
            {{ hasDefault(ruleOf(name)) ? `默认 ${formatParamValue(ruleOf(name).default)}` : '无默认值' }}
          </span>
          <el-button v-if="hasValue(name)" link size="small" @click="resetOne(name)">
            继承默认
          </el-button>
        </div>

        <p v-if="errorOf(name)" class="param-error">{{ errorOf(name) }}</p>
        <p v-else-if="paramHint(name, ruleOf(name))" class="param-hint">
          {{ paramHint(name, ruleOf(name)) }}
          <span v-if="rangeText(ruleOf(name)) !== '—'">（{{ rangeText(ruleOf(name)) }}）</span>
        </p>
      </div>

      <div v-if="rows.length" class="params-actions">
        <el-button link size="small" :disabled="!overriddenCount" @click="resetAll">
          全部恢复默认
        </el-button>
      </div>
    </template>

    <template v-else>
      <el-input
        v-model="jsonText"
        type="textarea"
        :rows="6"
        class="mono"
        placeholder='{ "temperature": 0.3 }'
        @blur="applyJson"
      />
      <p v-if="jsonError" class="param-error">{{ jsonError }}</p>
      <p v-else class="params-hint">
        键必须在当前模型的参数模板内；留空或删除某个键表示继承模型默认值。
      </p>
      <div class="params-actions">
        <el-button size="small" @click="applyJson">应用</el-button>
        <el-button size="small" @click="syncJsonFromValue">还原</el-button>
      </div>
    </template>

    <el-collapse class="param-help">
      <el-collapse-item name="help" :title="templateEmpty ? '平台支持的参数（当前模型未启用）' : `参数说明（当前模型支持 ${templateKeys.length} 项）`">
        <el-table :data="helpRows" size="small" row-key="name">
          <el-table-column prop="name" label="参数" width="180">
            <template #default="{ row }">
              <code>{{ row.name }}</code>
            </template>
          </el-table-column>
          <el-table-column prop="label" label="含义" min-width="160" />
          <el-table-column prop="range" label="取值" width="150" />
          <el-table-column prop="defaultText" label="默认" width="90" />
          <el-table-column prop="hint" label="说明" min-width="200" />
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.params-editor {
  width: 100%;
}

.params-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 8px;
}

.params-summary {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
}

.params-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #8a93a5;
  line-height: 1.5;
}

.params-alert {
  margin-bottom: 8px;
}

.params-empty {
  margin: 4px 0 8px;
  font-size: 12px;
  color: #8a93a5;
}

.param-row {
  display: grid;
  grid-template-columns: 220px minmax(160px, 240px) 1fr;
  column-gap: 12px;
  row-gap: 2px;
  align-items: start;
  padding: 8px 0;
  border-bottom: 1px dashed #eef0f4;
}

.param-row:last-of-type {
  border-bottom: none;
}

.param-label {
  font-size: 13px;
  color: #1f2937;
  line-height: 1.4;
}

.param-key {
  font-size: 11px;
  color: #9aa1b1;
}

.param-control {
  min-width: 0;
}

.param-tail {
  display: flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
}

.param-default {
  font-size: 12px;
  color: #8a93a5;
}

.param-hint {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 12px;
  color: #8a93a5;
  line-height: 1.5;
}

.param-error {
  grid-column: 1 / -1;
  margin: 0;
  font-size: 12px;
  color: #d64545;
}

.params-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.param-help {
  margin-top: 8px;
}

.full {
  width: 100%;
}

.mono :deep(textarea) {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
}

@media (max-width: 1100px) {
  .param-row {
    grid-template-columns: 1fr;
    row-gap: 4px;
  }

  .param-tail {
    min-height: 0;
  }
}
</style>
