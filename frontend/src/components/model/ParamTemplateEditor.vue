<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import NoticeBanner from '@/components/common/NoticeBanner.vue'
import { isSupportedModelParam, paramHint, SUPPORTED_MODEL_PARAMS } from '@/utils/modelParams'

/**
 * 参数模板编辑器：按行编辑参数类型与取值范围。
 *
 * 前端只做“看得见”的校验（未知键、min > max、默认值类型），
 * 平台上限（如 timeout 不得超过同步调用上限）以后端返回的
 * `param_template.<name>` 错误定位回对应行。
 */

type RuleType = 'number' | 'integer' | 'string' | 'boolean'

interface TemplateRow {
  key: number
  name: string
  type: RuleType
  minText: string
  maxText: string
  defaultText: string
  required: boolean
  enumText: string
  description: string
}

const TIMEOUT_PARAM = 'timeout'

const props = withDefaults(
  defineProps<{
    /** 模板对象：{ [name]: { type, min, max, default, enum, required, description } } */
    modelValue: Record<string, unknown>
    /** 后端校验错误，键形如 `param_template.temperature` 或 `param_template.temperature.min` */
    errors?: Record<string, string>
  }>(),
  { errors: () => ({}) },
)

const emit = defineEmits<{ 'update:modelValue': [value: Record<string, unknown>] }>()

let nextKey = 1
const rows = ref<TemplateRow[]>([])

function ruleToRow(name: string, raw: unknown): TemplateRow {
  const rule = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>
  const type = (rule.type as RuleType) ?? 'number'
  const enumValues = Array.isArray(rule.enum) ? rule.enum.map((value) => String(value)) : []
  return {
    key: nextKey++,
    name,
    type: ['number', 'integer', 'string', 'boolean'].includes(type) ? type : 'number',
    minText: rule.min === undefined || rule.min === null ? '' : String(rule.min),
    maxText: rule.max === undefined || rule.max === null ? '' : String(rule.max),
    defaultText: rule.default === undefined || rule.default === null ? '' : String(rule.default),
    required: rule.required === true,
    enumText: enumValues.join(', '),
    description: typeof rule.description === 'string' ? rule.description : '',
  }
}

function buildTemplate(source: TemplateRow[]): Record<string, unknown> {
  const template: Record<string, unknown> = {}
  for (const row of source) {
    const name = row.name.trim()
    if (!name) continue
    const rule: Record<string, unknown> = { type: row.type }
    const min = parseNumber(row.minText)
    const max = parseNumber(row.maxText)
    if (min !== null) rule.min = min
    if (max !== null) rule.max = max

    const enumValues = row.enumText
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    if (enumValues.length) {
      rule.enum =
        row.type === 'number' || row.type === 'integer'
          ? enumValues.map((item) => Number(item))
          : enumValues
    }

    if (row.defaultText.trim() !== '') {
      rule.default = parseValue(row.type, row.defaultText)
    }
    if (row.required) rule.required = true
    if (row.description.trim()) rule.description = row.description.trim()
    template[name] = rule
  }
  return template
}

function parseNumber(text: string): number | null {
  if (text.trim() === '') return null
  const value = Number(text)
  return Number.isFinite(value) ? value : null
}

function parseValue(type: RuleType, text: string): unknown {
  if (type === 'string') return text
  if (type === 'boolean') return text.trim() === 'true'
  return Number(text)
}

function serialize(value: Record<string, unknown>): string {
  return JSON.stringify(value)
}

// 外部值变化（打开不同模型的编辑框）时重建行；自身 emit 回写的值不重建，避免打断输入。
watch(
  () => props.modelValue,
  (value) => {
    const current = buildTemplate(rows.value)
    if (serialize(current) === serialize(value ?? {})) return
    rows.value = Object.entries(value ?? {}).map(([name, rule]) => ruleToRow(name, rule))
  },
  { immediate: true, deep: true },
)

function commit(): void {
  emit('update:modelValue', buildTemplate(rows.value))
}

function addRow(name = ''): void {
  rows.value.push({
    key: nextKey++,
    name,
    type: name === TIMEOUT_PARAM ? 'integer' : 'number',
    minText: '',
    maxText: '',
    defaultText: '',
    required: false,
    enumText: '',
    description: '',
  })
  commit()
}

function removeRow(key: number): void {
  rows.value = rows.value.filter((row) => row.key !== key)
  commit()
}

function changeType(row: TemplateRow): void {
  row.minText = ''
  row.maxText = ''
  row.defaultText = ''
  row.enumText = ''
  commit()
}

const unsupported = computed(() =>
  rows.value
    .map((row) => row.name.trim())
    .filter((name) => name && !isSupportedModelParam(name)),
)

const usedNames = computed(() => new Set(rows.value.map((row) => row.name.trim())))

const availableOptions = computed(() =>
  SUPPORTED_MODEL_PARAMS.filter((name) => !usedNames.value.has(name)),
)

function optionsFor(row: TemplateRow): readonly string[] {
  return SUPPORTED_MODEL_PARAMS.filter((name) => name === row.name || !usedNames.value.has(name))
}

function hintFor(row: TemplateRow): string | null {
  return paramHint(row.name) || null
}

function localError(row: TemplateRow): string | null {
  const name = row.name.trim()
  if (!name) return '参数名不能为空'
  const min = parseNumber(row.minText)
  const max = parseNumber(row.maxText)
  if (min !== null && max !== null && min > max) return 'min 不能大于 max'
  if (row.type === 'boolean' && row.enumText.trim()) return '布尔参数不支持枚举'
  return null
}

