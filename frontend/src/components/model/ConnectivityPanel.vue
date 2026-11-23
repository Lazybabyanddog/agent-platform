<script setup lang="ts">
import { computed } from 'vue'

import type { ModelProbe, ProbeRecord } from '@/api/endpoints'
import NoticeBanner from '@/components/common/NoticeBanner.vue'
import { formatProbeTime, verdictMeta } from '@/utils/modelProbe'

/**
 * 连通性面板：结论只做六态映射，界面不根据 status_code 二次推断。
 * 配置标识（平台侧填的模型标识 / Base URL）与上游命中标识并排展示，不一致时高亮。
 */
const props = withDefaults(
  defineProps<{
    /** 探测对象名称，用于标题，如供应商名或模型编码。 */
    label: string
    /** 平台侧配置的标识：模型级为 model_name，供应商级为 Base URL。 */
    configValue: string
    result: ModelProbe | null
    history: ProbeRecord[]
    probing?: boolean
    canProbe?: boolean
  }>(),
  { probing: false, canProbe: false },
)

const emit = defineEmits<{ probe: [] }>()

const meta = computed(() => verdictMeta(props.result?.verdict))
const hit = computed(() => props.result?.observed_model_name ?? null)
const observed = computed(() => props.result?.observed_models ?? [])

const unverified = computed(
  () => props.result?.verdict === 'CREDENTIAL_OK_MODEL_UNVERIFIED',
)

/** 命中与配置不一致（含明确“模型不在清单”）。 */
const mismatch = computed(() => {
  if (!props.result) return false
  if (props.result.verdict === 'MODEL_NOT_FOUND') return true
  return hit.value !== null && hit.value !== props.configValue
})

const sortedHistory = computed(() =>
  [...props.history].sort(
    (left, right) =>
      new Date(right.created_at).getTime() - new Date(left.created_at).getTime(),
  ),
)

function isHit(name: string): boolean {
  return hit.value !== null && name === hit.value
}

function rowMeta(verdict: string) {
  return verdictMeta(verdict)
}
</script>

<template>
  <section class="probe">
    <header class="probe-head">
      <div>
        <h4 class="probe-title">{{ label }} · 连通性测试</h4>
        <p class="page-hint">
          探测只读上游配置，不写入供应商；每次结论都会留痕，可在历史中回看。
        </p>
      </div>
      <el-button
        type="primary"
        plain
        :loading="probing"
        :disabled="!canProbe"
        @click="emit('probe')"
      >
        立即探测
      </el-button>
    </header>

    <NoticeBanner
      v-if="result"
      :title="hit ? `${meta.label} · 命中 ${hit}` : meta.label"
      :description="meta.hint"
      :tone="meta.tone"
    />
    <p v-else-if="sortedHistory.length" class="page-hint">
      最近一次结论见下方历史；点“立即探测”可刷新。
    </p>

    <div v-if="result" class="compare">
      <div class="compare-item">
        <div class="compare-label">配置标识</div>
        <code class="compare-value">{{ configValue }}</code>
      </div>
      <div class="compare-arrow" :class="{ 'compare-arrow-bad': mismatch }">→</div>
      <div class="compare-item" :class="{ 'compare-item-bad': mismatch }">
        <div class="compare-label">上游命中</div>
        <code v-if="hit" class="compare-value">{{ hit }}</code>
        <span v-else class="compare-empty">
          {{ unverified ? '无法解析模型清单' : '未命中' }}
        </span>
      </div>
      <div v-if="mismatch" class="compare-note">
        配置与上游不一致：请核对模型标识是否写错，或该模型未在上游开通。
      </div>
    </div>

    <div v-if="result" class="probe-meta">
      <span>耗时 {{ result.latency_ms }} ms</span>
      <span v-if="result.status_code">HTTP {{ result.status_code }}</span>
      <span v-if="result.model_count !== null && result.model_count !== undefined">
        清单 {{ result.model_count }} 项
      </span>
    </div>

    <div v-if="observed.length" class="observed">
      <div class="observed-title">上游实测模型清单</div>
      <div class="observed-list">
        <el-tag
          v-for="name in observed"
          :key="name"
          :type="isHit(name) ? 'success' : 'info'"
          :effect="isHit(name) ? 'dark' : 'plain'"
          size="small"
        >
          {{ name }}{{ isHit(name) ? ' · 命中' : '' }}
        </el-tag>
      </div>
    </div>
    <p v-else-if="result && unverified" class="page-hint">
      上游未返回可解析的模型清单，无法列出实测模型。
    </p>

    <el-collapse class="history">
      <el-collapse-item
        name="history"
        :title="`探测历史（最近 ${sortedHistory.length} 次，按时间倒序）`"
      >
        <el-table :data="sortedHistory" size="small" empty-text="还没有探测记录">
          <el-table-column label="时间" width="180">
            <template #default="{ row }">{{ formatProbeTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column label="结论" width="190">
            <template #default="{ row }">
              <el-tag :type="rowMeta(row.verdict).tag" size="small">
                {{ rowMeta(row.verdict).label }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="耗时" width="90">
            <template #default="{ row }">{{ row.latency_ms }} ms</template>
          </el-table-column>
          <el-table-column label="HTTP" width="80">
            <template #default="{ row }">{{ row.status_code ?? '—' }}</template>
          </el-table-column>
          <el-table-column label="命中模型" min-width="160">
            <template #default="{ row }">{{ row.observed_model_name ?? '—' }}</template>
          </el-table-column>
        </el-table>
      </el-collapse-item>
    </el-collapse>
  </section>
</template>

<style scoped>
.probe {
  border: 1px solid #e8eaf0;
  border-radius: 10px;
  padding: 16px;
  background: #fff;
}

.probe-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 8px;
}

.probe-title {
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
}

.compare {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 32px minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  padding: 12px;
  border-radius: 8px;
  background: #f7f9fc;
  margin-bottom: 12px;
}

.compare-item {
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid #e3e8f2;
  background: #fff;
  min-width: 0;
}

.compare-item-bad {
  border-color: #ffb4b4;
  background: #fff5f5;
}

.compare-label {
  font-size: 11px;
  color: #8a93a5;
  margin-bottom: 2px;
}

.compare-value {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #1f2430;
  word-break: break-all;
}

.compare-empty {
  font-size: 12px;
  color: #d64545;
}

.compare-arrow {
  text-align: center;
  color: #9aa3b5;
}

.compare-arrow-bad {
  color: #d64545;
}

.compare-note {
  grid-column: 1 / -1;
  font-size: 12px;
  color: #b23b3b;
}

.probe-meta {
  display: flex;
  gap: 16px;
  font-size: 12px;
  color: #5a6478;
  margin-bottom: 12px;
}

.observed-title {
  font-size: 12px;
  font-weight: 600;
  color: #3d4655;
  margin-bottom: 6px;
}

.observed-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 14px;
}

.history {
  margin-top: 12px;
  border-top: none;
}
</style>
