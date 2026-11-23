<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { ApiError, describeError } from '@/api/client'
import {
  addToolVersion,
  getTool,
  getToolGrant,
  grantTool,
  listToolVersions,
  revokeTool,
  updateToolStatus,
  type RiskLevel,
  type Tool,
  type ToolGrant,
  type ToolVersion,
} from '@/api/endpoints'
import ToolCallOverview from '@/components/tool/ToolCallOverview.vue'
import ToolSpecEditor from '@/components/tool/ToolSpecEditor.vue'
import ToolTestPanel from '@/components/tool/ToolTestPanel.vue'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()

const toolId = computed(() => String(route.params.toolId ?? ''))

const tool = ref<Tool | null>(null)
const versions = ref<ToolVersion[]>([])
const grant = ref<ToolGrant | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)
const statusCode = ref<number | null>(null)

const activeTab = ref<'contract' | 'test' | 'calls'>('contract')
const editorRef = ref<InstanceType<typeof ToolSpecEditor>>()
const selectedVersionId = ref('')
const riskLevel = ref<RiskLevel>('READ_ONLY')
const editorModel = ref<Record<string, unknown>>({})
const editorErrors = ref<Record<string, string>>({})
const savingVersion = ref(false)
const togglingStatus = ref(false)
const granting = ref(false)
const callOverview = ref<InstanceType<typeof ToolCallOverview>>()

const RISK_LEVELS: RiskLevel[] = ['READ_ONLY', 'WRITE', 'HIGH']
const DEFAULT_SPEC = {
  method: 'POST',
  url: 'https://',
  timeout_s: 15,
  headers: {},
  auth: { type: 'none' },
  input_schema: { type: 'object', properties: {} },
}

const canManage = computed(() => session.can('tool:manage'))
const sortedVersions = computed(() =>
  [...versions.value].sort((left, right) => right.version_no - left.version_no),
)
const selectedVersion = computed(
  () => versions.value.find((item) => item.id === selectedVersionId.value) ?? null,
)
const dirty = computed(() => {
  if (!selectedVersion.value) return true
  if (riskLevel.value !== selectedVersion.value.risk_level) return true
  return JSON.stringify(editorModel.value) !== JSON.stringify(selectedVersion.value.spec)
})

function riskType(risk: RiskLevel | null): 'success' | 'warning' | 'danger' | 'info' {
  if (risk === 'HIGH') return 'danger'
  if (risk === 'WRITE') return 'warning'
  if (risk === 'READ_ONLY') return 'success'
  return 'info'
}

function formatTime(value: string | null): string {
  return value ? new Date(value).toLocaleString() : '—'
}