function backendError(row: TemplateRow): string | null {
  const name = row.name.trim()
  if (!name) return null
  const prefix = `param_template.${name}`
  const matched = Object.entries(props.errors).find(
    ([field]) => field === prefix || field.startsWith(`${prefix}.`),
  )
  return matched ? matched[1] : null
}

function rowError(row: TemplateRow): string | null {
  return localError(row) ?? backendError(row)
}

/** 提交前的前端校验；返回按后端字段名归类的问题，便于与接口错误合并展示。 */
function validate(): Record<string, string> {
  const issues: Record<string, string> = {}
  const seen = new Set<string>()
  for (const row of rows.value) {
    const name = row.name.trim()
    if (!name) continue
    if (seen.has(name)) {
      issues[`param_template.${name}`] = '参数名重复'
      continue
    }
    seen.add(name)
    if (!isSupportedModelParam(name)) {
      issues[`param_template.${name}`] = `平台不支持参数 ${name}`
      continue
    }
    const local = localError(row)
    if (local) issues[`param_template.${name}`] = local
    if (row.type === 'boolean' && row.defaultText.trim() && !['true', 'false'].includes(row.defaultText.trim())) {
      issues[`param_template.${name}.default`] = '布尔默认值只能是 true / false'
    }
    if (row.type !== 'string' && row.defaultText.trim() && !Number.isFinite(Number(row.defaultText))) {
      issues[`param_template.${name}.default`] = '默认值必须是数字'
    }
  }
  return issues
}

defineExpose({ validate })
</script>

<template>
  <div class="template-editor">
    <NoticeBanner
      tone="info"
      title="参数模板决定调用方可以传什么"
      description="只有清单内的键会被透传；timeout 不进入上游参数，由执行层取“模板值与运行预算的较小者”。"
    />

    <el-table :data="rows" size="small" row-key="key" empty-text="模板为空：调用时不传任何采样参数">
      <el-table-column label="参数" min-width="190">
        <template #default="{ row }">
          <el-select
            v-model="row.name"
            filterable
            allow-create
            default-first-option
            placeholder="选择或输入参数名"
            class="full"
            @change="commit"
          >
            <el-option v-for="name in optionsFor(row)" :key="name" :label="name" :value="name" />
          </el-select>
          <p v-if="hintFor(row)" class="row-hint">{{ hintFor(row) }}</p>
        </template>
      </el-table-column>
      <el-table-column label="类型" width="120">
        <template #default="{ row }">
          <el-select v-model="row.type" class="full" @change="changeType(row)">
            <el-option label="number" value="number" />
            <el-option label="integer" value="integer" />
            <el-option label="string" value="string" />
            <el-option label="boolean" value="boolean" />
          </el-select>
        </template>
      </el-table-column>
      <el-table-column label="最小值" width="96">
        <template #default="{ row }">
          <el-input v-model="row.minText" placeholder="—" @change="commit" />
        </template>
      </el-table-column>
      <el-table-column label="最大值" width="96">
        <template #default="{ row }">
          <el-input v-model="row.maxText" placeholder="—" @change="commit" />
        </template>
      </el-table-column>
      <el-table-column label="默认值" width="110">
        <template #default="{ row }">
          <el-input v-model="row.defaultText" placeholder="—" @change="commit" />
        </template>
      </el-table-column>
      <el-table-column label="必填" width="70" align="center">
        <template #default="{ row }">
          <el-checkbox v-model="row.required" @change="commit" />
        </template>
      </el-table-column>
      <el-table-column label="枚举（逗号分隔）" min-width="150">
        <template #default="{ row }">
          <el-input v-model="row.enumText" placeholder="可选" @change="commit" />
        </template>
      </el-table-column>
      <el-table-column label="说明" min-width="150">
        <template #default="{ row }">
          <el-input v-model="row.description" placeholder="可选" @change="commit" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="70" align="center">
        <template #default="{ row }">
          <el-button link type="danger" @click="removeRow(row.key)">删除</el-button>
        </template>
      </el-table-column>
      <template #append>
        <div v-if="rows.length" class="row-errors">
          <p v-for="row in rows.filter((item) => rowError(item))" :key="row.key" class="row-error">
            {{ row.name || '未命名参数' }}：{{ rowError(row) }}
          </p>
        </div>
      </template>
    </el-table>

    <div class="template-actions">
      <el-dropdown v-if="availableOptions.length" trigger="click" @command="addRow">
        <el-button size="small" plain>添加参数</el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item v-for="name in availableOptions" :key="name" :command="name">
              {{ name }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
      <el-button size="small" plain @click="addRow('')">添加自定义参数</el-button>
      <span v-if="unsupported.length" class="unsupported">
        这些参数不在平台白名单内：{{ unsupported.join('、') }}，保存会被拒绝
      </span>
    </div>
  </div>
</template>

<style scoped>
.template-editor {
  width: 100%;
}

.full {
  width: 100%;
}

.row-hint {
  margin: 2px 0 0;
  font-size: 11px;
  color: #8a93a5;
  line-height: 1.4;
}

.row-errors {
  padding: 4px 8px 8px;
}

.row-error {
  margin: 2px 0 0;
  font-size: 12px;
  color: #d64545;
}

.template-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.unsupported {
  font-size: 12px;
  color: #b7791f;
}
</style>
