<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, describeError } from '@/api/client'
import {
  createPromptVersion,
  getPrompt,
  listPromptVersions,
  type Prompt,
  type PromptVersion,
} from '@/api/endpoints'
import MessageEditor from '@/components/prompt/MessageEditor.vue'
import RenderPreview from '@/components/prompt/RenderPreview.vue'
import VariableTable from '@/components/prompt/VariableTable.vue'
import VersionDiff from '@/components/prompt/VersionDiff.vue'
import { useSessionStore } from '@/stores/session'
import {
  cloneMessages,
  cloneVariables,
  draftSignature,
  fieldErrorsFrom,
  hasDraftContent,
  toMessagePayload,
  toMessages,
  toVariablePayload,
  toVariables,
  undeclaredPlaceholders,
  versionSignature,
  type PromptMessage,
  type PromptVariable,
} from '@/utils/prompt'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const promptId = computed(() => String(route.params.promptId ?? ''))

const prompt = ref<Prompt | null>(null)
const versions = ref<PromptVersion[]>([])
const selectedVersionNo = ref<number | null>(null)
const draftMessages = ref<PromptMessage[]>([])
const draftVariables = ref<PromptVariable[]>([])
const note = ref('')

const loading = ref(false)
const saving = ref(false)
const error = ref<string | null>(null)
const pageError = ref<{ title: string; message: string } | null>(null)
const saveError = ref<string | null>(null)
const fieldErrors = ref<Record<string, string>>({})

const canManage = computed(() => session.can('prompt:manage'))

const sortedVersions = computed(() =>
  [...versions.value].sort((left, right) => right.version_no - left.version_no),
)

const selectedVersion = computed(
  () => versions.value.find((item) => item.version_no === selectedVersionNo.value) ?? null,
)

const baselineSignature = computed(() => versionSignature(selectedVersion.value))

const baselineVariables = computed(() =>
  selectedVersion.value ? toVariables(selectedVersion.value.variables) : [],
)

const declaredNames = computed(() =>
  draftVariables.value.map((variable) => variable.name.trim()).filter((name) => name !== ''),
)

const placeholderWarnings = computed(() => {
  const found = undeclaredPlaceholders(draftMessages.value, declaredNames.value)
  const unique: string[] = []
  for (const item of found) {
    const label = `#${item.messageIndex + 1} 的 ${item.name}`
    if (!unique.includes(label)) unique.push(label)
  }
  return unique
})

const dirty = computed(() => {
  const signature = baselineSignature.value
  if (signature === null) {
    return hasDraftContent(draftMessages.value, draftVariables.value)
  }
  return draftSignature(draftMessages.value, draftVariables.value) !== signature
})

function resetDraft(): void {
  const version = selectedVersion.value
  if (version) {
    draftMessages.value = cloneMessages(toMessages(version.messages))
    draftVariables.value = cloneVariables(toVariables(version.variables))
  } else {
    draftMessages.value = [{ role: 'system', content: '' }]
    draftVariables.value = []
  }
  note.value = ''
  fieldErrors.value = {}
  saveError.value = null
}

function syncQuery(): void {
  const query = { ...route.query }
  if (selectedVersionNo.value === null) {
    delete query.version
  } else {
    query.version = String(selectedVersionNo.value)
  }
  void router.replace({ query })
}

function requestedVersionNo(): number | null {
  const raw = route.query.version
  if (typeof raw !== 'string' || raw === '') return null
  const parsed = Number(raw)
  return Number.isFinite(parsed) ? parsed : null
}

