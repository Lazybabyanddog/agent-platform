<script setup lang="ts">
import { ElMessage, ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, describeError } from '@/api/client'
import {
  activateAgent,
  createAgent,
  deactivateAgent,
  deleteAgent,
  ENVIRONMENTS,
  listAgents,
  type Agent,
  type AgentEnvironmentState,
  type AgentStatus,
  type AgentType,
  type Environment,
} from '@/api/endpoints'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const STATUS_OPTIONS: AgentStatus[] = ['DRAFT', 'ACTIVE', 'INACTIVE', 'ARCHIVED']
const TYPE_OPTIONS: AgentType[] = ['CHAT']

const agents = ref<Agent[]>([])
const total = ref(0)
const loading = ref(false)
const error = ref<string | null>(null)

const dialogVisible = ref(false)
const creating = ref(false)
const createError = ref<string | null>(null)
const codeError = ref('')
const formRef = ref<FormInstance>()
const form = ref({ code: '', name: '', description: '', tags: [] as string[] })

const canManage = computed(() => session.can('agent:manage'))

/** 正在启停 / 删除的行，避免整表 loading 与重复点击。 */
const togglingId = ref<string | null>(null)
const deletingId = ref<string | null>(null)

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

function firstQuery(key: string): string {
  const value = route.query[key]
  return typeof value === 'string' ? value : ''
}

function parsePage(value: string): number {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : 1
}

const page = ref(parsePage(firstQuery('page')))
const pageSize = ref([10, 20, 50].includes(Number(firstQuery('page_size')))
  ? Number(firstQuery('page_size'))
  : 20)

const statusFilter = ref<AgentStatus | ''>(
  STATUS_OPTIONS.includes(firstQuery('status') as AgentStatus)
    ? (firstQuery('status') as AgentStatus)
    : '',
)
const typeFilter = ref<AgentType | ''>(
  TYPE_OPTIONS.includes(firstQuery('type') as AgentType)
    ? (firstQuery('type') as AgentType)
    : '',
)
const tagFilter = ref(firstQuery('tag'))
const keyword = ref(firstQuery('q'))

const hasFilters = computed(
  () => Boolean(statusFilter.value || typeFilter.value || tagFilter.value.trim() || keyword.value.trim()),
)

function buildQuery(nextPage = page.value): Record<string, string> {
  const query: Record<string, string> = {
    page: String(nextPage),
    page_size: String(pageSize.value),
  }
  if (statusFilter.value) query.status = statusFilter.value
  if (typeFilter.value) query.type = typeFilter.value
  if (tagFilter.value.trim()) query.tag = tagFilter.value.trim()
  if (keyword.value.trim()) query.q = keyword.value.trim()
  return query
}

/** 筛选或分页条件写入地址栏，页面重挂载时按同一条件重建请求。 */
function navigate(nextPage = page.value): void {
  router.push({ query: buildQuery(nextPage) })
}

function search(): void {
  navigate(1)
}

function reset(): void {
  statusFilter.value = ''
  typeFilter.value = ''
  tagFilter.value = ''
  keyword.value = ''
  pageSize.value = 20
  navigate(1)
}

function onPageChange(value: number): void {
  navigate(value)
}

function onSizeChange(value: number): void {
  pageSize.value = value
  navigate(1)
}

function statusType(status: AgentStatus): 'success' | 'warning' | 'info' | 'danger' {
  if (status === 'ACTIVE') return 'success'
  if (status === 'DRAFT') return 'warning'
  if (status === 'ARCHIVED') return 'danger'
  return 'info'
}

function statusLabel(status: AgentStatus): string {
  if (status === 'ACTIVE') return '启用中'
  if (status === 'DRAFT') return '草稿'
  if (status === 'ARCHIVED') return '已归档'
  return '已停用'
}

/** 只有真正发布过的 Agent 才有启用/停用语义，草稿与归档走状态标签。 */
function canToggle(row: Agent): boolean {
  return row.status === 'ACTIVE' || row.status === 'INACTIVE'
}

