<script setup lang="ts">
import { computed, ref, watch } from 'vue'

import { describeError } from '@/api/client'
import { diffPromptVersions, type PromptDiff, type PromptVersion } from '@/api/endpoints'
import JsonBlock from '@/components/JsonBlock.vue'
import { toMessage, toVariable, type PromptMessage, type PromptVariable } from '@/utils/prompt'

const props = defineProps<{ versions: PromptVersion[] }>()

interface MessageChangeView {
  index: number
  base: PromptMessage | null
  target: PromptMessage | null
}

interface VariableChangeView {
  name: string
  base: PromptVariable | null
  target: PromptVariable | null
}

const baseId = ref('')
const targetId = ref('')
const diff = ref<PromptDiff | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)

const sorted = computed(() => [...props.versions].sort((a, b) => b.version_no - a.version_no))

const messageChanges = computed<MessageChangeView[]>(() =>
  (diff.value?.message_changes ?? []).map((item) => ({
    index: typeof item.index === 'number' ? item.index : 0,
    base: toMessage(item.base),
    target: toMessage(item.target),
  })),
)

const variableChanges = computed<VariableChangeView[]>(() =>
  (diff.value?.variables_changed ?? []).map((item) => ({
    name: typeof item.name === 'string' ? item.name : '未命名变量',
    base: toVariable(item.base),
    target: toVariable(item.target),
  })),
)

watch(
  sorted,
  (list) => {
    const ids = list.map((item) => item.id)
    if (!ids.includes(targetId.value)) targetId.value = list[0]?.id ?? ''
    if (!ids.includes(baseId.value)) baseId.value = list[1]?.id ?? list[0]?.id ?? ''
  },
  { immediate: true },
)

watch([baseId, targetId], () => {
  diff.value = null
  error.value = null
})

async function compare(): Promise<void> {
  diff.value = null
  error.value = null
  if (!baseId.value || !targetId.value) {
    error.value = '请选择两个版本'
    return
  }
  loading.value = true
  try {
    diff.value = await diffPromptVersions(baseId.value, targetId.value)
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div>
    <div class="diff-head">
      <span class="page-hint">对比两个版本的模板与变量声明</span>
      <div v-if="sorted.length >= 2" class="diff-actions">
        <el-select v-model="baseId" size="small" placeholder="基线版本" class="version-select">
          <el-option
            v-for="item in sorted"
            :key="item.id"
            :label="`v${item.version_no}`"
            :value="item.id"
          />
        </el-select>
        <span class="arrow">→</span>
        <el-select v-model="targetId" size="small" placeholder="目标版本" class="version-select">
          <el-option
            v-for="item in sorted"
            :key="item.id"
            :label="`v${item.version_no}`"
            :value="item.id"
          />
        </el-select>
        <el-button type="primary" size="small" :loading="loading" @click="compare">对比</el-button>
      </div>
    </div>

    <p v-if="sorted.length < 2" class="page-hint">至少需要两个版本才能对比。</p>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <template v-if="diff">
      <div class="diff-summary">
        <el-tag :type="diff.identical ? 'success' : 'warning'" size="small">
          {{ diff.identical ? '无差异' : '存在差异' }}
        </el-tag>
        <span class="page-hint">v{{ diff.base_version_no }} → v{{ diff.target_version_no }}</span>
      </div>

      <template v-if="!diff.identical">
        <div class="diff-block">
          <div class="diff-title">消息变更</div>
          <p v-if="!messageChanges.length" class="page-hint">消息没有变化。</p>

          <div v-for="change in messageChanges" :key="`msg-${change.index}`" class="change-item">
            <div class="change-index">#{{ change.index + 1 }}</div>
            <div class="change-pair">
              <div class="change-side">
                <div class="side-label">
                  基线
                  <el-tag v-if="change.base" size="small" type="info">{{ change.base.role }}</el-tag>
                  <el-tag v-else size="small" type="warning">无此消息</el-tag>
                </div>
                <pre class="side-content">{{ change.base?.content || '—' }}</pre>
              </div>
              <div class="change-side">
                <div class="side-label">
                  目标
                  <el-tag v-if="change.target" size="small" type="success">
                    {{ change.target.role }}
                  </el-tag>
                  <el-tag v-else size="small" type="warning">无此消息</el-tag>
                </div>
                <pre class="side-content">{{ change.target?.content || '—' }}</pre>
              </div>
            </div>
          </div>
        </div>

        <div class="diff-block">
          <div class="diff-title">变量变更</div>
          <div class="tag-row">
            <span class="tag-label">新增</span>
            <el-tag v-for="name in diff.variables_added" :key="`add-${name}`" type="success">
              {{ name }}
            </el-tag>
            <span v-if="!diff.variables_added.length" class="page-hint">无</span>
          </div>
          <div class="tag-row">
            <span class="tag-label">删除</span>
            <el-tag v-for="name in diff.variables_removed" :key="`del-${name}`" type="danger">
              {{ name }}
            </el-tag>
            <span v-if="!diff.variables_removed.length" class="page-hint">无</span>
          </div>

          <p v-if="!variableChanges.length" class="page-hint">变量定义没有变化。</p>
          <div
            v-for="change in variableChanges"
            :key="`var-${change.name}`"
            class="variable-change"
          >
            <div class="diff-title">
              <code>{{ change.name }}</code>
              <el-tag size="small" type="warning">定义已变更</el-tag>
            </div>
            <el-row :gutter="12">
              <el-col :span="12">
                <div class="side-label">基线</div>
                <JsonBlock :value="change.base" max-height="160px" />
              </el-col>
              <el-col :span="12">
                <div class="side-label">目标</div>
                <JsonBlock :value="change.target" max-height="160px" />
              </el-col>
            </el-row>
          </div>
        </div>
      </template>
    </template>
    <p v-else-if="sorted.length >= 2 && !error" class="page-hint">选择两个版本后点击「对比」。</p>
  </div>
</template>

<style scoped>
.diff-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}

.diff-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.version-select {
  width: 110px;
}

.arrow {
  color: #8b93a5;
}

.diff-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}

.diff-block {
  margin-bottom: 14px;
}

.diff-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-weight: 600;
  font-size: 13px;
}

.change-item {
  display: flex;
  gap: 10px;
  margin-bottom: 10px;
}

.change-index {
  width: 32px;
  padding-top: 6px;
  font-size: 12px;
  color: #8b93a5;
}

.change-pair {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  min-width: 0;
}

.change-side {
  min-width: 0;
}

.side-label {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}

.side-content {
  margin: 0;
  padding: 8px 10px;
  border-radius: 6px;
  background: #f7f8fc;
  border: 1px solid #e8eaf0;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.55;
  white-space: pre-wrap;
  word-break: break-word;
  min-height: 32px;
}

.tag-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}

.tag-label {
  width: 40px;
  font-size: 12px;
  color: #6b7280;
}

.variable-change {
  margin-top: 8px;
}
</style>
