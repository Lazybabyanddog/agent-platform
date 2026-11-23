<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { computed, ref, watch } from 'vue'

import type { Model, Provider } from '@/api/endpoints'
import NoticeBanner from '@/components/common/NoticeBanner.vue'
import ParamTemplateEditor from '@/components/model/ParamTemplateEditor.vue'

interface ModelFormPayload {
  code: string
  provider_id: string
  model_name: string
  ctx_window: number | null
  modalities: string[]
  capabilities: Record<string, unknown>
  param_template: Record<string, unknown>
  price: Record<string, unknown>
}

const props = withDefaults(
  defineProps<{
    /** 有值即编辑态：code 与所属供应商只读。 */
    model: Model | null
    providers: Provider[]
    /** 新建时预选的供应商（从供应商分区进入）。 */
    defaultProviderId?: string | null
    submitting?: boolean
    error?: string | null
    /** 后端字段级错误，形如 `param_template.temperature`。 */
    fieldErrors?: Record<string, string>
  }>(),
  { defaultProviderId: null, submitting: false, error: null, fieldErrors: () => ({}) },
)

const emit = defineEmits<{ submit: [payload: ModelFormPayload]; cancel: [] }>()

const formRef = ref<FormInstance>()
const templateEditor = ref<InstanceType<typeof ParamTemplateEditor>>()
const paramTemplate = ref<Record<string, unknown>>({})
const paramErrors = ref<Record<string, string>>({})

const isEdit = computed(() => props.model !== null)

const form = ref({
  code: '',
  provider_id: '',
  model_name: '',
  ctx_window: '',
  modalities: ['text'] as string[],
  capabilitiesText: '{}',
  priceText: '{}',
})

const jsonError = ref<{ capabilities?: string; price?: string }>({})

const rules: FormRules = {
  code: [
    { required: true, message: '请输入编码', trigger: 'blur' },
    {
      pattern: /^[A-Za-z0-9_.-]{1,64}$/,
      message: '1~64 位，仅允许字母、数字与 _ . -',
      trigger: 'blur',
    },
  ],
  provider_id: [{ required: true, message: '请选择所属供应商', trigger: 'change' }],
  model_name: [{ required: true, message: '请输入上游模型标识', trigger: 'blur' }],
  ctx_window: [
    {
      pattern: /^\d*$/,
      message: '请输入正整数（留空表示未声明）',
      trigger: 'blur',
    },
  ],
}

const modelNameChanged = computed(
  () => isEdit.value && form.value.model_name.trim() !== (props.model?.model_name ?? ''),
)

watch(
  () => props.model,
  (model) => {
    form.value = {
      code: model?.code ?? '',
      provider_id: model?.provider_id ?? props.defaultProviderId ?? '',
      model_name: model?.model_name ?? '',
      ctx_window: model?.ctx_window === null || model?.ctx_window === undefined ? '' : String(model.ctx_window),
      modalities: model?.modalities?.length ? [...model.modalities] : ['text'],
      capabilitiesText: JSON.stringify(model?.capabilities ?? {}, null, 2),
      priceText: JSON.stringify(model?.price ?? {}, null, 2),
    }
    paramTemplate.value = JSON.parse(JSON.stringify(model?.param_template ?? {})) as Record<string, unknown>
    paramErrors.value = {}
    jsonError.value = {}
  },
  { immediate: true },
)

watch(
  () => props.fieldErrors,
  (errors) => {
    const templateErrors: Record<string, string> = {}
    for (const [field, reason] of Object.entries(errors)) {
      if (field.startsWith('param_template')) templateErrors[field] = reason
    }
    if (Object.keys(templateErrors).length) {
      paramErrors.value = { ...paramErrors.value, ...templateErrors }
    }
  },
  { immediate: true },
)

