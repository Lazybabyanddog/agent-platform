<script setup lang="ts">
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, ref } from 'vue'

import { ApiError, describeError } from '@/api/client'
import {
  createModel,
  createProvider,
  listModelProbes,
  listModels,
  listProviderProbes,
  listProviders,
  listSecrets,
  probeModel,
  probeProvider,
  updateModel,
  updateModelStatus,
  updateProvider,
  type Model,
  type ModelProbe,
  type ModelStatusValue,
  type ModelUpdateInput,
  type ProbeRecord,
  type Provider,
  type ProviderCreateInput,
  type ProviderUpdateInput,
  type Secret,
} from '@/api/endpoints'
import NoticeBanner from '@/components/common/NoticeBanner.vue'
import ConnectivityPanel from '@/components/model/ConnectivityPanel.vue'
import ModelForm from '@/components/model/ModelForm.vue'
import ProviderForm from '@/components/model/ProviderForm.vue'
import { useSessionStore } from '@/stores/session'
import { formatProbeTime, verdictMeta } from '@/utils/modelProbe'

interface ModelPayload {
  code: string
  provider_id: string
  model_name: string
  ctx_window: number | null
  modalities: string[]
  capabilities: Record<string, unknown>
  param_template: Record<string, unknown>
  price: Record<string, unknown>
}

interface ProbeTarget {
  kind: 'provider' | 'model'
  id: string
  label: string
  /** 平台侧配置的标识：模型级为 model_name，供应商级为 Base URL。 */
  configValue: string
}

const session = useSessionStore()

const providers = ref<Provider[]>([])
const models = ref<Model[]>([])
const secrets = ref<Secret[]>([])
const loading = ref(false)
const error = ref<string | null>(null)

const providerKeyword = ref('')
const modelKeyword = ref('')
const providerFilter = ref<string | null>(null)

const canManage = computed(() => session.can('model:manage'))
const canReadSecrets = computed(() => session.can('secret:manage'))

const providerById = computed(() => new Map(providers.value.map((item) => [item.id, item])))

const filteredProviders = computed(() => {
  const text = providerKeyword.value.trim().toLowerCase()
  if (!text) return providers.value
  return providers.value.filter((provider) =>
    `${provider.name} ${provider.base_url} ${provider.region ?? ''}`.toLowerCase().includes(text),
  )
})

const filteredModels = computed(() => {
  const text = modelKeyword.value.trim().toLowerCase()
  return models.value.filter((model) => {
    if (providerFilter.value && model.provider_id !== providerFilter.value) return false
    if (!text) return true
    return `${model.code} ${model.model_name} ${providerName(model.provider_id)}`
      .toLowerCase()
      .includes(text)
  })
})

function providerName(providerId: string): string {
  return providerById.value.get(providerId)?.name ?? '未知供应商'
}

function providerProbeMeta(provider: Provider) {
  return verdictMeta(provider.latest_probe?.verdict ?? null)
}

function statusTagType(status: string): 'success' | 'info' {
  return status === 'ACTIVE' ? 'success' : 'info'
}

function templateTimeout(model: Model): number | null {
  const rule = model.param_template?.timeout
  if (!rule || typeof rule !== 'object') return null
  const value = (rule as Record<string, unknown>).default ?? (rule as Record<string, unknown>).max
  return typeof value === 'number' ? value : null
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    const [providerList, modelList] = await Promise.all([listProviders(), listModels()])
    providers.value = providerList
    models.value = modelList
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
  if (!canReadSecrets.value) return
  try {
    secrets.value = (await listSecrets()).items
  } catch {
    // 凭据列表缺失不阻塞主视图；创建供应商时仍可留空凭据引用。
    secrets.value = []
  }
}

// ---------------------------------------------------------------- 供应商

const providerDialogVisible = ref(false)
const editingProvider = ref<Provider | null>(null)
const providerSubmitting = ref(false)
const providerError = ref<string | null>(null)

function openCreateProvider(): void {
  editingProvider.value = null
  providerError.value = null
  providerDialogVisible.value = true
}

function openEditProvider(provider: Provider): void {
  editingProvider.value = provider
  providerError.value = null
  providerDialogVisible.value = true
}

