<script setup lang="ts">
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { ApiError, describeError } from '@/api/client'
import { createPrompt, listPrompts, type Prompt } from '@/api/endpoints'
import { useSessionStore } from '@/stores/session'
import { PROMPT_CODE_PATTERN } from '@/utils/prompt'

const router = useRouter()
const session = useSessionStore()

const prompts = ref<Prompt[]>([])
const loading = ref(false)
const error = ref<string | null>(null)
const keyword = ref('')

const dialogVisible = ref(false)
const creating = ref(false)
const createError = ref<string | null>(null)
const codeError = ref('')
const formRef = ref<FormInstance>()
const form = ref({ code: '', name: '', description: '' })

const canManage = computed(() => session.can('prompt:manage'))

const filtered = computed(() => {
  const text = keyword.value.trim().toLowerCase()
  if (!text) return prompts.value
  return prompts.value.filter((prompt) =>
    `${prompt.code} ${prompt.name}`.toLowerCase().includes(text),
  )
})

const rules: FormRules = {
  code: [
    { required: true, message: '请输入编码', trigger: 'blur' },
    {
      pattern: PROMPT_CODE_PATTERN,
      message: '1~64 位，仅允许字母、数字与 _ . -',
      trigger: 'blur',
    },
  ],
  name: [{ required: true, message: '请输入名称', trigger: 'blur' }],
}

function statusType(status: Prompt['status']): 'success' | 'info' {
  return status === 'ACTIVE' ? 'success' : 'info'
}

async function load(): Promise<void> {
  loading.value = true
  error.value = null
  try {
    prompts.value = await listPrompts()
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}

function openCreate(): void {
  form.value = { code: '', name: '', description: '' }
  codeError.value = ''
  createError.value = null
  dialogVisible.value = true
}

async function submit(): Promise<void> {
  if (!formRef.value) return
  codeError.value = ''
  createError.value = null
  const valid = await formRef.value.validate().catch(() => false)
  if (!valid) return

  creating.value = true
  try {
    const created = await createPrompt({
      code: form.value.code.trim(),
      name: form.value.name.trim(),
      description: form.value.description.trim() || null,
    })
    dialogVisible.value = false
    ElMessage.success(`已创建 Prompt ${created.code}`)
    await router.push(`/prompts/${created.id}`)
  } catch (err) {
    if (err instanceof ApiError) {
      if (err.code === 'CONFLICT') {
        // 后端 CONFLICT 不带 details，只能按语义定位到 code 字段。
        codeError.value = '该编码在本租户已存在，请换一个'
        return
      }
      if (err.code === 'VALIDATION_FAILED') {
        const codeDetail = err.details.find((detail) => detail.field === 'code')
        if (codeDetail) {
          codeError.value = codeDetail.reason ?? '编码不合法'
          return
        }
      }
    }
    createError.value = describeError(err)
  } finally {
    creating.value = false
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <h2 class="page-title">Prompt 管理</h2>
      <div class="actions">
        <el-input
          v-model="keyword"
          placeholder="按编码 / 名称过滤"
          clearable
          class="search"
        />
        <el-button :loading="loading" @click="load">刷新</el-button>
        <el-button v-if="canManage" type="primary" @click="openCreate">新建 Prompt</el-button>
      </div>
    </div>

    <el-alert v-if="error" :title="error" type="error" show-icon :closable="false" />

    <el-table v-loading="loading" :data="filtered" stripe row-key="id">
      <el-table-column prop="code" label="编码" width="200" />
      <el-table-column prop="name" label="名称" min-width="160" />
      <el-table-column prop="description" label="描述" min-width="220" show-overflow-tooltip />
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag :type="statusType(row.status)" size="small">{{ row.status }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="router.push(`/prompts/${row.id}`)">
            模板与版本
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty
      v-if="!loading && !filtered.length"
      :description="prompts.length ? '没有匹配的 Prompt。' : '还没有任何 Prompt。'"
    >
      <el-button v-if="canManage && !prompts.length" type="primary" @click="openCreate">
        新建 Prompt
      </el-button>
    </el-empty>

    <el-dialog v-model="dialogVisible" title="新建 Prompt" width="480px">
      <el-alert
        v-if="createError"
        :title="createError"
        type="error"
        show-icon
        :closable="false"
        class="dialog-error"
      />
      <el-form ref="formRef" :model="form" :rules="rules" label-width="72px">
        <el-form-item label="编码" prop="code" :error="codeError || undefined">
          <el-input
            v-model="form.code"
            placeholder="如 support_reply"
            maxlength="64"
            @input="codeError = ''"
          />
        </el-form-item>
        <el-form-item label="名称" prop="name">
          <el-input v-model="form.name" placeholder="如 客服回复模板" maxlength="100" />
        </el-form-item>
        <el-form-item label="描述" prop="description">
          <el-input
            v-model="form.description"
            type="textarea"
            :autosize="{ minRows: 2, maxRows: 4 }"
            placeholder="可选"
          />
        </el-form-item>
      </el-form>
      <p class="page-hint">创建后进入详情页填写模板与变量，保存时才会生成 v1。</p>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="submit">创建</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.page-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}

.page-head .page-title {
  margin: 0;
}

.actions {
  display: flex;
  gap: 8px;
}

.search {
  width: 240px;
}

.dialog-error {
  margin-bottom: 12px;
}
</style>
