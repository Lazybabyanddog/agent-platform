<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'

import { describeError } from '@/api/client'
import { listToolCalls, type ToolCallOverview, type ToolVersion } from '@/api/endpoints'

const props = defineProps<{
  toolId: string
  versions: ToolVersion[]
}>()

const overview = ref<ToolCallOverview | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

const versionLabels = computed(() => {
  const map = new Map<string, string>()
  for (const item of props.versions) map.set(item.id, `v${item.version_no}`)
  return map
})

function percent(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

function latency(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : `${value.toFixed(0)} ms`
}

function formatTime(value: string): string {
  return new Date(value).toLocaleString()
}

async function load(): Promise<void> {
  if (!props.toolId) return
  loading.value = true
  error.value = null
  try {
    overview.value = await listToolCalls(props.toolId, { limit: 20 })
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

watch(() => props.toolId, load)

onMounted(load)

defineExpose({ reload: load })
</script>

<template>
  <div v-loading="loading">
    <div class="head">
      <span v-if="overview" class="hint">
        统计窗口：{{ formatTime(overview.window_start) }} ~ {{ formatTime(overview.window_end) }}
      </span>
      <el-button size="small" :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <template v-if="overview">
      <el-row v-if="overview.total > 0" :gutter="12" class="stats">
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-label">调用次数</div>
            <div class="stat-value">{{ overview.total }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-label">成功率</div>
            <div class="stat-value">{{ percent(overview.success_rate) }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-label">平均耗时</div>
            <div class="stat-value">{{ latency(overview.avg_latency_ms) }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never">
            <div class="stat-label">失败次数</div>
            <div class="stat-value">{{ overview.failed }}</div>
          </el-card>
        </el-col>
      </el-row>

      <div v-if="overview.total > 0 && overview.errors.length" class="errors">
        <span class="errors-label">错误分布：</span>
        <el-tag
          v-for="item in overview.errors"
          :key="item.code"
          type="danger"
          size="small"
          class="error-tag"
        >
          {{ item.code }} × {{ item.count }}
        </el-tag>
      </div>

      <el-empty
        v-if="!overview.total"
        description="统计窗口内还没有调用记录。可在「测试调用」里发起一次。"
      />

      <el-table v-else :data="overview.items" size="small" row-key="id">
        <el-table-column label="时间" width="180">
          <template #default="{ row }">{{ formatTime(row.started_at) }}</template>
        </el-table-column>
        <el-table-column label="版本" width="80">
          <template #default="{ row }">
            {{ versionLabels.get(row.tool_version_id) ?? '—' }}
          </template>
        </el-table-column>
        <el-table-column label="状态" width="100">
          <template #default="{ row }">
            <el-tag :type="row.status === 'SUCCESS' ? 'success' : 'danger'" size="small">
              {{ row.status }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="耗时" width="100">
          <template #default="{ row }">{{ row.latency_ms }} ms</template>
        </el-table-column>
        <el-table-column label="错误码" min-width="160">
          <template #default="{ row }">{{ row.error_code ?? '—' }}</template>
        </el-table-column>
      </el-table>
    </template>
  </div>
</template>

<style scoped>
.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}

.hint {
  font-size: 12px;
  color: #8a93a5;
}

.stats {
  margin-bottom: 12px;
}

.stat-label {
  font-size: 12px;
  color: #8a93a5;
}

.stat-value {
  margin-top: 4px;
  font-size: 22px;
  font-weight: 600;
  color: #1f2430;
}

.errors {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.errors-label {
  font-size: 12px;
  color: #6b7280;
}

.error-tag {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}
</style>