async function submitProvider(payload: ProviderUpdateInput): Promise<void> {
  providerSubmitting.value = true
  providerError.value = null
  try {
    if (editingProvider.value) {
      const updated = await updateProvider(editingProvider.value.id, payload)
      ElMessage.success(`已更新供应商 ${updated.name}`)
    } else {
      const body: ProviderCreateInput = {
        name: payload.name ?? '',
        base_url: payload.base_url ?? '',
        adapter: payload.adapter ?? 'openai_compatible',
        region: payload.region ?? null,
        secret_id: payload.secret_id ?? null,
      }
      const created = await createProvider(body)
      ElMessage.success(`已创建供应商 ${created.name}`)
    }
    providerDialogVisible.value = false
    await load()
  } catch (err) {
    providerError.value = describeError(err)
  } finally {
    providerSubmitting.value = false
  }
}

// ---------------------------------------------------------------- 模型

const modelDialogVisible = ref(false)
const editingModel = ref<Model | null>(null)
const newModelProviderId = ref<string | null>(null)
const modelSubmitting = ref(false)
const modelError = ref<string | null>(null)
const modelParamErrors = ref<Record<string, string>>({})
const togglingModelId = ref<string | null>(null)

function openCreateModel(providerId: string | null = null): void {
  editingModel.value = null
  newModelProviderId.value = providerId ?? providerFilter.value ?? providers.value[0]?.id ?? null
  modelError.value = null
  modelParamErrors.value = {}
  modelDialogVisible.value = true
}

function openEditModel(model: Model): void {
  editingModel.value = model
  newModelProviderId.value = model.provider_id
  modelError.value = null
  modelParamErrors.value = {}
  modelDialogVisible.value = true
}

async function submitModel(payload: ModelPayload): Promise<void> {
  modelSubmitting.value = true
  modelError.value = null
  modelParamErrors.value = {}
  try {
    if (editingModel.value) {
      const body: ModelUpdateInput = {
        model_name: payload.model_name,
        ctx_window: payload.ctx_window,
        modalities: payload.modalities,
        capabilities: payload.capabilities,
        param_template: payload.param_template,
        price: payload.price,
      }
      const updated = await updateModel(editingModel.value.id, body)
      ElMessage.success(`已更新模型 ${updated.code}`)
    } else {
      const created = await createModel({
        provider_id: payload.provider_id,
        code: payload.code,
        model_name: payload.model_name,
        ctx_window: payload.ctx_window,
        modalities: payload.modalities,
        capabilities: payload.capabilities,
        param_template: payload.param_template,
        price: payload.price,
      })
      ElMessage.success(`已创建模型 ${created.code}`)
    }
    modelDialogVisible.value = false
    await load()
  } catch (err) {
    if (err instanceof ApiError && err.code === 'VALIDATION_FAILED') {
      modelParamErrors.value = Object.fromEntries(
        err.details
          .filter((detail) => detail.field)
          .map((detail) => [detail.field as string, detail.reason ?? '不合法']),
      )
    }
    modelError.value = describeError(err)
  } finally {
    modelSubmitting.value = false
  }
}

async function toggleModelStatus(model: Model, active: boolean): Promise<void> {
  const target: ModelStatusValue = active ? 'ACTIVE' : 'DISABLED'
  if (model.status === target) return
  if (!active) {
    const confirmed = await ElMessageBox.confirm(
      '停用只阻止新的模型绑定与发布；已发布版本继续按快照运行，不会因此中断调用。',
      '确认停用模型',
      { confirmButtonText: '停用', cancelButtonText: '取消', type: 'warning' },
    ).catch(() => false)
    if (!confirmed) return
  }
  togglingModelId.value = model.id
  try {
    const updated = await updateModelStatus(model.id, { status: target })
    Object.assign(model, updated)
    ElMessage.success(`${model.code} 已${target === 'ACTIVE' ? '启用' : '停用'}`)
  } catch (err) {
    error.value = describeError(err)
    await load()
  } finally {
    togglingModelId.value = null
  }
}

// ---------------------------------------------------------------- 连通性

const probeVisible = ref(false)
const probeTarget = ref<ProbeTarget | null>(null)
const probeResult = ref<ModelProbe | null>(null)
const probeHistory = ref<ProbeRecord[]>([])
const probing = ref(false)
const probeLoading = ref(false)
const probeError = ref<string | null>(null)

async function openProbe(target: ProbeTarget): Promise<void> {
  probeTarget.value = target
  probeResult.value = null
  probeHistory.value = []
  probeError.value = null
  probeVisible.value = true
  await loadProbeHistory()
}

function openProbeProvider(provider: Provider): void {
  void openProbe({
    kind: 'provider',
    id: provider.id,
    label: provider.name,
    configValue: provider.base_url,
  })
}

function openProbeModel(model: Model): void {
  void openProbe({
    kind: 'model',
    id: model.id,
    label: model.code,
    configValue: model.model_name,
  })
}

