<script setup lang="ts">
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import { describeError } from '@/api/client'
import {
  createSecret,
  disableSecret,
  listSecrets,
  rotateSecret,
  type Secret,
} from '@/api/endpoints'
import NoticeBanner from '@/components/common/NoticeBanner.vue'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const canManage = computed(() => session.can('secret:manage'))

const secrets = ref<Secret[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const keyword = ref('')

const filtered = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return secrets.value
  return secrets.value.filter((item) => item.name.toLowerCase().includes(text))
})

function formatTime(value: string): string {
  return new Date(value).toLocaleString()
}

function statusType(status: string): 'success' | 'info' {
  return status === 'ACTIVE' ? 'success' : 'info'
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    secrets.value = (await listSecrets()).items
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

// ---------------------------------------------------------------- 新建（同名即轮换）

const createVisible = ref(false)
const createSubmitting = ref(false)
const createError = ref<string | null>(null)
const createFormRef = ref<FormInstance>()
const createForm = ref({ name: '', value: '' })

/** 后端把同名创建当成轮换而不是冲突，这里提前把后果说清楚。 */
const nameConflict = computed(() =>
  secrets.value.find((item) => item.name === createForm.value.name.trim()),
)

const createRules: FormRules = {
  name: [
    { required: true, message: '请输入凭据名称', trigger: 'blur' },
    {
      pattern: /^[A-Za-z0-9_.-]{1,120}$/,
      message: '1~120 位，仅允许字母、数字与 _ . -',
      trigger: 'blur',
    },
  ],
  value: [{ required: true, message: '请输入凭据内容', trigger: 'blur' }],
}

function openCreate(): void {
  createForm.value = { name: '', value: '' }
  createError.value = null
  createVisible.value = true
}

async function submitCreate(): Promise<void> {
  if (!createFormRef.value) return
  createError.value = null
  const valid = await createFormRef.value.validate().catch(() => false)
  if (!valid) return

  if (!createForm.value.value) {
    createError.value = '凭据内容不能为空'
    return
  }

  if (nameConflict.value) {
    const confirmed = await ElMessageBox.confirm(
      `已存在同名凭据「${nameConflict.value.name}」v${nameConflict.value.key_version}，继续保存会按轮换处理：引用 ID 不变，版本升到 v${
        nameConflict.value.key_version + 1
      }，所有引用方立即使用新内容。`,
      '同名凭据将被轮换',
      { confirmButtonText: '继续轮换', cancelButtonText: '取消', type: 'warning' },
    ).catch(() => false)
    if (!confirmed) return
  }

  createSubmitting.value = true
  try {
    const saved = await createSecret({
      name: createForm.value.name.trim(),
      // 密钥原样提交，不 trim：前后空白可能是有意义的部分
      value: createForm.value.value,
    })
    createVisible.value = false
    ElMessage.success(`凭据 ${saved.name} 已保存（v${saved.key_version}）`)
    await load()
  } catch (err) {
    createError.value = describeError(err)
  } finally {
    createSubmitting.value = false
  }
}

// ---------------------------------------------------------------- 轮换

const rotateVisible = ref(false)
const rotateSubmitting = ref(false)
const rotateError = ref<string | null>(null)
const rotateTarget = ref<Secret | null>(null)
const rotateFormRef = ref<FormInstance>()
const rotateForm = ref({ value: '' })

const rotateRules: FormRules = {
  value: [{ required: true, message: '请输入新的凭据内容', trigger: 'blur' }],
}

function openRotate(row: Secret): void {
  rotateTarget.value = row
  rotateForm.value = { value: '' }
  rotateError.value = null
  rotateVisible.value = true
}

async function submitRotate(): Promise<void> {
  if (!rotateFormRef.value || !rotateTarget.value) return
  rotateError.value = null
  const valid = await rotateFormRef.value.validate().catch(() => false)
  if (!valid) return

  rotateSubmitting.value = true
  try {
    const saved = await rotateSecret(rotateTarget.value.id, { value: rotateForm.value.value })
    rotateVisible.value = false
    ElMessage.success(`凭据 ${saved.name} 已轮换到 v${saved.key_version}`)
    await load()
  } catch (err) {
    rotateError.value = describeError(err)
  } finally {
    rotateSubmitting.value = false
  }
}

// ---------------------------------------------------------------- 停用

const disablingId = ref<string | null>(null)

async function disable(row: Secret): Promise<void> {
  const confirmed = await ElMessageBox.confirm(
    `停用后，引用「${row.name}」的模型供应商与远程 MCP 工具在调用时会因凭据不可用而失败；引用配置不会自动切换。凭据记录会保留，可通过轮换重新启用。`,
    '确认停用凭据',
    { confirmButtonText: '停用', cancelButtonText: '取消', type: 'warning' },
  ).catch(() => false)
  if (!confirmed) return

  disablingId.value = row.id
  try {
    await disableSecret(row.id)
    ElMessage.success(`凭据 ${row.name} 已停用`)
    await load()
  } catch (err) {
    error.value = describeError(err)
  } finally {
    disablingId.value = null
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">凭据管理</h2>
        <p class="page-hint">
          存放模型供应商与远程 MCP 工具使用的密钥。内容加密入库，接口与界面只回显掩码。
        </p>
      </div>
      <div class="actions">
        <el-input v-model="keyword" placeholder="按名称过滤" clearable class="search" />
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新建凭据</el-button>
      </div>
    </div>

    <NoticeBanner
      tone="info"
      title="凭据只暴露引用，不回显内容"
      description="内容以 AES-GCM 加密存储，界面只展示掩码与版本号。同名新建视为轮换，引用方无需改配置。"
    />

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="alert">
      <template #default>
        <el-button size="small" @click="load">重试</el-button>
      </template>
    </el-alert>

    <el-table v-loading="loading" :data="filtered" stripe row-key="id">
      <el-table-column prop="name" label="名称" min-width="180" />
      <el-table-column label="内容" width="120">
        <template #default="{ row }">
          <span class="masked">{{ row.masked }}</span>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="100">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">
            {{ row.status === 'ACTIVE' ? '启用中' : '已停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="版本" width="80">
        <template #default="{ row }">v{{ row.key_version }}</template>
      </el-table-column>
      <el-table-column label="创建时间" width="180">
        <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="140" fixed="right">
        <template #default="{ row }">
          <template v-if="canManage">
            <el-button link type="primary" @click="openRotate(row)">轮换</el-button>
            <el-button
              link
              type="danger"
              :disabled="row.status !== 'ACTIVE'"
              :loading="disablingId === row.id"
              @click="disable(row)"
            >
              停用
            </el-button>
          </template>
          <span v-else class="muted">只读</span>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-if="!loading && !filtered.length"
      :description="secrets.length ? '没有匹配的凭据。' : '还没有创建任何凭据。'"
    >
      <el-button v-if="canManage && !secrets.length" type="primary" @click="openCreate">
        新建凭据
      </el-button>
    </el-empty>

    <el-dialog v-model="createVisible" title="新建凭据" width="520px" destroy-on-close>
      <el-alert
        v-if="createError"
        :title="createError"
        type="error"
        show-icon
        :closable="false"
        class="dialog-error"
      />
      <NoticeBanner
        v-if="nameConflict"
        tone="warning"
        title="同名凭据已存在，保存将按轮换处理"
        :description="`「${nameConflict.name}」当前为 v${nameConflict.key_version}，保存后升到 v${
          nameConflict.key_version + 1
        }；引用 ID 不变，引用方立即生效。`"
      />
      <el-form ref="createFormRef" :model="createForm" :rules="createRules" label-width="88px">
        <el-form-item label="名称" prop="name">
          <el-input v-model="createForm.name" maxlength="120" placeholder="如 openai-main" />
        </el-form-item>
        <el-form-item label="凭据内容" prop="value">
          <el-input
            v-model="createForm.value"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="粘贴 API Key，保存后不可回显"
          />
        </el-form-item>
      </el-form>
      <p class="page-hint dialog-hint">
        名称只允许字母、数字与 _ . -，用于在模型供应商 / 工具的凭据下拉中辨识。
      </p>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="createSubmitting" @click="submitCreate">
          保存
        </el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="rotateVisible" title="轮换凭据" width="520px" destroy-on-close>
      <el-alert
        v-if="rotateError"
        :title="rotateError"
        type="error"
        show-icon
        :closable="false"
        class="dialog-error"
      />
      <NoticeBanner
        tone="warning"
        title="轮换会立即影响所有引用方"
        :description="`「${rotateTarget?.name ?? ''}」当前为 v${
          rotateTarget?.key_version ?? 0
        }，保存后升到 v${
          (rotateTarget?.key_version ?? 0) + 1
        }；引用 ID 不变，模型供应商与 MCP 工具下次调用即使用新内容。`"
      />
      <el-form ref="rotateFormRef" :model="rotateForm" :rules="rotateRules" label-width="88px">
        <el-form-item label="新内容" prop="value">
          <el-input
            v-model="rotateForm.value"
            type="password"
            show-password
            autocomplete="new-password"
            placeholder="粘贴新的 API Key"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="rotateVisible = false">取消</el-button>
        <el-button type="primary" :loading="rotateSubmitting" @click="submitRotate">
          轮换
        </el-button>
      </template>
    </el-dialog>
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

.page-head .page-title {
  margin: 0;
}

.page-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #8a93a5;
}

.actions {
  display: flex;
  gap: 8px;
}

.search {
  width: 220px;
}

.alert {
  margin-bottom: 12px;
}

.dialog-error {
  margin-bottom: 12px;
}

.dialog-hint {
  margin-top: -4px;
}

.masked {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  letter-spacing: 1px;
  color: #5a6478;
}

.muted {
  font-size: 12px;
  color: #9aa3b5;
}
</style>
