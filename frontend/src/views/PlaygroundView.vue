<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { describeError } from '@/api/client'
import {
  ENVIRONMENTS,
  listAgentVersions,
  listAgents,
  type Agent,
  type AgentVersion,
  type Environment,
  type PreviewRequest,
} from '@/api/endpoints'
import AgentPreviewPanel from '@/components/agent/AgentPreviewPanel.vue'

interface PromptVariableView {
  name: string
  type?: string
  required?: boolean
  default?: unknown
}

interface SnapshotRefs {
  model_id: string
  prompt_version_id: string
  tool_version_ids: string[]
  params: Record<string, unknown>
  policy: Record<string, unknown>
}

const route = useRoute()

const agents = ref<Agent[]>([])
const agentId = ref<string>(String(route.query.agentId ?? ''))
const env = ref<Environment>('DEV')
const loadingAgents = ref(false)
const agentsError = ref<string | null>(null)

const refs = ref<SnapshotRefs | null>(null)
const variables = ref<PromptVariableView[]>([])
const basisVersion = ref<AgentVersion | null>(null)
const refsError = ref<string | null>(null)
const refsLoading = ref(false)

const selectedAgent = computed(
  () => agents.value.find((item) => item.id === agentId.value) ?? null,
)

function buildRequest(input: Record<string, unknown>, sessionId: string): PreviewRequest {
  if (!refs.value) throw new Error('配置尚未加载')
  return {
    env: env.value,
    ...refs.value,
    input,
    preview_session_id: sessionId,
    timeout_s: null,
  }
}

async function loadAgents(): Promise<void> {
  loadingAgents.value = true
  agentsError.value = null
  try {
    const page = await listAgents({ page_size: 100 })
    agents.value = page.items
    if (!agentId.value && agents.value.length) {
      agentId.value = agents.value[0].id
    }
  } catch (err) {
    agentsError.value = describeError(err)
  } finally {
    loadingAgents.value = false
  }
}

/** 调试台复用详情页的预览能力：按最新版本快照解析引用后执行。 */
async function loadBasis(): Promise<void> {
  refs.value = null
  variables.value = []
  basisVersion.value = null
  refsError.value = null
  if (!agentId.value) return
  refsLoading.value = true
  try {
    const versionList = await listAgentVersions(agentId.value)
    const latest = [...versionList].sort((a, b) => b.version_no - a.version_no)[0]
    if (!latest) {
      refsError.value = '该 Agent 还没有版本；先在详情页配置并保存一个版本。'
      return
    }
    const snapshot = (latest.snapshot ?? {}) as {
      model?: { model_id?: string; params?: Record<string, unknown> }
      prompt?: { prompt_version_id?: string; variables?: PromptVariableView[] }
      tools?: { tool_version_id?: string }[]
      policy?: Record<string, unknown>
    }
    const modelId = snapshot.model?.model_id ?? ''
    const promptVersionId = snapshot.prompt?.prompt_version_id ?? ''
    if (!modelId || !promptVersionId) {
      refsError.value = '版本快照缺少模型或 Prompt 引用，请重新保存版本。'
      return
    }
    refs.value = {
      model_id: modelId,
      prompt_version_id: promptVersionId,
      tool_version_ids: (snapshot.tools ?? [])
        .map((item) => item.tool_version_id)
        .filter((value): value is string => Boolean(value)),
      params: snapshot.model?.params ?? {},
      policy: snapshot.policy ?? {},
    }
    variables.value = snapshot.prompt?.variables ?? []
    basisVersion.value = latest
  } catch (err) {
    refsError.value = describeError(err)
  } finally {
    refsLoading.value = false
  }
}

watch([agentId], () => {
  void loadBasis()
})

onMounted(async () => {
  await loadAgents()
  await loadBasis()
})
</script>

<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">调试台</h2>
      <span class="page-hint">预览执行：按引用装配，不产生版本、不改发布指针。</span>
    </div>

    <el-alert v-if="agentsError" :title="agentsError" type="error" show-icon :closable="false" />

    <el-card shadow="never" class="panel">
      <div class="run-form">
        <el-select
          v-model="agentId"
          placeholder="选择 Agent"
          class="agent-select"
          filterable
          :loading="loadingAgents"
        >
          <el-option
            v-for="item in agents"
            :key="item.id"
            :label="`${item.code} · ${item.name}`"
            :value="item.id"
          />
        </el-select>

        <el-radio-group v-model="env">
          <el-radio-button v-for="item in ENVIRONMENTS" :key="item" :value="item">
            {{ item }}
          </el-radio-button>
        </el-radio-group>

        <el-button link type="primary" :disabled="!selectedAgent" @click="selectedAgent && $router.push(`/agents/${selectedAgent.id}`)">
          在详情页编辑配置
        </el-button>
      </div>

      <div v-loading="refsLoading" class="basis">
        <template v-if="basisVersion">
          <el-tag size="small" type="info">按 v{{ basisVersion.version_no }} 配置预览</el-tag>
          <span class="hash" :title="basisVersion.config_hash">
            config_hash {{ basisVersion.config_hash.slice(0, 12) }}…
          </span>
        </template>
        <el-alert
          v-else-if="refsError"
          :title="refsError"
          type="warning"
          show-icon
          :closable="false"
        />
        <span v-else class="page-hint">选择 Agent 后加载最新版本配置。</span>
      </div>
    </el-card>

    <AgentPreviewPanel
      v-if="refs"
      :agent-id="agentId"
      :build-request="buildRequest"
      :variables="variables"
      class="panel"
    />
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 16px;
}

.page-head .page-title {
  margin: 0;
}

.panel {
  border-radius: 10px;
  margin-bottom: 16px;
}

.run-form {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}

.agent-select {
  width: 280px;
}

.basis {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 32px;
}

.hash {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: #5a6478;
}
</style>