async function loadProbeHistory(): Promise<void> {
  const target = probeTarget.value
  if (!target) return
  probeLoading.value = true
  probeError.value = null
  try {
    probeHistory.value =
      target.kind === 'provider'
        ? await listProviderProbes(target.id)
        : await listModelProbes(target.id)
  } catch (err) {
    probeError.value = describeError(err)
  } finally {
    probeLoading.value = false
  }
}

async function runProbe(): Promise<void> {
  const target = probeTarget.value
  if (!target) return
  probing.value = true
  probeError.value = null
  try {
    probeResult.value =
      target.kind === 'provider'
        ? await probeProvider(target.id)
        : await probeModel(target.id)
    probeHistory.value =
      target.kind === 'provider'
        ? await listProviderProbes(target.id)
        : await listModelProbes(target.id)
    providers.value = await listProviders()
    ElMessage.success('探测完成，结论已留痕')
  } catch (err) {
    probeError.value = describeError(err)
  } finally {
    probing.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h2 class="page-title">模型配置</h2>
        <p class="page-hint">
          维护供应商与模型：参数模板决定可传参数，连通性结论会留痕并可回看。
        </p>
      </div>
      <div class="actions">
        <el-button :loading="loading" @click="load">刷新</el-button>
        <template v-if="canManage">
          <el-button @click="openCreateProvider">新建供应商</el-button>
          <el-button type="primary" :disabled="!providers.length" @click="openCreateModel()">
            新建模型
          </el-button>
        </template>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" class="alert">
      <template #default>
        <el-button size="small" @click="load">重试</el-button>
      </template>
    </el-alert>

    <NoticeBanner
      v-if="!canManage"
      tone="info"
      title="当前账号是只读视图"
      description="没有 model:manage 权限，因此不显示新建、编辑、停用与探测入口；数据仍可正常浏览。"
    />

    <el-row :gutter="16">
      <el-col :span="11">
        <el-card shadow="never" class="section">
          <template #header>
            <div class="section-head">
              <span class="section-title">供应商</span>
              <el-input
                v-model="providerKeyword"
                size="small"
                placeholder="按名称 / Base URL 过滤"
                clearable
                class="filter"
              />
            </div>
          </template>

          <el-table v-loading="loading" :data="filteredProviders" row-key="id" size="small">
            <el-table-column label="名称 / Base URL" min-width="200">
              <template #default="{ row }">
                <div class="cell-title">
                  {{ row.name }}
                  <el-tag v-if="row.region" size="small" effect="plain">{{ row.region }}</el-tag>
                </div>
                <div class="cell-sub mono">{{ row.base_url }}</div>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="80">
              <template #default="{ row }">
                <el-tag :type="statusTagType(row.status)" size="small">
                  {{ row.status === 'ACTIVE' ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="最近探测" width="130">
              <template #default="{ row }">
                <template v-if="row.latest_probe">
                  <el-tag :type="providerProbeMeta(row).tag" size="small">
                    {{ providerProbeMeta(row).label }}
                  </el-tag>
                  <div class="cell-sub">{{ formatProbeTime(row.latest_probe.created_at) }}</div>
                </template>
                <span v-else class="muted">未探测</span>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="150" fixed="right">
              <template #default="{ row }">
                <el-button link type="primary" @click="providerFilter = row.id">模型</el-button>
                <el-button v-if="canManage" link type="primary" @click="openProbeProvider(row)">
                  探测
                </el-button>
                <el-button v-if="canManage" link type="primary" @click="openEditProvider(row)">
                  编辑
                </el-button>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                :description="providers.length ? '没有匹配的供应商。' : '还没有供应商。'"
                :image-size="72"
              >
                <el-button
                  v-if="canManage && !providers.length"
                  type="primary"
                  @click="openCreateProvider"
                >
                  新建供应商
                </el-button>
              </el-empty>
            </template>
          </el-table>
        </el-card>
      </el-col>

      <el-col :span="13">
        <el-card shadow="never" class="section">
          <template #header>
            <div class="section-head">
              <span class="section-title">模型</span>
              <div class="section-tools">
                <el-tag v-if="providerFilter" size="small" closable @close="providerFilter = null">
                  只看 {{ providerName(providerFilter) }}
                </el-tag>
                <el-input
                  v-model="modelKeyword"
                  size="small"
                  placeholder="按编码 / 模型标识过滤"
                  clearable
                  class="filter"
                />
                <el-button
                  v-if="canManage"
                  size="small"
                  :disabled="!providers.length"
                  @click="openCreateModel(providerFilter)"
                >
                  新建
                </el-button>
              </div>
            </div>
          </template>

          <el-table v-loading="loading" :data="filteredModels" row-key="id" size="small">
            <el-table-column label="编码 / 模型标识" min-width="190">
              <template #default="{ row }">
                <div class="cell-title mono">{{ row.code }}</div>
                <div class="cell-sub mono">{{ row.model_name }}</div>
              </template>
            </el-table-column>
            <el-table-column label="供应商" width="120" show-overflow-tooltip>
              <template #default="{ row }">{{ providerName(row.provider_id) }}</template>
            </el-table-column>
            <el-table-column label="参数" width="96">
              <template #default="{ row }">
                <el-tag v-if="templateTimeout(row) !== null" size="small" effect="plain">
                  超时 {{ templateTimeout(row) }}s
                </el-tag>
                <span v-else class="muted">默认</span>
              </template>
            </el-table-column>
            <el-table-column label="状态" width="100">
              <template #default="{ row }">
                <el-switch
                  v-if="canManage"
                  :model-value="row.status === 'ACTIVE'"
                  :loading="togglingModelId === row.id"
                  :disabled="togglingModelId === row.id"
                  inline-prompt
                  active-text="启用"
                  inactive-text="停用"
                  @change="(value: boolean) => toggleModelStatus(row, value)"
                />
                <el-tag v-else :type="statusTagType(row.status)" size="small">
                  {{ row.status === 'ACTIVE' ? '启用' : '停用' }}
                </el-tag>
              </template>
            </el-table-column>
            <el-table-column label="操作" width="120" fixed="right">
              <template #default="{ row }">
                <el-button v-if="canManage" link type="primary" @click="openProbeModel(row)">
                  探测
                </el-button>
                <el-button v-if="canManage" link type="primary" @click="openEditModel(row)">
                  编辑
                </el-button>
                <span v-if="!canManage" class="muted">只读</span>
              </template>
            </el-table-column>
            <template #empty>
              <el-empty
                :description="models.length ? '没有匹配的模型。' : '还没有模型。'"
                :image-size="72"
              >
                <el-button v-if="canManage && !models.length" type="primary" @click="openCreateModel()">
                  新建模型
                </el-button>
              </el-empty>
            </template>
          </el-table>
        </el-card>
      </el-col>
    </el-row>

    <el-dialog
      v-model="providerDialogVisible"
      :title="editingProvider ? '编辑供应商' : '新建供应商'"
      width="560px"
      destroy-on-close
    >
      <ProviderForm
        :key="editingProvider?.id ?? 'create-provider'"
        :provider="editingProvider"
        :secrets="secrets"
        :submitting="providerSubmitting"
        :error="providerError"
        @submit="submitProvider"
        @cancel="providerDialogVisible = false"
      />
    </el-dialog>

    <el-dialog
      v-model="modelDialogVisible"
      :title="editingModel ? `编辑模型 ${editingModel.code}` : '新建模型'"
      width="880px"
      top="5vh"
      destroy-on-close
    >
      <ModelForm
        :key="editingModel?.id ?? 'create-model'"
        :model="editingModel"
        :providers="providers"
        :default-provider-id="newModelProviderId"
        :submitting="modelSubmitting"
        :error="modelError"
        :field-errors="modelParamErrors"
        @submit="submitModel"
        @cancel="modelDialogVisible = false"
      />
    </el-dialog>

    <el-drawer
      v-model="probeVisible"
      :title="probeTarget ? `连通性 · ${probeTarget.label}` : '连通性'"
      size="620px"
      destroy-on-close
    >
      <el-alert
        v-if="probeError"
        :title="probeError"
        type="error"
        show-icon
        :closable="false"
        class="alert"
      />
      <div v-loading="probeLoading">
        <ConnectivityPanel
          v-if="probeTarget"
          :label="probeTarget.label"
          :config-value="probeTarget.configValue"
          :result="probeResult"
          :history="probeHistory"
          :probing="probing"
          :can-probe="canManage"
          @probe="runProbe"
        />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-head .page-title {
  margin: 0;
}

.page-hint {
  margin: 4px 0 0;
  font-size: 12px;
  color: #8a93a5;
}

.actions {
  display: flex;
  gap: 8px;
}

.alert {
  margin-bottom: 12px;
}

.section {
  height: 100%;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.section-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}

.section-title {
  font-size: 14px;
  font-weight: 600;
  color: #1f2430;
}

.filter {
  width: 210px;
}

.cell-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 500;
}

.cell-sub {
  font-size: 11px;
  color: #8a93a5;
  word-break: break-all;
}

.mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.muted {
  font-size: 12px;
  color: #9aa3b5;
}
</style>