function selectVersion(version: ToolVersion): void {
  selectedVersionId.value = version.id
  riskLevel.value = version.risk_level
  editorModel.value = JSON.parse(JSON.stringify(version.spec)) as Record<string, unknown>
  editorErrors.value = {}
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  statusCode.value = null
  try {
    const [toolData, versionData, grantData] = await Promise.all([
      getTool(toolId.value),
      listToolVersions(toolId.value),
      getToolGrant(toolId.value),
    ])
    tool.value = toolData
    versions.value = versionData
    grant.value = grantData

    const preferred = versionData.find((item) => item.version_no === toolData.latest_version_no)
    if (preferred) {
      selectVersion(preferred)
    } else if (!selectedVersionId.value) {
      editorModel.value = JSON.parse(JSON.stringify(DEFAULT_SPEC)) as Record<string, unknown>
      riskLevel.value = 'READ_ONLY'
      selectedVersionId.value = ''
    }
  } catch (err) {
    if (err instanceof ApiError) statusCode.value = err.status
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

const KNOWN_SPEC_FIELDS = new Set([
  'spec',
  'spec.method',
  'spec.url',
  'spec.timeout_s',
  'spec.headers',
  'spec.auth',
  'spec.auth.secret_ref_id',
  'spec.auth.name',
  'spec.input_schema',
  'spec.output_schema',
])

function applyDefinitionError(err: ApiError): void {
  const mapped: Record<string, string> = {}
  const unmapped: string[] = []
  for (const detail of err.details) {
    const field = detail.field ?? ''
    if (KNOWN_SPEC_FIELDS.has(field)) {
      mapped[field] = detail.reason ?? '不合法'
    } else {
      unmapped.push(`${field || 'spec'}: ${detail.reason ?? '不合法'}`)
    }
  }
  if (unmapped.length) mapped._summary = unmapped.join('；')
  editorErrors.value = mapped
  error.value = unmapped.length ? '工具定义存在未映射到具体控件的错误，请查看上方汇总' : null
}

async function saveVersion(): Promise<void> {
  const editor = editorRef.value
  if (!editor) return
  editorErrors.value = {}
  const localErrors = editor.validate()
  if (Object.keys(localErrors).length) {
    editorErrors.value = localErrors
    return
  }

  if (selectedVersion.value && !dirty.value) {
    ElMessage.info('当前版本没有改动，无需新建版本')
    return
  }

  savingVersion.value = true
  try {
    const previousHash = sortedVersions.value[0]?.config_hash ?? null
    const created = await addToolVersion(toolId.value, {
      spec: editor.getSpec(),
      risk_level: riskLevel.value,
    })
    tool.value = await getTool(toolId.value)
    versions.value = await listToolVersions(toolId.value)
    selectVersion(created)
    if (previousHash && created.config_hash === previousHash) {
      ElMessage.warning(`已保存为 v${created.version_no}，但与上一版本等价（config_hash 相同）`)
    } else {
      ElMessage.success(`已保存为 v${created.version_no}`)
    }
  } catch (err) {
    if (err instanceof ApiError && err.code === 'VALIDATION_FAILED') {
      applyDefinitionError(err)
    } else {
      error.value = describeError(err)
    }
  } finally {
    savingVersion.value = false
  }
}

async function toggleStatus(value: boolean): Promise<void> {
  if (!tool.value) return
  togglingStatus.value = true
  try {
    tool.value = await updateToolStatus(toolId.value, {
      status: value ? 'ACTIVE' : 'DISABLED',
    })
    ElMessage.success(value ? '工具已启用' : '工具已停用')
  } catch (err) {
    error.value = describeError(err)
    await load()
  } finally {
    togglingStatus.value = false
  }
}

async function toggleGrant(): Promise<void> {
  if (!tool.value || !grant.value) return
  if (grant.value.granted) {
    try {
      await ElMessageBox.confirm(
        '撤销后，引用该工具的 Agent 版本将无法再绑定或调用，确认撤销？',
        '撤销授权',
        { type: 'warning', confirmButtonText: '撤销授权', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }

  granting.value = true
  try {
    if (grant.value.granted) {
      await revokeTool(toolId.value)
      ElMessage.success('已撤销授权，该工具不可被新的 Agent 版本绑定')
    } else {
      await grantTool(toolId.value)
      ElMessage.success('已授权给本租户，可用于 Agent 绑定')
    }
    grant.value = await getToolGrant(toolId.value)
    tool.value = await getTool(toolId.value)
  } catch (err) {
    error.value = describeError(err)
  } finally {
    granting.value = false
  }
}

async function confirmDisable(value: boolean): Promise<void> {
  if (!value) {
    try {
      await ElMessageBox.confirm(
        '停用后，引用了该工具的 Agent 版本在运行时会调用失败。确认停用？',
        '停用工具',
        { type: 'warning', confirmButtonText: '停用', cancelButtonText: '取消' },
      )
    } catch {
      return
    }
  }
  await toggleStatus(value)
}

onMounted(load)
</script>

<template>
  <div v-loading="loading">
    <el-page-header class="breadcrumb" @back="router.push('/tools')">
      <template #content>
        <span class="title">{{ tool?.name ?? '工具详情' }}</span>
        <el-tag v-if="tool" class="tag" size="small" effect="plain">{{ tool.code }}</el-tag>
        <el-tag v-if="tool" class="tag" size="small">{{ tool.type }}</el-tag>
      </template>
      <template #extra>
        <div v-if="tool" class="head-actions">
          <el-tag :type="tool.status === 'ACTIVE' ? 'success' : 'info'" size="small">
            {{ tool.status === 'ACTIVE' ? '启用中' : '已停用' }}
          </el-tag>
          <el-switch
            v-if="canManage"
            :model-value="tool.status === 'ACTIVE'"
            :loading="togglingStatus"
            inline-prompt
            active-text="启用"
            inactive-text="停用"
            @change="(value: boolean) => confirmDisable(value)"
          />
          <el-button v-if="canManage" :loading="granting" @click="toggleGrant">
            {{ grant?.granted ? '撤销授权' : '授权给 Agent' }}
          </el-button>
          <el-button :loading="loading" @click="load">刷新</el-button>
        </div>
      </template>
    </el-page-header>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="alert" />

    <el-result
      v-if="!loading && !tool && error"
      :icon="statusCode === 403 ? 'warning' : 'error'"
      :title="statusCode === 403 ? '没有访问权限' : '工具不存在'"
      :sub-title="statusCode === 403 ? '当前账号缺少 tool:read 权限。' : '请返回列表确认工具仍存在。'"
    >
      <template #extra>
        <el-button @click="router.push('/tools')">返回工具列表</el-button>
      </template>
    </el-result>

    <template v-if="tool">
      <el-card shadow="never" class="summary">
        <el-descriptions :column="4" size="small">
          <el-descriptions-item label="风险等级">
            <el-tag v-if="tool.risk_level" :type="riskType(tool.risk_level)" size="small">
              {{ tool.risk_level }}
            </el-tag>
            <span v-else class="muted">无版本</span>
          </el-descriptions-item>
          <el-descriptions-item label="最新版本">
            {{ tool.latest_version_no === null ? '—' : `v${tool.latest_version_no}` }}
          </el-descriptions-item>
          <el-descriptions-item label="授权状态">
            <el-tag :type="grant?.granted ? 'success' : 'info'" size="small">
              {{ grant?.granted ? '已授权' : '未授权' }}
            </el-tag>
          </el-descriptions-item>
          <el-descriptions-item label="授权时间">
            {{ formatTime(grant?.granted_at ?? null) }}
          </el-descriptions-item>
          <el-descriptions-item label="描述" :span="2">
            {{ tool.description || '—' }}
          </el-descriptions-item>
          <el-descriptions-item label="更新时间">{{ formatTime(tool.updated_at) }}</el-descriptions-item>
          <el-descriptions-item label="授权人">
            <span class="mono">{{ grant?.granted_by ? grant.granted_by.slice(0, 8) : '—' }}</span>
          </el-descriptions-item>
        </el-descriptions>
      </el-card>

      <el-alert
        v-if="tool.status === 'DISABLED'"
        title="该工具已停用：引用了它的 Agent 版本在运行时会被拒绝调用（RESOURCE_UNAVAILABLE）。"
        type="warning"
        show-icon
        :closable="false"
        class="alert"
      />
      <el-alert
        v-else-if="grant && !grant.granted"
        title="该工具尚未授权给本租户：Agent 无法绑定它，但契约仍可编辑并保存新版本。"
        type="info"
        show-icon
        :closable="false"
        class="alert"
      />

      <el-tabs v-model="activeTab" class="tabs">
        <el-tab-pane label="契约与版本" name="contract">
          <el-row :gutter="16">
            <el-col :span="15">
              <el-card shadow="never">
                <template #header>
                  <div class="card-head">
                    <span>
                      {{ selectedVersion ? `v${selectedVersion.version_no} 契约` : '首个版本契约' }}
                      <el-tag v-if="dirty" size="small" type="warning" class="tag">有改动</el-tag>
                    </span>
                    <div class="version-actions">
                      <el-select v-if="canManage" v-model="riskLevel" size="small" class="risk-select">
                        <el-option
                          v-for="level in RISK_LEVELS"
                          :key="level"
                          :label="level"
                          :value="level"
                        />
                      </el-select>
                      <el-button
                        v-if="canManage"
                        type="primary"
                        size="small"
                        :loading="savingVersion"
                        :disabled="!!selectedVersion && !dirty"
                        @click="saveVersion"
                      >
                        保存为新版本
                      </el-button>
                    </div>
                  </div>
                </template>
                <el-alert
                  v-if="editorErrors._summary"
                  :title="`未映射到控件的校验错误：${editorErrors._summary}`"
                  type="error"
                  show-icon
                  :closable="false"
                  class="alert"
                />
                <ToolSpecEditor
                  ref="editorRef"
                  v-model="editorModel"
                  :errors="editorErrors"
                  :readonly="!canManage"
                  :type="tool.type"
                />
              </el-card>
            </el-col>
            <el-col :span="9">
              <el-card shadow="never">
                <template #header>
                  <div class="card-head">
                    <span>版本历史</span>
                    <span class="muted">{{ versions.length }} 个版本</span>
                  </div>
                </template>
                <el-empty v-if="!versions.length" description="还没有版本" :image-size="60" />
                <el-table
                  v-else
                  :data="sortedVersions"
                  size="small"
                  row-key="id"
                  highlight-current-row
                  :current-row-key="selectedVersionId"
                  @row-click="selectVersion"
                >
                  <el-table-column label="版本" width="70">
                    <template #default="{ row }">v{{ row.version_no }}</template>
                  </el-table-column>
                  <el-table-column label="风险" width="100">
                    <template #default="{ row }">
                      <el-tag :type="riskType(row.risk_level)" size="small">
                        {{ row.risk_level }}
                      </el-tag>
                    </template>
                  </el-table-column>
                  <el-table-column label="保存时间" min-width="150">
                    <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
                  </el-table-column>
                  <el-table-column label="配置指纹" width="110">
                    <template #default="{ row }">
                      <span class="mono" :title="row.config_hash">
                        {{ row.config_hash.slice(0, 10) }}
                      </span>
                    </template>
                  </el-table-column>
                </el-table>
                <p class="hint">选择任一版本查看契约；修改后保存会生成新版本，历史版本不可变。</p>
              </el-card>
            </el-col>
          </el-row>
        </el-tab-pane>

        <el-tab-pane v-if="canManage" label="测试调用" name="test">
          <el-card shadow="never">
            <ToolTestPanel
              :versions="versions"
              :can-manage="canManage"
              :granted="grant?.granted ?? false"
              :active="tool.status === 'ACTIVE'"
              @executed="callOverview?.reload()"
              @grant="toggleGrant"
              @enable="confirmDisable(true)"
            />
          </el-card>
        </el-tab-pane>

        <el-tab-pane label="调用记录" name="calls">
          <el-card shadow="never">
            <ToolCallOverview ref="callOverview" :tool-id="toolId" :versions="versions" />
          </el-card>
        </el-tab-pane>
      </el-tabs>
    </template>
  </div>
</template>

<style scoped>
.breadcrumb {
  margin-bottom: 16px;
}

.title {
  font-size: 18px;
  font-weight: 600;
}

.tag {
  margin-left: 8px;
}

.head-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.alert {
  margin-bottom: 12px;
}

.summary {
  margin-bottom: 16px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.version-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.risk-select {
  width: 130px;
}

.hint {
  margin: 12px 0 0;
  font-size: 12px;
  color: #8a93a5;
}

.muted {
  font-size: 12px;
  color: #9aa3b5;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
</style>