function applyRequestedVersion(): void {
  const requested = requestedVersionNo()
  const latest = sortedVersions.value[0] ?? null
  const matched = requested === null
    ? null
    : versions.value.find((item) => item.version_no === requested) ?? null

  if (matched) {
    selectedVersionNo.value = matched.version_no
  } else {
    if (requested !== null && latest) {
      ElMessage.info(`版本 v${requested} 不存在，已展示 v${latest.version_no}`)
    }
    selectedVersionNo.value = latest?.version_no ?? null
  }
  resetDraft()
  syncQuery()
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  pageError.value = null
  try {
    const [detail, list] = await Promise.all([
      getPrompt(promptId.value),
      listPromptVersions(promptId.value),
    ])
    prompt.value = detail
    versions.value = list
    applyRequestedVersion()
  } catch (err) {
    if (err instanceof ApiError && err.status === 403) {
      pageError.value = {
        title: '没有访问权限',
        message: '当前账号缺少 prompt:read 权限，请联系管理员开通。',
      }
    } else if (err instanceof ApiError && err.status === 404) {
      pageError.value = {
        title: 'Prompt 不存在',
        message: '该 Prompt 可能已被删除，或不属于当前租户。',
      }
    } else {
      error.value = describeError(err)
    }
  } finally {
    loading.value = false
  }
}

