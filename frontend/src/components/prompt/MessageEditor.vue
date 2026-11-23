<script setup lang="ts">
import {
  PROMPT_ROLES,
  placeholderNames,
  splitPlaceholders,
  type PromptMessage,
} from '@/utils/prompt'

const props = withDefaults(
  defineProps<{
    modelValue: PromptMessage[]
    readonly?: boolean
    /** 变量表中已声明的变量名，用于标记未声明的占位符。 */
    declaredNames?: string[]
    errors?: Record<string, string>
  }>(),
  { readonly: false, declaredNames: () => [], errors: () => ({}) },
)

const emit = defineEmits<{ (event: 'update:modelValue', value: PromptMessage[]): void }>()

function update(next: PromptMessage[]): void {
  emit('update:modelValue', next)
}

function patch(index: number, change: Partial<PromptMessage>): void {
  update(props.modelValue.map((message, current) => (
    current === index ? { ...message, ...change } : message
  )))
}

function addMessage(): void {
  update([...props.modelValue, { role: 'user', content: '' }])
}

function removeMessage(index: number): void {
  update(props.modelValue.filter((_, current) => current !== index))
}

function moveMessage(index: number, offset: number): void {
  const target = index + offset
  if (target < 0 || target >= props.modelValue.length) return
  const next = [...props.modelValue]
  const [moved] = next.splice(index, 1)
  if (!moved) return
  next.splice(target, 0, moved)
  update(next)
}

function placeholders(content: string): { name: string; declared: boolean }[] {
  return placeholderNames(content).map((name) => ({
    name,
    declared: props.declaredNames.includes(name),
  }))
}

function errorOf(index: number, key?: 'role' | 'content'): string {
  const field = key ? `messages.${index}.${key}` : `messages.${index}`
  return props.errors[field] ?? ''
}
</script>

<template>
  <div class="message-editor">
    <div v-for="(message, index) in modelValue" :key="index" class="message-item">
      <div class="message-head">
        <div class="message-index">#{{ index + 1 }}</div>
        <el-select
          :model-value="message.role"
          size="small"
          class="role-select"
          :disabled="readonly"
          @update:model-value="patch(index, { role: $event })"
        >
          <el-option v-for="role in PROMPT_ROLES" :key="role" :label="role" :value="role" />
        </el-select>
        <div class="message-actions">
          <el-button
            link
            type="primary"
            :disabled="readonly || index === 0"
            @click="moveMessage(index, -1)"
          >
            上移
          </el-button>
          <el-button
            link
            type="primary"
            :disabled="readonly || index === modelValue.length - 1"
            @click="moveMessage(index, 1)"
          >
            下移
          </el-button>
          <el-button
            v-if="!readonly"
            link
            type="danger"
            :disabled="modelValue.length <= 1"
            @click="removeMessage(index)"
          >
            删除
          </el-button>
        </div>
      </div>

      <el-input
        :model-value="message.content"
        type="textarea"
        :autosize="{ minRows: 3, maxRows: 10 }"
        placeholder="消息内容，占位符写作 {{ name }}"
        :disabled="readonly"
        @update:model-value="patch(index, { content: $event })"
      />

      <div
        v-if="placeholders(message.content).length"
        class="content-preview"
        aria-label="占位符高亮预览"
      >
        <template v-for="(segment, segmentIndex) in splitPlaceholders(message.content)" :key="segmentIndex">
          <mark
            v-if="segment.placeholder"
            :class="declaredNames.includes(segment.placeholder) ? 'ph-ok' : 'ph-bad'"
          >{{ segment.text }}</mark>
          <span v-else>{{ segment.text }}</span>
        </template>
      </div>

      <div v-if="placeholders(message.content).length" class="placeholder-row">
        <span class="placeholder-label">占位符</span>
        <el-tag
          v-for="item in placeholders(message.content)"
          :key="item.name"
          size="small"
          :type="item.declared ? 'success' : 'danger'"
        >
          {{ item.name }}{{ item.declared ? '' : '（未声明）' }}
        </el-tag>
      </div>

      <p v-if="errorOf(index)" class="field-error">{{ errorOf(index) }}</p>
      <p v-if="errorOf(index, 'role')" class="field-error">{{ errorOf(index, 'role') }}</p>
      <p v-if="errorOf(index, 'content')" class="field-error">{{ errorOf(index, 'content') }}</p>
    </div>

    <el-button v-if="!readonly" class="add-button" @click="addMessage">新增消息</el-button>
  </div>
</template>

<style scoped>
.message-item {
  border: 1px solid #e8eaf0;
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
  background: #fbfcff;
}

.message-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}

.message-index {
  font-size: 12px;
  color: #8b93a5;
  width: 28px;
}

.role-select {
  width: 130px;
}

.message-actions {
  margin-left: auto;
}

.content-preview {
  margin-top: 6px;
  padding: 6px 8px;
  border-radius: 6px;
  background: #f5f6fa;
  font-size: 12px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
  color: #4b5563;
  max-height: 120px;
  overflow: auto;
}

.content-preview mark {
  padding: 0 2px;
  border-radius: 3px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: #e2e8ff;
  color: #1e40af;
}

.content-preview mark.ph-bad {
  background: #fde2e1;
  color: #b42318;
}

.placeholder-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 6px;
}

.placeholder-label {
  font-size: 12px;
  color: #8b93a5;
}

.field-error {
  margin: 6px 0 0;
  color: #d93025;
  font-size: 12px;
}

.add-button {
  width: 100%;
  border-style: dashed;
}
</style>
