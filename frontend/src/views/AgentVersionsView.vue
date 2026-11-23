<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { describeError } from '@/api/client'
import {
  diffAgentVersions,
  getAgent,
  listAgentVersions,
  type Agent,
  type AgentVersion,
  type AgentVersionDiff,
} from '@/api/endpoints'
import JsonBlock from '@/components/JsonBlock.vue'

const route = useRoute()
const router = useRouter()
const agentId = computed(() => String(route.params.agentId ?? ''))

const agent = ref<Agent | null>(null)
const versions = ref<AgentVersion[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const selectedId = ref<string>('')
const baseId = ref<string>('')
const targetId = ref<string>('')
const diff = ref<AgentVersionDiff | null>(null)
const diffLoading = ref(false)
const diffError = ref<string | null>(null)

const selected = computed(
  () => versions.value.find((item) => item.id === selectedId.value) ?? null,
)

const sorted = computed(() => [...versions.value].sort((a, b) => b.version_no - a.version_no))

function formatTime(value: string): string {
  return new Date(value).toLocaleString()
}

function short(value: string): string {
  return value.length > 12 ? `${value.slice(0, 8)}…` : value
}

function selectRow(row: AgentVersion): void {
  selectedId.value = row.id
}

function rowClass({ row }: { row: AgentVersion }): string {
  return row.id === selectedId.value ? 'row-selected' : ''
}

function toolNames(kind: 'added' | 'removed' | 'changed'): string[] {
  const items = diff.value?.tools[kind] ?? []
  return items.map((item) =>
    String(item.tool_code ?? item.tool_version_id ?? item.tool_id ?? '未命名工具'),
  )
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [detail, list] = await Promise.all([
      getAgent(agentId.value),
      listAgentVersions(agentId.value),
    ])
    agent.value = detail
    versions.value = list
    if (list.length) {
      selectedId.value = list[0].id
      targetId.value = list[0].id
      baseId.value = list[1]?.id ?? list[0].id
    }
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

async function compare(): Promise<void> {
  diffError.value = null
  diff.value = null
  if (!baseId.value || !targetId.value) {
    diffError.value = '请选择两个版本'
    return
  }
  diffLoading.value = true
  try {
    diff.value = await diffAgentVersions(baseId.value, targetId.value)
  } catch (err) {
    diffError.value = describeError(err)
  } finally {
    diffLoading.value = false
  }
}

watch([baseId, targetId], () => {
  diff.value = null
  diffError.value = null
})

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <el-button link type="primary" @click="router.push('/agents')">← 返回列表</el-button>
        <h2 class="page-title">
          版本与快照
          <span v-if="agent" class="agent-chip">{{ agent.code }} · {{ agent.name }}</span>
        </h2>
      </div>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <el-row :gutter="16">
      <el-col :span="10">
        <el-card shadow="never" class="panel">
          <template #header>
            <span class="panel-title">版本列表</span>
          </template>
          <el-table
            v-loading="loading"
            :data="sorted"
            size="small"
            row-key="id"
            :row-class-name="rowClass"
            @row-click="selectRow"
          >
            <el-table-column label="版本" width="80">
              <template #default="{ row }">v{{ row.version_no }}</template>
            </el-table-column>
            <el-table-column label="配置哈希" width="120">
              <template #default="{ row }">
                <el-tooltip :content="row.config_hash" placement="top">
                  <code>{{ short(row.config_hash) }}</code>
                </el-tooltip>
              </template>
            </el-table-column>
            <el-table-column prop="note" label="备注" show-overflow-tooltip />
            <el-table-column label="创建时间" width="170">
              <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
            </el-table-column>
          </el-table>
          <p v-if="!loading && !versions.length" class="page-hint">该 Agent 还没有版本。</p>
        </el-card>
      </el-col>

      <el-col :span="14">
        <el-card shadow="never" class="panel">
          <template #header>
            <div class="panel-head">
              <span class="panel-title">配置快照</span>
              <el-tag v-if="selected" size="small" type="info">v{{ selected.version_no }}</el-tag>
            </div>
          </template>
          <template v-if="selected">
            <el-descriptions :column="2" size="small" border label-width="92px" class="meta">
              <el-descriptions-item label="快照 schema">
                v{{ selected.snapshot_schema_version }}
              </el-descriptions-item>
              <el-descriptions-item label="配置哈希">
                <code class="hash">{{ selected.config_hash }}</code>
              </el-descriptions-item>
              <el-descriptions-item label="创建时间">
                {{ formatTime(selected.created_at) }}
              </el-descriptions-item>
              <el-descriptions-item label="备注">{{ selected.note ?? '—' }}</el-descriptions-item>
            </el-descriptions>
            <JsonBlock :value="selected.snapshot" max-height="360px" />
          </template>
          <el-empty v-else description="选择左侧版本查看快照" />
        </el-card>
      </el-col>
    </el-row>

    <el-card shadow="never" class="panel compare">
      <template #header>
        <div class="panel-head">
          <span class="panel-title">两版本对比</span>
          <div class="compare-actions">
            <el-select v-model="baseId" placeholder="基线版本" class="version-select">
              <el-option
                v-for="item in sorted"
                :key="item.id"
                :label="`v${item.version_no}`"
                :value="item.id"
              />
            </el-select>
            <span class="arrow">→</span>
            <el-select v-model="targetId" placeholder="目标版本" class="version-select">
              <el-option
                v-for="item in sorted"
                :key="item.id"
                :label="`v${item.version_no}`"
                :value="item.id"
              />
            </el-select>
            <el-button type="primary" :loading="diffLoading" @click="compare">对比</el-button>
          </div>
        </div>
      </template>

      <el-alert v-if="diffError" :title="diffError" type="error" show-icon :closable="false" />

      <template v-if="diff">
        <div class="diff-summary">
          <el-tag :type="diff.identical ? 'success' : 'warning'" size="small">
            {{ diff.identical ? '两个版本配置完全一致' : '存在差异' }}
          </el-tag>
          <span class="page-hint">
            v{{ diff.base_version_no }} → v{{ diff.target_version_no }}
            <template v-if="diff.config_hash_changed">（config_hash 已变化）</template>
          </span>
        </div>

        <el-row :gutter="16" class="diff-grid">
          <el-col :span="12">
            <div class="diff-block">
              <div class="diff-title">
                模型
                <el-tag :type="diff.model.changed ? 'warning' : 'info'" size="small">
                  {{ diff.model.changed ? '已变更' : '未变更' }}
                </el-tag>
              </div>
              <JsonBlock :value="{ base: diff.model.base, target: diff.model.target }" />
            </div>
          </el-col>
          <el-col :span="12">
            <div class="diff-block">
              <div class="diff-title">
                Prompt
                <el-tag :type="diff.prompt.changed ? 'warning' : 'info'" size="small">
                  {{ diff.prompt.changed ? '已变更' : '未变更' }}
                </el-tag>
              </div>
              <JsonBlock :value="{ base: diff.prompt.base, target: diff.prompt.target }" />
            </div>
          </el-col>
        </el-row>

        <div class="diff-block">
          <div class="diff-title">工具</div>
          <div class="tool-row">
            <span class="tool-label">新增</span>
            <el-tag v-for="name in toolNames('added')" :key="`add-${name}`" type="success">
              {{ name }}
            </el-tag>
            <span v-if="!toolNames('added').length" class="page-hint">无</span>
          </div>
          <div class="tool-row">
            <span class="tool-label">移除</span>
            <el-tag v-for="name in toolNames('removed')" :key="`del-${name}`" type="danger">
              {{ name }}
            </el-tag>
            <span v-if="!toolNames('removed').length" class="page-hint">无</span>
          </div>
          <div class="tool-row">
            <span class="tool-label">变更</span>
            <el-tag v-for="name in toolNames('changed')" :key="`chg-${name}`" type="warning">
              {{ name }}
            </el-tag>
            <span v-if="!toolNames('changed').length" class="page-hint">无</span>
          </div>
        </div>

        <div class="diff-block">
          <div class="diff-title">执行策略</div>
          <JsonBlock :value="diff.policy" />
        </div>
      </template>
      <p v-else-if="!diffError" class="page-hint">选择两个版本后点击「对比」。</p>
    </el-card>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-title {
  margin: 6px 0 0;
}

.agent-chip {
  margin-left: 8px;
  font-size: 12px;
  font-weight: 400;
  color: #6b7280;
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

.meta {
  margin-bottom: 12px;
}

.hash {
  word-break: break-all;
  font-size: 12px;
}

:deep(.el-table__body tr.row-selected > td.el-table__cell) {
  background: #eef3ff;
}

.compare-actions {
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

.diff-grid {
  margin-bottom: 0;
}

.diff-block {
  margin-bottom: 12px;
}

.diff-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
  font-weight: 600;
  font-size: 13px;
}

.tool-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}

.tool-label {
  width: 40px;
  font-size: 12px;
  color: #6b7280;
}
</style>
