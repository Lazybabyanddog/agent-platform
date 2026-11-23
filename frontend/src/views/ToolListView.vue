<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, describeError } from '@/api/client'
import {
  createTool,
  listTools,
  updateToolStatus,
  type RiskLevel,
  type Tool,
} from '@/api/endpoints'
import ToolSpecEditor from '@/components/tool/ToolSpecEditor.vue'
import { useSessionStore } from '@/stores/session'

const router = useRouter()
const session = useSessionStore()

const tools = ref<Tool[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const keyword = ref('')
const togglingId = ref<string | null>(null)

const dialogVisible = ref(false)
const creating = ref(false)
const createError = ref<string | null>(null)
const codeError = ref('')
const fieldErrors = ref<Record<string, string>>({})
const formRef = ref<FormInstance>()
const specEditor = ref<InstanceType<typeof ToolSpecEditor>>()

const RISK_LEVELS: RiskLevel[] = ['READ_ONLY', 'WRITE', 'HIGH']
const DEFAULT_SPEC = {
  method: 'POST',
  url: 'https://',
  timeout_s: 15,
  headers: {},
  auth: { type: 'none' },
  input_schema: { type: 'object', properties: {} },
}

const form = ref({
  code: '',
  name: '',
  description: '',
  risk_level: 'READ_ONLY' as RiskLevel,
  spec: { ...DEFAULT_SPEC } as Record<string, unknown>,
})

const canManage = computed(() => session.can('tool:manage'))

const filtered = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return tools.value
  return tools.value.filter((tool) =>
    `${tool.code} ${tool.name}`.toLowerCase().includes(text),
  )
})

const rules: FormRules = {
  code: [
    { required: true, message: '请输入编码', trigger: 'blur' },
    {
      pattern: /^[A-Za-z0-9_.-]{1,64}$/,
      message: '1~64 位，仅允许字母、数字与 _ . -',
      trigger: 'blur',
    },
  ],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
}

