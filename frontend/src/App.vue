<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { onTraceId } from '@/api/client'
import LoginCard from '@/components/LoginCard.vue'
import { useSessionStore } from '@/stores/session'

const route = useRoute()
const session = useSessionStore()

const activeMenu = computed(() => {
  if (route.path.startsWith('/agents')) return '/agents'
  if (route.path.startsWith('/prompts')) return '/prompts'
  if (route.path.startsWith('/tools')) return '/tools'
  if (route.path.startsWith('/models')) return '/models'
  return route.path
})
const traceId = ref<string | null>(null)

const navItems = [
  { path: '/agents', label: 'Agent 列表', permission: 'agent:read' },
  { path: '/prompts', label: 'Prompt 管理' },
  { path: '/tools', label: '工具管理' },
  { path: '/models', label: '模型配置', permission: 'model:read' },
  { path: '/releases', label: '发布与回滚', permission: 'run:read' },
  { path: '/playground', label: '调试台', permission: 'agent:invoke' },
]

const visibleNavItems = computed(() =>
  navItems.filter((item) => !item.permission || session.can(item.permission)),
)

let unsubscribe: (() => void) | null = null

onMounted(async () => {
  unsubscribe = onTraceId((value) => {
    traceId.value = value
  })
  await session.restore()
})

onUnmounted(() => {
  unsubscribe?.()
})

function shorten(value: string): string {
  return value.length > 18 ? `${value.slice(0, 8)}…${value.slice(-6)}` : value
}
</script>

<template>
  <el-container v-if="!session.token" class="shell shell-login">
    <el-main class="content">
      <LoginCard />
    </el-main>
  </el-container>

  <el-container v-else class="shell">
    <el-aside width="220px" class="sidebar">
      <div class="brand">
        <span class="brand-mark">AP</span>
        <div>
          <div class="brand-title">Agent 中台</div>
          <div class="brand-sub">Foundation Console</div>
        </div>
      </div>
      <el-menu :default-active="activeMenu" router class="nav">
        <el-menu-item v-for="item in visibleNavItems" :key="item.path" :index="item.path">
          {{ item.label }}
        </el-menu-item>
      </el-menu>
    </el-aside>

    <el-container>
      <el-header class="topbar">
        <div class="topbar-left">
          <span class="crumb">{{ route.meta.title ?? '' }}</span>
          <el-tooltip v-if="traceId" :content="traceId" placement="bottom">
            <span class="trace">trace {{ shorten(traceId) }}</span>
          </el-tooltip>
        </div>
        <div class="topbar-right">
          <span class="user">{{ session.displayName }}</span>
          <el-button link type="primary" @click="session.signOut()">退出</el-button>
        </div>
      </el-header>
      <el-main class="content">
        <router-view :key="route.fullPath" />
      </el-main>
    </el-container>
  </el-container>
</template>

<style scoped>
.shell {
  height: 100vh;
}

.shell-login {
  background: #f6f7fb;
}

.sidebar {
  background: #14161c;
  border-right: 1px solid #23262f;
  display: flex;
  flex-direction: column;
}

.brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 18px 16px;
  color: #f5f7fa;
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
  background: linear-gradient(135deg, #4f7cff, #7a5cff);
  font-weight: 700;
  font-size: 13px;
}

.brand-title {
  font-size: 14px;
  font-weight: 600;
}

.brand-sub {
  font-size: 11px;
  color: #7d8598;
}

.nav {
  border-right: none;
  background: transparent;
  --el-menu-bg-color: transparent;
  --el-menu-text-color: #b6bdcf;
  --el-menu-hover-bg-color: #1d2029;
  --el-menu-active-color: #ffffff;
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-bottom: 1px solid #e8eaf0;
  background: #ffffff;
}

.topbar-left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.crumb {
  font-weight: 600;
  color: #1f2430;
}

.trace {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11px;
  color: #5a6478;
  background: #f1f3f8;
  border-radius: 999px;
  padding: 2px 8px;
  cursor: default;
}

.user {
  font-size: 12px;
  color: #5a6478;
}

.content {
  background: #f6f7fb;
}
</style>
