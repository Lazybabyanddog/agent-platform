<script setup lang="ts">
import { ElMessage } from 'element-plus'
import { computed } from 'vue'

import {
  PROMPT_VARIABLE_TYPES,
  VARIABLE_NAME_PATTERN,
  VARIABLE_TYPE_LABELS,
  defaultForType,
  hasDefault,
  type PromptVariable,
  type PromptVariableType,
} from '@/utils/prompt'

const props = withDefaults(
  defineProps<{
    modelValue: PromptVariable[]
    readonly?: boolean
    errors?: Record<string, string>
  }>(),
  { readonly: false, errors: () => ({}) },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: PromptVariable[]): void }>()

const duplicateNames = computed(() => {
  const seen = new Set<string>()
  const duplicated = new Set<string>()
  for (const variable of props.modelValue) {
    const name = variable.name.trim()
    if (!name) continue
    if (seen.has(name)) duplicated.add(name)
    seen.add(name)
  }
  return duplicated
})

function update(next: PromptVariable[]): void {
  emit('update:modelValue', next)
}

function replace(index: number, next: PromptVariable): void {
  update(props.modelValue.map((variable, current) => (current === index ? next : variable)))
}

function setName(index: number, name: string): void {
  const variable = props.modelValue[index]
  if (!variable) return
  replace(index, { ...variable, name })
}

function changeType(index: number, type: PromptVariableType): void {
  const variable = props.modelValue[index]
  if (!variable || variable.type === type) return
  const next: PromptVariable = { name: variable.name, type, required: variable.required }
  if (hasDefault(variable)) {
    ElMessage.info(`变量 ${variable.name || '未命名'} 的类型已变更，默认值已清空，请重新填写`)
  }
  replace(index, next)
}

function changeRequired(index: number, required: boolean): void {
  const variable = props.modelValue[index]
  if (!variable) return
  replace(index, { ...variable, required })
}

function toggleDefault(index: number, enabled: boolean): void {
  const variable = props.modelValue[index]
  if (!variable) return
  if (enabled) {
    replace(index, { ...variable, default: defaultForType(variable.type) })
    return
  }
  const next: PromptVariable = {
    name: variable.name,
    type: variable.type,
    required: variable.required,
  }
  replace(index, next)
}

function setDefault(index: number, value: unknown): void {
  const variable = props.modelValue[index]
  if (!variable) return
  replace(index, { ...variable, default: value })
}

function addVariable(): void {
  update([...props.modelValue, { name: '', type: 'string', required: false }])
}

function removeVariable(index: number): void {
  update(props.modelValue.filter((_, current) => current !== index))
}

function errorOf(index: number, key: 'name' | 'type' | 'required' | 'default'): string {
  return props.errors[`variables.${index}.${key}`] ?? props.errors[`variables.${index}`] ?? ''
}

function nameError(index: number, variable: PromptVariable): string {
  const serverError = errorOf(index, 'name')
  if (serverError) return serverError
  const name = variable.name.trim()
  if (!name) return ''
  if (!VARIABLE_NAME_PATTERN.test(name)) return '变量名需以字母或下划线开头，仅含字母数字与下划线'
  if (duplicateNames.value.has(name)) return '变量名重复'
  return ''
}

function asNumber(value: unknown): number {
  return typeof value === 'number' ? value : 0
}

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function asBoolean(value: unknown): boolean {
  return value === true
}
</script>

<template>
  <div class="variable-table">
    <div class="variable-row variable-head">
      <span>变量名</span>
      <span>类型</span>
      <span>必填</span>
      <span>默认值</span>
      <span class="action-head">操作</span>
    </div>

    <div v-for="(variable, index) in modelValue" :key="index" class="variable-row">
      <div class="cell">
        <el-input
          :model-value="variable.name"
          size="small"
          placeholder="如 user_name"
          :disabled="readonly"
          @update:model-value="setName(index, $event)"
        />
        <p v-if="nameError(index, variable)" class="field-error">{{ nameError(index, variable) }}</p>
      </div>

      <div class="cell">
        <el-select
          :model-value="variable.type"
          size="small"
          :disabled="readonly"
          @update:model-value="changeType(index, $event)"
        >
          <el-option
            v-for="type in PROMPT_VARIABLE_TYPES"
            :key="type"
            :label="`${VARIABLE_TYPE_LABELS[type]}（${type}）`"
            :value="type"
          />
        </el-select>
        <p v-if="errorOf(index, 'type')" class="field-error">{{ errorOf(index, 'type') }}</p>
      </div>

      <div class="cell cell-center">
        <el-switch
          :model-value="variable.required"
          :disabled="readonly"
          @update:model-value="changeRequired(index, $event)"
        />
        <p v-if="errorOf(index, 'required')" class="field-error">{{ errorOf(index, 'required') }}</p>
      </div>

      <div class="cell">
        <el-switch
          :model-value="hasDefault(variable)"
          :disabled="readonly"
          size="small"
          active-text="设置"
          inline-prompt
          @update:model-value="toggleDefault(index, $event)"
        />
        <template v-if="hasDefault(variable)">
          <el-input
            v-if="variable.type === 'string'"
            :model-value="asText(variable.default)"
            size="small"
            placeholder="默认文本（可为空）"
            :disabled="readonly"
            class="default-control"
            @update:model-value="setDefault(index, $event)"
          />
          <el-input-number
            v-else-if="variable.type === 'number' || variable.type === 'integer'"
            :model-value="asNumber(variable.default)"
            size="small"
            :step="variable.type === 'integer' ? 1 : 0.1"
            :precision="variable.type === 'integer' ? 0 : undefined"
            :disabled="readonly"
            class="default-control"
            @update:model-value="setDefault(index, $event)"
          />
          <el-switch
            v-else
            :model-value="asBoolean(variable.default)"
            :disabled="readonly"
            class="default-control"
            @update:model-value="setDefault(index, $event)"
          />
        </template>
        <p v-if="errorOf(index, 'default')" class="field-error">{{ errorOf(index, 'default') }}</p>
      </div>

      <div class="cell cell-center">
        <el-button
          v-if="!readonly"
          link
          type="danger"
          @click="removeVariable(index)"
        >
          删除
        </el-button>
        <span v-else class="readonly-dash">—</span>
      </div>
    </div>

    <p v-if="!modelValue.length" class="page-hint">
      还没有变量声明；模板里的 <code v-text="'{{ name }}'" /> 必须先在此声明。
    </p>

    <el-button v-if="!readonly" class="add-button" @click="addVariable">新增变量</el-button>
  </div>
</template>

<style scoped>
.variable-row {
  display: grid;
  grid-template-columns: 1.1fr 1.3fr 60px 1.3fr 56px;
  gap: 8px;
  align-items: start;
  padding: 8px 0;
  border-bottom: 1px dashed #eceef4;
}

.variable-head {
  font-size: 12px;
  color: #8b93a5;
  border-bottom: 1px solid #e8eaf0;
}

.cell {
  min-width: 0;
}

.cell-center {
  display: flex;
  justify-content: center;
  padding-top: 4px;
}

.default-control {
  margin-top: 6px;
  width: 100%;
}

.field-error {
  margin: 4px 0 0;
  color: #d93025;
  font-size: 12px;
}

.action-head {
  text-align: center;
}

.readonly-dash {
  color: #c0c4cc;
}

.add-button {
  width: 100%;
  border-style: dashed;
  margin-top: 10px;
}
</style>