function parseJson(text: string): Record<string, unknown> | null {
  if (!text.trim()) return {}
  try {
    const parsed = JSON.parse(text) as unknown
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null
    return parsed as Record<string, unknown>
  } catch {
    return null
  }
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  jsonError.value = {}
  const capabilities = parseJson(form.value.capabilitiesText)
  if (capabilities === null) jsonError.value.capabilities = '必须是 JSON 对象'
  const price = parseJson(form.value.priceText)
  if (price === null) jsonError.value.price = '必须是 JSON 对象'
  if (Object.keys(jsonError.value).length) return

  paramErrors.value = templateEditor.value?.validate() ?? {}
  if (Object.keys(paramErrors.value).length) {
    ElMessage.warning('参数模板还有未修正的问题，请按行内提示调整')
    return
  }

  const ctxWindow = form.value.ctx_window.trim()
  emit('submit', {
    code: form.value.code.trim(),
    provider_id: form.value.provider_id,
    model_name: form.value.model_name.trim(),
    ctx_window: ctxWindow === '' ? null : Number(ctxWindow),
    modalities: form.value.modalities.map((item) => item.trim()).filter(Boolean),
    capabilities: capabilities ?? {},
    param_template: paramTemplate.value,
    price: price ?? {},
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
      v-if="modelNameChanged"
      tone="warning"
      title="修改上游模型标识只影响之后生成的 Agent 版本"
      description="已发布的版本按快照继续运行，不会因为这次修改而切换上游模型。"
    />

    <el-form ref="formRef" :model="form" :rules="rules" label-width="112px">
      <el-row :gutter="12">
        <el-col :span="12">
          <el-form-item label="编码" prop="code">
            <el-input
              v-model="form.code"
              :disabled="isEdit"
              maxlength="64"
              placeholder="如 gpt-4o-mini"
            />
            <p v-if="isEdit" class="field-hint">
              编码被已发布版本引用，不可修改；需更换编码时请新建模型。
            </p>
          </el-form-item>
        </el-col>
        <el-col :span="12">
          <el-form-item label="所属供应商" prop="provider_id">
            <el-select v-model="form.provider_id" :disabled="isEdit" class="full" placeholder="选择供应商">
              <el-option
                v-for="provider in providers"
                :key="provider.id"
                :label="provider.name"
                :value="provider.id"
              />
            </el-select>
            <p v-if="isEdit" class="field-hint">
              所属供应商不可修改；需更换供应商时请新建模型，避免已发布版本的上游被替换。
            </p>
          </el-form-item>
        </el-col>
      </el-row>

      <el-row :gutter="12">
        <el-col :span="12">
          <el-form-item label="上游模型标识" prop="model_name">
            <el-input
              v-model="form.model_name"
              maxlength="160"
              placeholder="如 gpt-4o-mini"
            />
          </el-form-item>
        </el-col>
        <el-col :span="6">
          <el-form-item label="上下文窗口">
            <el-input v-model="form.ctx_window" placeholder="tokens，可选" />
          </el-form-item>
        </el-col>
        <el-col :span="6">
          <el-form-item label="模态">
            <el-select
              v-model="form.modalities"
              multiple
              filterable
              allow-create
              default-first-option
              class="full"
              placeholder="text / vision"
            >
              <el-option label="text" value="text" />
              <el-option label="vision" value="vision" />
              <el-option label="audio" value="audio" />
            </el-select>
          </el-form-item>
        </el-col>
      </el-row>
    </el-form>

    <div class="section-title">参数模板</div>
    <ParamTemplateEditor
      ref="templateEditor"
      v-model="paramTemplate"
      :errors="paramErrors"
    />

    <el-collapse class="json-section">
      <el-collapse-item name="json" title="能力声明与价格（JSON，可选）">
        <el-row :gutter="12">
          <el-col :span="12">
            <div class="json-label">capabilities</div>
            <el-input
              v-model="form.capabilitiesText"
              type="textarea"
              :autosize="{ minRows: 4, maxRows: 10 }"
            />
            <p v-if="jsonError.capabilities" class="json-error">{{ jsonError.capabilities }}</p>
          </el-col>
          <el-col :span="12">
            <div class="json-label">price</div>
            <el-input
              v-model="form.priceText"
              type="textarea"
              :autosize="{ minRows: 4, maxRows: 10 }"
            />
            <p v-if="jsonError.price" class="json-error">{{ jsonError.price }}</p>
          </el-col>
        </el-row>
      </el-collapse-item>
    </el-collapse>

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

.field-hint {
  margin: 2px 0 0;
  font-size: 11px;
  color: #8a93a5;
  line-height: 1.4;
}

.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #3d4655;
  margin: 8px 0;
}

.json-section {
  margin-top: 12px;
  border-top: none;
}

.json-label {
  font-size: 12px;
  color: #5a6478;
  margin-bottom: 4px;
}

.json-error {
  margin: 2px 0 0;
  font-size: 12px;
  color: #d64545;
}

.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 12px;
}
</style>