async function toggleStatus(row: Agent, active: boolean): Promise<void> {
  if (!active) {
    try {
      await ElMessageBox.confirm(
        `停用后 ${row.code} 在所有环境的新调用都会被拒绝（AGENT_INACTIVE），发布指针保持不变。确定停用吗？`,
        '停用 Agent',
        { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  togglingId.value = row.id
  try {
    await (active ? activateAgent(row.id) : deactivateAgent(row.id))
    ElMessage.success(`${row.code} 已${active ? '启用' : '停用'}`)
    await load()
  } catch (err) {
    if (err instanceof ApiError && err.code === 'AGENT_NOT_PUBLISHED') {
      ElMessage.warning('该 Agent 尚未发布到任何环境，无法启用；请先发布版本')
      await load()
      return
    }
    ElMessage.error(describeError(err))
    await load()
  } finally {
    togglingId.value = null
  }
}

async function removeAgent(row: Agent): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `删除后 ${row.code} 会立即从列表消失、所有环境的新调用被拒绝，编码可被重新使用；版本、发布与运行历史仍保留。确定删除吗？`,
      '删除 Agent',
      { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  deletingId.value = row.id
  try {
    await deleteAgent(row.id)
    ElMessage.success(`已删除 Agent ${row.code}`)
    await load()
  } catch (err) {
    ElMessage.error(describeError(err))
    await load()
  } finally {
    deletingId.value = null
  }
}

function envState(row: Agent, env: Environment): AgentEnvironmentState | null {
  return row.environments?.[env] ?? null
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString()
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const result = await listAgents({
      page: page.value,
      page_size: pageSize.value,
      status: statusFilter.value || undefined,
      type: typeFilter.value || undefined,
      tag: tagFilter.value.trim() || undefined,
      q: keyword.value.trim() || undefined,
    })
    agents.value = result.items
    total.value = result.total
  } catch (err) {
    agents.value = []
    total.value = 0
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  form.value = { code: '', name: '', description: '', tags: [] }
  codeError.value = ''
  createError.value = null
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  codeError.value = ''
  createError.value = null
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  creating.value = true
  try {
    const created = await createAgent({
      code: form.value.code.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim() || null,
      tags: [...new Set(form.value.tags.map((tag) => tag.trim()).filter(Boolean))],
      type: 'CHAT',
    })
    dialogVisible.value = false
    ElMessage.success(`已创建 Agent ${created.code}`)
    await router.push(`/agents/${created.id}`)
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.code === 'CONFLICT') {
        // 后端 CONFLICT 不带 details，按语义定位到编码字段。
        codeError.value = '该编码在本租户已存在，请换一个'
        return
      }
      if (err.code === 'VALIDATION_FAILED') {
        const codeDetail = err.details.find((detail) => detail.field === 'code')
        if (codeDetail) {
          codeError.value = codeDetail.reason ?? '编码不合法'
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
      <h2 class="page-title">Agent 列表</h2>
      <div class="actions">
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新建 Agent</el-button>
      </div>
    </div>

    <el-card shadow="never" class="filter-card">
      <div class="filters">
        <el-input
          v-model="keyword"
          placeholder="按编码 / 名称搜索"
          clearable
          class="search"
          @keyup.enter="search"
          @clear="search"
        />
        <el-input
          v-model="tagFilter"
          placeholder="按标签筛选"
          clearable
          class="search"
          @keyup.enter="search"
          @clear="search"
        />
        <el-select v-model="statusFilter" placeholder="全部状态" clearable class="select" @change="search">
          <el-option
            v-for="item in STATUS_OPTIONS"
            :key="item"
            :label="statusLabel(item)"
            :value="item"
          />
        </el-select>
        <el-select v-model="typeFilter" placeholder="全部类型" clearable class="select" @change="search">
          <el-option v-for="item in TYPE_OPTIONS" :key="item" :label="item" :value="item" />
        </el-select>
        <el-button type="primary" @click="search">查询</el-button>
        <el-button v-if="hasFilters" @click="reset">清空筛选</el-button>
      </div>
    </el-card>

    <el-alert v-if="error" type="error" show-icon :closable="false" class="error-box">
      <template #title>
        <div class="error-title">
          <span>{{ error }}</span>
          <el-button size="small" @click="load">重试</el-button>
        </div>
      </template>
    </el-alert>

    <el-card shadow="never" class="table-card">
      <el-table v-loading="loading" :data="agents" stripe row-key="id">
        <el-table-column prop="code" label="编码" width="180" />
        <el-table-column prop="name" label="名称" min-width="150" show-overflow-tooltip />
        <el-table-column label="类型" width="90">
          <template #default="{ row }">{{ row.type }}</template>
        </el-table-column>
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-switch
              v-if="canManage && canToggle(row)"
              :model-value="row.status === 'ACTIVE'"
              :loading="togglingId === row.id"
              :disabled="togglingId === row.id"
              inline-prompt
              active-text="启用"
              inactive-text="停用"
              @change="(value: string | number | boolean) => toggleStatus(row, Boolean(value))"
            />
            <el-tag v-else :type="statusType(row.status)" size="small">
              {{ statusLabel(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column
          v-for="env in ENVIRONMENTS"
          :key="env"
          :label="env"
          width="110"
          align="center"
        >
          <template #default="{ row }">
            <el-tooltip
              v-if="envState(row, env)"
              :content="`发布于 ${formatTime(envState(row, env)!.released_at)}`"
              placement="top"
            >
              <el-tag type="success" size="small" effect="plain">
                v{{ envState(row, env)!.version_no }}
              </el-tag>
            </el-tooltip>
            <span v-else class="unpublished">未发布</span>
          </template>
        </el-table-column>
        <el-table-column prop="tags" label="标签" min-width="140">
          <template #default="{ row }">
            <el-tag v-for="tag in row.tags" :key="tag" size="small" type="info" class="tag">
              {{ tag }}
            </el-tag>
            <span v-if="!row.tags.length" class="unpublished">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="updated_at" label="更新时间" width="180">
          <template #default="{ row }">{{ formatTime(row.updated_at) }}</template>
        </el-table-column>
        <el-table-column label="操作" width="250" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" @click="router.push(`/agents/${row.id}`)">
              配置
            </el-button>
            <el-button link type="primary" @click="router.push(`/agents/${row.id}/versions`)">
              版本
            </el-button>
            <el-button link type="primary" @click="router.push(`/playground?agentId=${row.id}`)">
              调试
            </el-button>
            <el-button
              v-if="canManage"
              link
              type="danger"
              :loading="deletingId === row.id"
              @click="removeAgent(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
      </el-table>

      <el-empty
        v-if="!loading && !error && !agents.length"
        :description="hasFilters ? '没有符合条件的 Agent。' : '暂无 Agent。'"
      >
        <el-button v-if="hasFilters" @click="reset">清空筛选</el-button>
        <el-button v-else-if="canManage" type="primary" @click="openCreate">新建 Agent</el-button>
      </el-empty>

      <div v-if="total > 0" class="pager">
        <el-pagination
          :current-page="page"
          :page-size="pageSize"
          :page-sizes="[10, 20, 50]"
          :total="total"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @current-change="onPageChange"
          @size-change="onSizeChange"
        />
      </div>
    </el-card>

    <el-dialog v-model="dialogVisible" title="新建 Agent" width="520px">
      <el-alert
        v-if="createError"
        :title="createError"
        type="error"
        show-icon
        :closable="false"
        class="dialog-error"
      />
      <el-form ref="formRef" :model="form" :rules="rules" label-width="72px">
        <el-form-item label="编码" prop="code" :error="codeError || undefined">
          <el-input
            v-model="form.code"
            placeholder="如 support_agent"
            maxlength="64"
            @input="codeError = ''"
          />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 客服助手" maxlength="160" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="可选"
          />
        </el-form-item>
        <el-form-item label="标签" prop="tags">
          <el-select
            v-model="form.tags"
            multiple
            filterable
            allow-create
            default-first-option
            :reserve-keyword="false"
            placeholder="输入后回车添加，可留空"
            class="tag-input"
          />
        </el-form-item>
      </el-form>
      <p class="page-hint">创建后进入配置页选择模型与 Prompt 并保存，才会生成 v1。</p>
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
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-head .page-title {
  margin: 0;
}

.actions {
  display: flex;
  gap: 8px;
}

.filter-card {
  margin-bottom: 16px;
  border-radius: 10px;
}

.filters {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}

.search {
  width: 220px;
}

.select {
  width: 150px;
}

.table-card {
  border-radius: 10px;
}

.error-box {
  margin-bottom: 16px;
}

.error-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.unpublished {
  color: #9aa1b1;
  font-size: 12px;
}

.tag {
  margin-right: 4px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding-top: 16px;
}

.dialog-error {
  margin-bottom: 12px;
}

.tag-input {
  width: 100%;
}
</style>
