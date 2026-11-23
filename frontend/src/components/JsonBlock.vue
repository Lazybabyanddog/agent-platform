<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    value: unknown
    maxHeight?: string
  }>(),
  { maxHeight: '260px' },
)

const text = computed(() => {
  if (props.value === null || props.value === undefined) return '—'
  try {
    return JSON.stringify(props.value, null, 2)
  } catch {
    return String(props.value)
  }
})
</script>

<template>
  <pre class="json-block" :style="{ maxHeight }">{{ text }}</pre>
</template>

<style scoped>
.json-block {
  margin: 0;
  padding: 10px 12px;
  border-radius: 8px;
  background: #0f1218;
  color: #d7e0f5;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.55;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