function riskType(risk: RiskLevel | null): 'success' | 'warning' | 'danger' | 'info' {
  if (risk === 'HIGH') return 'danger'
  if (risk === 'WRITE') return 'warning'
  if (risk === 'READ_ONLY') return 'success'
  return 'info'
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString()
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    tools.value = await listTools()
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

async function toggleStatus(row: Tool, value: boolean): Promise<void> {
  const target = value ? 'ACTIVE' : 'DISABLED'
  if (row.status === target) return
  togglingId.value = row.id
  try {
    const updated = await updateToolStatus(row.id, { status: target })
    Object.assign(row, updated)
    ElMessage.success(`${row.code} 已${target === 'ACTIVE' ? '启用' : '停用'}`)
  } catch (err) {
    error.value = describeError(err)
    await load()
  } finally {
    togglingId.value = null
  }
}

function openCreate(): void {
  form.value = {
    code: '',
    name: '',
    description: '',
    risk_level: 'READ_ONLY',
    spec: JSON.parse(JSON.stringify(DEFAULT_SPEC)) as Record<string, unknown>,
  }
  codeError.value = ''
  createError.value = null
  fieldErrors.value = {}
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!formRef.value || !specEditor.value) return
  codeError.value = ''
  createError.value = null
  fieldErrors.value = {}

  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  const specErrors = specEditor.value.validate()
  if (Object.keys(specErrors).length) {
    fieldErrors.value = specErrors
    createError.value = '调用契约还有未修正的问题'
    return
  }

  creating.value = true
  try {
    const created = await createTool({
      code: form.value.code.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim() || null,
      type: 'HTTP',
      risk_level: form.value.risk_level,
      spec: specEditor.value.getSpec(),
    })
    dialogVisible.value = false
    ElMessage.success(`已创建工具 ${created.code}`)
    await router.push(`/tools/${created.id}`)
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.code === 'CONFLICT') {
        codeError.value = '该编码在本租户已存在，请换一个'
        return
      }
      if (err.code === 'VALIDATION_FAILED') {
        const codeDetail = err.details.find((detail) => detail.field === 'code')
        if (codeDetail) {
          codeError.value = codeDetail.reason ?? '编码不合法'
          return
        }
        fieldErrors.value = Object.fromEntries(
          err.details
            .filter((detail) => detail.field)
            .map((detail) => [detail.field as string, detail.reason ?? '不合法']),
        )
        if (Object.keys(fieldErrors.value).length) {
          createError.value = '调用契约校验未通过'
          return
        }
      }
    }
    createError.value = describeError(err)
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">工具管理</h2>
        <p class="page-hint">注册 HTTP 工具契约、维护版本、授权并验证调用。</p>
      </div>
      <div class="actions">
        <el-input
          v-model="keyword"
          placeholder="按编码 / 名称过滤"
          clearable
          class="search"
        />
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新建工具</el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="alert">
      <template #default>
        <el-button size="small" @click="load">重试</el-button>
      </template>
    </el-alert>

    <el-table v-loading="loading" :data="filtered" stripe row-key="id">
      <el-table-column prop="code" label="编码" width="180" />
      <el-table-column prop="name" label="名称" min-width="150" />
      <el-table-column prop="description" label="描述" min-width="180" show-overflow-tooltip />
      <el-table-column label="类型" width="80">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ row.type }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="风险" width="110">
        <template #default="{ row }">
          <el-tag v-if="row.risk_level" :type="riskType(row.risk_level)" size="small">
            {{ row.risk_level }}
          </el-tag>
          <span v-else class="muted">无版本</span>
        </template>
      </el-table-column>
      <el-table-column label="最新版本" width="90">
        <template #default="{ row }">
          {{ row.latest_version_no === null ? '—' : `v${row.latest_version_no}` }}
        </template>
      </el-table-column>
      <el-table-column label="授权" width="90">
        <template #default="{ row }">
          <el-tag :type="row.granted ? 'success' : 'info'" size="small">
            {{ row.granted ? '已授权' : '未授权' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="状态" width="120">
        <template #default="{ row }">
          <el-switch
            v-if="canManage"
            :model-value="row.status === 'ACTIVE'"
            :disabled="togglingId === row.id"
            :loading="togglingId === row.id"
            inline-prompt
            active-text="启用"
            inactive-text="停用"
            @change="(value: boolean) => toggleStatus(row, value)"
          />
          <el-tag v-else :type="row.status === 'ACTIVE' ? 'success' : 'info'" size="small">
            {{ row.status === 'ACTIVE' ? '启用中' : '已停用' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="更新时间" width="180">
        <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
      </el-table-column>
      <el-table-column label="操作" width="90" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push(`/tools/${row.id}`)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-if="!loading && !filtered.length"
      :description="tools.length ? '没有匹配的工具。' : '还没有注册任何工具。'"
    >
      <el-button v-if="canManage && !tools.length" type="primary" @click="openCreate">
        新建工具
      </el-button>
    </el-empty>

    <el-dialog v-model="dialogVisible" title="新建工具" width="720px" top="6vh">
      <el-alert
        v-if="createError"
        :title="createError"
        type="error"
        show-icon
        :closable="false"
        class="dialog-error"
      />
      <el-form ref="formRef" :model="form" :rules="rules" label-width="72px">
        <el-row :gutter="12">
          <el-col :span="12">
            <el-form-item label="编码" prop="code" :error="codeError || undefined">
              <el-input
                v-model="form.code"
                placeholder="如 order_lookup"
                maxlength="64"
                @input="codeError = ''"
              />
            </el-form-item>
          </el-col>
          <el-col :span="12">
            <el-form-item label="名称" prop="name">
              <el-input v-model="form.name" placeholder="如 订单查询" maxlength="160" />
            </el-form-item>
          </el-col>
        </el-row>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 3 }"
            placeholder="可选"
          />
        </el-form-item>
        <el-form-item label="风险等级" prop="risk_level">
          <el-select v-model="form.risk_level" class="risk-select">
            <el-option v-for="level in RISK_LEVELS" :key="level" :label="level" :value="level" />
          </el-select>
          <span class="risk-hint">HIGH 风险工具在测试调用时需显式确认</span>
        </el-form-item>
      </el-form>

      <el-divider content-position="left">调用契约（将作为 v1 保存）</el-divider>
      <ToolSpecEditor
        ref="specEditor"
        v-model="form.spec"
        :errors="fieldErrors"
      />

      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submit">创建</el-button>
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
  width: 240px;
}

.dialog-error {
  margin-bottom: 12px;
}

.alert {
  margin-bottom: 12px;
}

.risk-select {
  width: 160px;
  margin-right: 12px;
}

.risk-hint {
  font-size: 12px;
  color: #8a93a5;
}

.muted {
  font-size: 12px;
  color: #9aa3b5;
}
</style>