async function selectVersion(versionNo: number): Promise<void> {
  if (versionNo === selectedVersionNo.value) return
  if (dirty.value) {
    try {
      await ElMessageBox.confirm(
        '切换版本会丢弃当前未保存的修改，是否继续？',
        '未保存的修改',
        { type: 'warning', confirmButtonText: '丢弃并切换', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  selectedVersionNo.value = versionNo
  resetDraft()
  syncQuery()
}

function onVersionRowClick(row: PromptVersion): void {
  void selectVersion(row.version_no)
}

function rowClass({ row }: { row: PromptVersion }): string {
  return row.version_no === selectedVersionNo.value ? 'row-selected' : ''
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString()
}

function short(value: string): string {
  return value.length > 8 ? value.slice(0, 8) : value
}

async function save(): Promise<void> {
  if (!dirty.value) return
  saving.value = true
  saveError.value = null
  fieldErrors.value = {}
  try {
    const created = await createPromptVersion(promptId.value, {
      messages: toMessagePayload(draftMessages.value),
      variables: toVariablePayload(draftVariables.value),
      note: note.value.trim() || null,
    })
    versions.value = [...versions.value, created].sort(
      (left, right) => left.version_no - right.version_no,
    )
    selectedVersionNo.value = created.version_no
    resetDraft()
    syncQuery()
    ElMessage.success(`已生成 v${created.version_no}`)
  } catch (err) {
    if (err instanceof ApiError && err.code === 'CONFLICT') {
      saveError.value = '版本号冲突，请刷新版本列表后重试（草稿已保留）'
    } else if (err instanceof ApiError && err.code === 'VALIDATION_FAILED') {
      fieldErrors.value = fieldErrorsFrom(err.details)
      const general = err.details
        .filter((detail) => !detail.field)
        .map((detail) => detail.reason ?? '校验失败')
      saveError.value = general.length
        ? general.join('；')
        : '保存失败，请检查标记的消息或变量'
    } else {
      saveError.value = describeError(err)
    }
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <el-button link type="primary" @click="router.push('/prompts')">← 返回列表</el-button>
        <h2 class="page-title">
          {{ prompt?.code ?? 'Prompt 详情' }}
          <span v-if="prompt" class="prompt-chip">{{ prompt.name }} · {{ prompt.status }}</span>
        </h2>
      </div>
      <div class="head-actions">
        <el-select
          v-if="sortedVersions.length"
          :model-value="selectedVersionNo"
          size="small"
          class="version-select"
          @update:model-value="selectVersion($event)"
        >
          <el-option
            v-for="item in sortedVersions"
            :key="item.id"
            :label="`v${item.version_no}`"
            :value="item.version_no"
          />
        </el-select>
        <el-input
          v-if="canManage"
          v-model="note"
          placeholder="版本备注（可选）"
          size="small"
          class="note-input"
          maxlength="500"
        />
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button
          v-if="canManage"
          type="primary"
          :disabled="!dirty"
          :loading="saving"
          @click="save"
        >
          保存为新版本
        </el-button>
      </div>
    </div>

    <el-result
      v-if="pageError"
      icon="warning"
      :title="pageError.title"
      :sub-title="pageError.message"
    >
      <template #extra>
        <el-button @click="router.push('/prompts')">返回列表</el-button>
      </template>
    </el-result>

    <template v-else>
      <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />
      <el-alert
        v-if="saveError"
        :title="saveError"
        type="error"
        show-icon
        class="save-error"
        @close="saveError = null"
      />

      <el-alert
        v-if="!loading && !versions.length"
        title="该 Prompt 还没有版本：填充模板与变量后保存，将生成 v1。"
        type="info"
        show-icon
        :closable="false"
        class="save-error"
      />

      <el-row v-loading="loading" :gutter="16">
        <el-col :span="14">
          <el-card shadow="never" class="panel">
            <template #header>
              <div class="panel-head">
                <span class="panel-title">消息模板</span>
                <span class="page-hint">
                  <template v-if="selectedVersion">编辑基线 v{{ selectedVersion.version_no }}</template>
                  <template v-else>尚未保存任何版本</template>
                </span>
              </div>
            </template>
            <MessageEditor
              v-model="draftMessages"
              :readonly="!canManage"
              :declared-names="declaredNames"
              :errors="fieldErrors"
            />
            <p v-if="placeholderWarnings.length" class="placeholder-warning">
              未声明的占位符：{{ placeholderWarnings.join('、') }}（保存时会被后端拒绝）
            </p>
          </el-card>

          <el-card shadow="never" class="panel">
            <template #header>
              <div class="panel-head">
                <span class="panel-title">变量声明</span>
                <span class="page-hint">{{ draftVariables.length }} 个变量</span>
              </div>
            </template>
            <VariableTable
              v-model="draftVariables"
              :readonly="!canManage"
              :errors="fieldErrors"
            />
          </el-card>
        </el-col>

        <el-col :span="10">
          <el-card shadow="never" class="panel">
            <template #header>
              <span class="panel-title">渲染预览</span>
            </template>
            <RenderPreview :version="selectedVersion" :variables="baselineVariables" />
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="panel">
        <template #header>
          <div class="panel-head">
            <span class="panel-title">版本历史</span>
            <span class="page-hint">点击行切换编辑基线</span>
          </div>
        </template>
        <el-table
          :data="sortedVersions"
          size="small"
          row-key="id"
          :row-class-name="rowClass"
          @row-click="onVersionRowClick"
        >
          <el-table-column label="版本" width="80">
            <template #default="{ row }">v{{ row.version_no }}</template>
          </el-table-column>
          <el-table-column label="备注" min-width="160" show-overflow-tooltip>
            <template #default="{ row }">{{ row.note ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="配置哈希" width="120">
            <template #default="{ row }">
              <el-tooltip :content="row.config_hash" placement="top">
                <code>{{ short(row.config_hash) }}</code>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column label="创建时间" width="180">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="状态" width="110">
            <template #default="{ row }">
              <el-tag v-if="row.version_no === selectedVersionNo" size="small" type="success">
                当前基线
              </el-tag>
            </template>
          </el-table-column>
        </el-table>
        <p v-if="!sortedVersions.length" class="page-hint">还没有版本，保存后生成 v1。</p>
      </el-card>

      <el-card shadow="never" class="panel">
        <template #header>
          <span class="panel-title">版本对比</span>
        </template>
        <VersionDiff :versions="versions" />
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-title {
  margin: 6px 0 0;
}

.prompt-chip {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: #6b7280;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.version-select {
  width: 96px;
}

.note-input {
  width: 190px;
}

.panel {
  border-radius: 10px;
  margin-bottom: 16px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title {
  font-weight: 600;
}

.save-error {
  margin-bottom: 12px;
}

.placeholder-warning {
  margin: 10px 0 0;
  font-size: 12px;
  color: #b26a00;
}

:deep(.el-table__body tr.row-selected > td.el-table__cell) {
  background: #eef3ff;
}
</style>
