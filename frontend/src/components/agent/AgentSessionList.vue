<script setup lang="ts">
import { computed } from 'vue'
import { Delete, Plus, Refresh } from '@element-plus/icons-vue'

import type { SessionItem } from '@/api/endpoints'

const props = defineProps<{
  sessions: SessionItem[]
  total: number
  loading?: boolean
  loadingMore?: boolean
  error?: string | null
  selectedId?: string | null
  /** 正在提交删除的会话标识；只锁行内按钮，不影响列表其他交互。 */
  deletingId?: string | null
}>()

const emit = defineEmits<{
  (e: 'select', session: SessionItem): void
  (e: 'delete', session: SessionItem): void
  (e: 'load-more'): void
  (e: 'retry'): void
  (e: 'new-session'): void
}>()

const canLoadMore = computed(() => props.sessions.length < props.total)

const units: [Intl.RelativeTimeFormatUnit, number][] = [
  ['day', 86_400_000],
  ['hour', 3_600_000],
  ['minute', 60_000],
  ['second', 1_000],
]
const relative = new Intl.RelativeTimeFormat('zh-CN', { numeric: 'auto' })

/** 列表里的时间只做粗粒度提示，精确时间放在 title 上避免干扰阅读。 */
function relativeTime(value: string): string {
  const timestamp = new Date(value).getTime()
  if (Number.isNaN(timestamp)) return value
  const diff = timestamp - Date.now()
  const abs = Math.abs(diff)
  for (const [unit, ms] of units) {
    if (abs >= ms || unit === 'second') return relative.format(Math.round(diff / ms), unit)
  }
  return value
}

/** 标题缺失（存量数据）时用环境 · 轮数 · 相对时间占位。 */
function sessionLabel(item: SessionItem): string {
  if (item.title) return item.title
  return `${item.env} · ${item.turn_count} 轮 · ${relativeTime(item.last_active_at)}`
}
</script>

<template>
  <el-card shadow="never" class="session-card">
    <template #header>
      <div class="list-head">
        <div class="head-left">
          <span class="list-title">会话</span>
          <el-tag v-if="total" size="small" type="info" effect="plain">{{ total }}</el-tag>
        </div>
        <el-button size="small" type="primary" plain :icon="Plus" @click="emit('new-session')">
          新建
        </el-button>
      </div>
    </template>

    <el-alert v-if="error" type="error" :closable="false" show-icon class="list-error">
      <template #title>
        <div class="error-row">
          <span>{{ error }}</span>
          <el-button link type="primary" :icon="Refresh" @click="emit('retry')">重试</el-button>
        </div>
      </template>
    </el-alert>

    <div v-loading="loading && !sessions.length" class="session-body">
      <el-empty
        v-if="!loading && !sessions.length && !error"
        description="还没有会话；发送一条消息即可创建。"
        :image-size="72"
      >
        <el-button type="primary" plain :icon="Plus" @click="emit('new-session')">
          新建会话
        </el-button>
      </el-empty>

      <ul v-else class="session-list">
        <li
          v-for="item in sessions"
          :key="item.id"
          class="session-item"
          :class="{ 'is-active': item.id === selectedId, 'is-expired': item.expired }"
          :title="new Date(item.last_active_at).toLocaleString()"
          @click="emit('select', item)"
        >
          <div class="item-head">
            <div class="item-title">{{ sessionLabel(item) }}</div>
            <el-button
              v-if="item.can_delete"
              class="item-delete"
              link
              type="danger"
              :icon="Delete"
              :loading="deletingId === item.id"
              title="删除会话（不可恢复）"
              @click.stop="emit('delete', item)"
            />
          </div>
          <div class="item-meta">
            <span>{{ item.env }}</span>
            <span>{{ item.turn_count }} 轮</span>
            <span>{{ relativeTime(item.last_active_at) }}</span>
            <el-tag v-if="item.expired" size="small" type="danger" effect="plain">已过期</el-tag>
            <el-tag v-else-if="!item.owned" size="small" type="info" effect="plain">他人</el-tag>
          </div>
        </li>
      </ul>

      <div v-if="canLoadMore" class="load-more">
        <el-button
          link
          type="primary"
          :loading="loadingMore"
          :disabled="loading"
          @click="emit('load-more')"
        >
          加载更多（{{ sessions.length }} / {{ total }}）
        </el-button>
      </div>
    </div>
  </el-card>
</template>

<style scoped>
.session-card {
  border-radius: 10px;
}

.list-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.head-left {
  display: flex;
  align-items: center;
  gap: 8px;
}

.list-title {
  font-weight: 600;
}

.list-error {
  margin-bottom: 8px;
}

.error-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.session-body {
  min-height: 200px;
}

.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 520px;
  overflow-y: auto;
}

.session-item {
  padding: 9px 10px;
  border: 1px solid #edf0f5;
  border-radius: 8px;
  cursor: pointer;
  transition: border-color 0.15s, background 0.15s;
}

.session-item:hover {
  border-color: #c7d7f7;
  background: #f7faff;
}

.session-item.is-active {
  border-color: #3b82f6;
  background: #eef5ff;
}

.session-item.is-expired {
  opacity: 0.72;
}

.item-head {
  display: flex;
  align-items: flex-start;
  gap: 4px;
}

/* 悬停或聚焦时才显形，避免列表被一排删除按钮干扰；行本身已有 title 提示 */
.item-delete {
  flex: none;
  height: 18px;
  padding: 0 2px;
  opacity: 0;
  transition: opacity 0.15s;
}

.session-item:hover .item-delete,
.session-item.is-active .item-delete,
.item-delete:focus-visible {
  opacity: 1;
}

.item-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  line-height: 1.45;
  color: #1f2937;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
}

.item-meta {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
  margin-top: 5px;
  font-size: 11px;
  color: #8a91a3;
}

.load-more {
  display: flex;
  justify-content: center;
  padding-top: 8px;
}
</style>
