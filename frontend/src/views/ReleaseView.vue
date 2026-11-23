<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref, watch } from 'vue'

import { describeError } from '@/api/client'
import {
  ENVIRONMENTS,
  getAgent,
  getAgentVersionReadiness,
  listAgentVersions,
  listAgents,
  listReleases,
  publishRelease,
  rollbackRelease,
  type Agent,
  type AgentEnvironmentState,
  type AgentReadiness,
  type AgentRelease,
  type AgentVersion,
  type Environment,
} from '@/api/endpoints'

const agents = ref<Agent[]>([])
const agentId = ref<string>('')
const env = ref<Environment>('DEV')
const versions = ref<AgentVersion[]>([])
const releases = ref<AgentRelease[]>([])
const agentDetail = ref<Agent | null>(null)

const loading = ref(false)
const error = ref<string | null>(null)
const publishVersionId = ref<string>('')
const publishNote = ref<string>('')
const publishing = ref(false)
const rollingBackId = ref<string | null>(null)

const readiness = ref<AgentReadiness | null>(null)
const readinessLoading = ref(false)
const readinessError = ref<string | null>(null)

const currentState = computed<AgentEnvironmentState | null>(
  () => agentDetail.value?.environments?.[env.value] ?? null,
)
const envReleases = computed(() => releases.value.filter((item) => item.env === env.value))
const sortedVersions = computed(() => [...versions.value].sort((a, b) => b.version_no - a.version_no))
const canPublish = computed(() => Boolean(readiness.value?.ready) && !readinessLoading.value)

function formatTime(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : '—'
}

function short(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value
}

