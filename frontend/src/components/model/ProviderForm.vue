<script setup lang="ts">
import { ElMessageBox, type FormInstance, type FormRules } from 'element-plus'
import { computed, ref, watch } from 'vue'

import type { Provider, ProviderUpdateInput, Secret } from '@/api/endpoints'
import NoticeBanner from '@/components/common/NoticeBanner.vue'

const props = withDefaults(
  defineProps<{
    /** 有值即编辑态；为空表示新建供应商。 */
    provider: Provider | null
    secrets: Secret[]
    submitting?: boolean
    error?: string | null
  }>(),
  { submitting: false, error: null },
)

const emit = defineEmits<{
  submit: [payload: ProviderUpdateInput]
  cancel: []
}>()

const formRef = ref<FormInstance>()
const form = ref({
  name: '',
  base_url: '',
  adapter: 'openai_compatible',
  region: '',
  secret_id: '',
  status: 'ACTIVE' as 'ACTIVE' | 'DISABLED',
})

const isEdit = computed(() => props.provider !== null)

const rules: FormRules = {
  name: [{ required: true, message: '请输入供应商名称', trigger: 'blur' }],
  base_url: [
    { required: true, message: '请输入 Base URL', trigger: 'blur' },
    {
      pattern: /^https?:\/\/.+/,
      message: '以 http:// 或 https:// 开头',
      trigger: 'blur',
    },
  ],
}

const impact = computed(() => {
  if (!props.provider) return null
  const baseUrlChanged = form.value.base_url.trim() !== props.provider.base_url
  const secretChanged = form.value.secret_id !== (props.provider.secret_ref_id ?? '')
  return baseUrlChanged || secretChanged
    ? 'Base URL 与凭据引用在调用时活取：保存后，引用此供应商的全部已发布 Agent 版本会立即使用新配置。'
    : null
})

const disabling = computed(
  () => isEdit.value && props.provider?.status === 'ACTIVE' && form.value.status === 'DISABLED',
)

watch(
  () => props.provider,
  (provider) => {
    form.value = {
      name: provider?.name ?? '',
      base_url: provider?.base_url ?? '',
      adapter: provider?.adapter ?? 'openai_compatible',
      region: provider?.region ?? '',
      secret_id: provider?.secret_ref_id ?? '',
      status: provider?.status === 'DISABLED' ? 'DISABLED' : 'ACTIVE',
    }
  },
  { immediate: true },
)

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  if (impact.value) {
    const confirmed = await ElMessageBox.confirm(impact.value, '确认变更影响范围', {
      confirmButtonText: '确认变更',
      cancelButtonText: '取消',
      type: 'warning',
    }).catch(() => false)
    if (!confirmed) return
  }

  emit('submit', {
    name: form.value.name.trim(),
    base_url: form.value.base_url.trim(),
    adapter: form.value.adapter.trim() || 'openai_compatible',
    region: form.value.region.trim() || null,
    secret_id: form.value.secret_id || null,
    ...(isEdit.value ? { status: form.value.status } : {}),
  })
}
</script>

<template>
  <div>
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="form-error"
    />
    <NoticeBanner
      v-if="impact"
      tone="warning"
      title="这次变更会立即影响已发布版本"
      :description="impact"
    />
    <NoticeBanner
      v-if="disabling"
      tone="warning"
      title="停用只阻止新的模型绑定与发布"
      description="已发布版本继续按快照运行，不会因此中断调用；可用性变化只影响之后生成的 Agent 版本。"
    />

    <el-form ref="formRef" :model="form" :rules="rules" label-width="96px">
      <el-form-item label="名称" prop="name">
        <el-input v-model="form.name" maxlength="120" placeholder="如 OpenAI 主账号" />
      </el-form-item>
      <el-form-item label="Base URL" prop="base_url">
        <el-input v-model="form.base_url" maxlength="512" placeholder="https://api.example.com/v1" />
      </el-form-item>
      <el-form-item label="适配器" prop="adapter">
        <el-input v-model="form.adapter" maxlength="64" placeholder="openai_compatible" />
      </el-form-item>
      <el-form-item label="区域" prop="region">
        <el-input v-model="form.region" maxlength="32" placeholder="可选，如 cn-north / us-east" />
      </el-form-item>
      <el-form-item label="凭据" prop="secret_id">
        <el-select v-model="form.secret_id" clearable placeholder="不绑定凭据" class="full">
          <el-option
            v-for="secret in secrets"
            :key="secret.id"
            :label="secret.name"
            :value="secret.id"
          />
        </el-select>
      </el-form-item>
      <el-form-item v-if="isEdit" label="状态" prop="status">
        <el-select v-model="form.status" class="full">
          <el-option label="启用（ACTIVE）" value="ACTIVE" />
          <el-option label="停用（DISABLED）" value="DISABLED" />
        </el-select>
      </el-form-item>
    </el-form>
    <p v-if="!secrets.length" class="page-hint">
      没有可绑定的凭据，或当前账号无凭据读取权限；可先请具备 `secret:manage` 的同事创建凭据。
    </p>

    <div class="form-actions">
      <el-button @click="emit('cancel')">取消</el-button>
      <el-button type="primary" :loading="submitting" @click="submit">保存</el-button>
    </div>
  </div>
</template>

<style scoped>
.form-error {
  margin-bottom: 12px;
}

.full {
  width: 100%;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 8px;
}
</style>
