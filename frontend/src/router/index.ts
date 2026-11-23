import { createRouter, createWebHistory } from 'vue-router'

import { useSessionStore } from '@/stores/session'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
    permission?: string
  }
}

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/agents' },
    {
      path: '/agents',
      name: 'agents',
      component: () => import('@/views/AgentListView.vue'),
      meta: { title: 'Agent 列表', permission: 'agent:read' },
    },
    {
      path: '/agents/:agentId/versions',
      name: 'agent-versions',
      component: () => import('@/views/AgentVersionsView.vue'),
      meta: { title: '版本与快照', permission: 'agent:read' },
    },
    {
      path: '/agents/:agentId',
      name: 'agent-detail',
      component: () => import('@/views/AgentDetailView.vue'),
      meta: { title: 'Agent 详情', permission: 'agent:read' },
    },
    {
      path: '/prompts',
      name: 'prompts',
      component: () => import('@/views/PromptListView.vue'),
      meta: { title: 'Prompt 管理' },
    },
    {
      path: '/prompts/:promptId',
      name: 'prompt-detail',
      component: () => import('@/views/PromptDetailView.vue'),
      meta: { title: 'Prompt 详情' },
    },
    {
      path: '/tools',
      name: 'tools',
      component: () => import('@/views/ToolListView.vue'),
      meta: { title: '工具管理' },
    },
    {
      path: '/tools/:toolId',
      name: 'tool-detail',
      component: () => import('@/views/ToolDetailView.vue'),
      meta: { title: '工具详情' },
    },
    {
      path: '/models',
      name: 'models',
      component: () => import('@/views/ModelConfigView.vue'),
      meta: { title: '模型配置', permission: 'model:read' },
    },
    {
      path: '/releases',
      name: 'releases',
      component: () => import('@/views/ReleaseView.vue'),
      meta: { title: '发布与回滚', permission: 'run:read' },
    },
    {
      path: '/playground',
      name: 'playground',
      component: () => import('@/views/PlaygroundView.vue'),
      meta: { title: '调试台', permission: 'agent:invoke' },
    },
    {
      path: '/forbidden',
      name: 'forbidden',
      component: () => import('@/views/ForbiddenView.vue'),
      meta: { title: '无权访问' },
    },
  ],
})

/** 仅登录后检查权限；未登录交给登录页，避免首次导航误判。 */
router.beforeEach((to) => {
  const required = to.meta.permission
  if (!required) return true
  const session = useSessionStore()
  if (!session.token) return true
  if (session.can(required)) return true
  return { name: 'forbidden', query: { from: to.fullPath, need: required } }
})

export default router