async function loadAgents(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const page = await listAgents({ page_size: 100 })
    agents.value = page.items
    if (!agentId.value && agents.value.length) {
      agentId.value = agents.value[0].id
    }
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

async function loadReadiness(): Promise<void> {
  if (!agentId.value || !publishVersionId.value) {
    readiness.value = null
    return
  }
  readinessLoading.value = true
  readinessError.value = null
  try {
    readiness.value = await getAgentVersionReadiness(agentId.value, publishVersionId.value)
  } catch (err) {
    readiness.value = null
    readinessError.value = describeError(err)
  } finally {
    readinessLoading.value = false
  }
}

async function loadAgentData(): Promise<void> {
  if (!agentId.value) return
  loading.value = true
  error.value = null
  try {
    const [detail, versionList, releaseList] = await Promise.all([
      getAgent(agentId.value),
      listAgentVersions(agentId.value),
      listReleases(agentId.value),
    ])
    agentDetail.value = detail
    versions.value = versionList
    releases.value = releaseList
    const latest = [...versionList].sort((a, b) => b.version_no - a.version_no)[0]
    if (!versionList.some((item) => item.id === publishVersionId.value)) {
      publishVersionId.value = latest?.id ?? ''
    }
    await loadReadiness()
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

async function publish(): Promise<void> {
  if (!agentId.value || !publishVersionId.value) {
    ElMessage.warning('请选择要发布的版本')
    return
  }
  if (!readiness.value?.ready) {
    ElMessage.warning('发布体检未通过，先修复不可用引用')
    return
  }
  publishing.value = true
  try {
    const release = await publishRelease(agentId.value, {
      agent_version_id: publishVersionId.value,
      env: env.value,
      note: publishNote.value.trim() || null,
    })
    ElMessage.success(`已发布 v${release.version_no} 到 ${release.env}`)
    publishNote.value = ''
    await loadAgentData()
  } catch (err) {
    ElMessage.error(describeError(err))
    await loadAgentData()
  } finally {
    publishing.value = false
  }
}

async function rollback(release: AgentRelease): Promise<void> {
  try {
    await ElMessageBox.confirm(
      `确认将 ${release.env} 指针回滚到 v${release.version_no}（release ${short(release.release_id)}）？`,
      '回滚确认',
      { type: 'warning', confirmButtonText: '回滚', cancelButtonText: '取消' },
    )
  } catch {
    return
  }
  rollingBackId.value = release.release_id
  try {
    await rollbackRelease(agentId.value, { env: release.env, release_id: release.release_id })
    ElMessage.success(`已回滚到 v${release.version_no}`)
    await loadAgentData()
  } catch (err) {
    ElMessage.error(describeError(err))
    await loadAgentData()
  } finally {
    rollingBackId.value = null
  }
}

watch([agentId], () => {
  versions.value = []
  releases.value = []
  agentDetail.value = null
  readiness.value = null
  void loadAgentData()
})

watch([publishVersionId], () => {
  void loadReadiness()
})

onMounted(loadAgents)
</script>

<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">发布与回滚</h2>
      <div class="head-actions">
        <el-select v-model="agentId" placeholder="选择 Agent" class="agent-select">
          <el-option
            v-for="item in agents"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>
        <el-button :loading="loading" @click="loadAgentData">刷新</el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <el-empty v-if="!agents.length && !loading" description="暂无 Agent，请先在列表中创建" />

    <template v-else>
      <el-radio-group v-model="env" class="env-switch">
        <el-radio-button v-for="item in ENVIRONMENTS" :key="item" :value="item">
          {{ item }}
        </el-radio-button>
      </el-radio-group>

      <el-row :gutter="12" class="pointers">
        <el-col v-for="item in ENVIRONMENTS" :key="item" :span="8">
          <el-card
            shadow="never"
            class="pointer-card"
            :class="{ 'pointer-active': item === env }"
            @click="env = item"
          >
            <div class="pointer-env">{{ item }}</div>
            <template v-if="agentDetail?.environments?.[item]">
              <div class="pointer-version">
                v{{ agentDetail.environments[item]!.version_no }}
              </div>
              <div class="pointer-meta">
                release {{ short(agentDetail.environments[item]!.release_id) }}
              </div>
              <div class="pointer-meta">
                {{ formatTime(agentDetail.environments[item]!.released_at) }}
              </div>
            </template>
            <div v-else class="pointer-meta">未发布</div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="panel">
        <template #header>
          <div class="panel-head">
            <span class="panel-title">发布到 {{ env }}</span>
            <span class="page-hint">
              当前生效：
              <template v-if="currentState">v{{ currentState.version_no }}</template>
              <template v-else>无</template>
            </span>
          </div>
        </template>
        <div class="publish-form">
          <el-select v-model="publishVersionId" placeholder="选择版本" class="version-select">
            <el-option
              v-for="item in sortedVersions"
              :key="item.id"
              :label="`v${item.version_no}${item.note ? ` · ${item.note}` : ''}`"
              :value="item.id"
            />
          </el-select>
          <el-input v-model="publishNote" placeholder="发布备注（可选）" class="note-input" />
          <el-button type="primary" :loading="publishing" :disabled="!canPublish" @click="publish">
            发布到 {{ env }}
          </el-button>
        </div>
        <p v-if="!versions.length" class="page-hint">该 Agent 还没有可发布版本。</p>

        <div class="readiness">
          <div class="readiness-head">
            <span class="config-title">发布前体检</span>
            <el-button
              size="small"
              :loading="readinessLoading"
              :disabled="!publishVersionId"
              @click="loadReadiness"
            >
              重新体检
            </el-button>
          </div>

          <el-alert
            v-if="readinessError"
            :title="readinessError"
            type="error"
            show-icon
            :closable="false"
          />

          <template v-else-if="readiness">
            <el-alert
              v-if="readiness.ready"
              :title="`v${readiness.version_no} 全部引用可用，可以发布`"
              type="success"
              show-icon
              :closable="false"
            />
            <el-alert
              v-else
              :title="`v${readiness.version_no} 存在不可用引用，发布入口已阻断`"
              type="warning"
              show-icon
              :closable="false"
            />
            <el-table :data="readiness.bindings" size="small" row-key="resource_id" class="binding-table">
              <el-table-column prop="role" label="角色" width="90" />
              <el-table-column prop="resource_type" label="资源类型" width="130" />
              <el-table-column label="资源 ID" min-width="150">
                <template #default="{ row }"><code>{{ short(row.resource_id) }}</code></template>
              </el-table-column>
              <el-table-column label="可用" width="80">
                <template #default="{ row }">
                  <el-tag :type="row.available ? 'success' : 'danger'" size="small">
                    {{ row.available ? '是' : '否' }}
                  </el-tag>
                </template>
              </el-table-column>
              <el-table-column prop="reason" label="原因" min-width="180" show-overflow-tooltip />
            </el-table>
            <div v-if="readiness.preview" class="readiness-preview">
              最近同配置预览：{{ readiness.preview.status }} ·
              {{ formatTime(readiness.preview.started_at) }}
            </div>
            <div v-else class="readiness-preview">暂无同配置预览记录。</div>
          </template>

          <div v-else v-loading="readinessLoading" class="readiness-loading" />
        </div>
      </el-card>

      <el-card shadow="never" class="panel">
        <template #header>
          <span class="panel-title">{{ env }} 发布历史</span>
        </template>
        <el-table v-loading="loading" :data="envReleases" size="small" row-key="release_id">
          <el-table-column label="版本" width="80">
            <template #default="{ row }">v{{ row.version_no }}</template>
          </el-table-column>
          <el-table-column label="Release" width="160">
            <template #default="{ row }">
              <code>{{ short(row.release_id) }}</code>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="120">
            <template #default="{ row }">
              <el-tag
                size="small"
                :type="row.release_id === currentState?.release_id ? 'success' : 'info'"
              >
                {{ row.release_id === currentState?.release_id ? '当前指针' : row.status }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="released_at" label="发布时间" width="180">
            <template #default="{ row }">{{ formatTime(row.released_at) }}</template>
          </el-table-column>
          <el-table-column label="操作" width="110">
            <template #default="{ row }">
              <el-button
                link
                type="warning"
                :disabled="row.release_id === currentState?.release_id"
                :loading="rollingBackId === row.release_id"
                @click="rollback(row)"
              >
                回滚到此版本
              </el-button>
            </template>
          </el-table-column>
        </el-table>
        <p v-if="!loading && !envReleases.length" class="page-hint">
          {{ env }} 还没有发布记录。
        </p>
      </el-card>
    </template>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-head .page-title {
  margin: 0;
}

.head-actions {
  display: flex;
  gap: 8px;
}

.agent-select {
  width: 260px;
}

.env-switch {
  margin-bottom: 12px;
}

.pointers {
  margin-bottom: 4px;
}

.pointer-card {
  border-radius: 10px;
  cursor: pointer;
}

.pointer-active {
  border-color: #4f7cff;
}

.pointer-env {
  font-size: 11px;
  letter-spacing: 0.08em;
  color: #6b7280;
}

.pointer-version {
  font-size: 22px;
  font-weight: 600;
  margin: 4px 0;
}

.pointer-meta {
  font-size: 12px;
  color: #8b93a5;
}

.panel {
  border-radius: 10px;
  margin-top: 16px;
}

.panel-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title {
  font-weight: 600;
}

.publish-form {
  display: flex;
  gap: 8px;
}

.version-select {
  width: 260px;
}

.note-input {
  flex: 1;
}

.readiness {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed #e5e7eb;
}

.readiness-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.config-title {
  font-weight: 600;
  font-size: 13px;
}

.binding-table {
  margin-top: 10px;
}

.readiness-preview {
  margin-top: 10px;
  font-size: 12px;
  color: #5a6478;
}

.readiness-loading {
  height: 90px;
}
</style>
