<script setup lang="ts">
/**
 * 可复用的声明/提示条：
 * - 变更影响（Base URL / 凭据对已发布版本立即生效）
 * - 停用语义（停用只阻断新绑定与发布，已发布版本继续运行）
 * - 探测结论与错误定位提示
 */
withDefaults(
  defineProps<{
    title: string
    tone?: 'info' | 'warning' | 'danger' | 'success'
    description?: string | null
  }>(),
  { tone: 'info', description: null },
)
</script>

<template>
  <div class="notice" :class="`notice-${tone}`" role="status">
    <div class="notice-title">{{ title }}</div>
    <p v-if="description" class="notice-desc">{{ description }}</p>
    <slot />
  </div>
</template>

<style scoped>
.notice {
  border-radius: 8px;
  border: 1px solid transparent;
  padding: 10px 14px;
  margin-bottom: 12px;
  font-size: 13px;
  line-height: 1.5;
}

.notice-title {
  font-weight: 600;
}

.notice-desc {
  margin: 4px 0 0;
  color: inherit;
  opacity: 0.85;
}

.notice-info {
  background: #f0f5ff;
  border-color: #c8d8ff;
  color: #1d3f8f;
}

.notice-warning {
  background: #fff8eb;
  border-color: #ffd899;
  color: #8a5300;
}

.notice-danger {
  background: #fef1f1;
  border-color: #ffc7c7;
  color: #8f1d1d;
}

.notice-success {
  background: #f0fbf4;
  border-color: #b9e6c9;
  color: #16643a;
}
</style>
