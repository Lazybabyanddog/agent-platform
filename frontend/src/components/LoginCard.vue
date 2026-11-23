<script setup lang="ts">
import { reactive, ref } from 'vue'

import { describeError } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()

const form = reactive({ tenantCode: '', email: '', password: '' })
const loading = ref(false)
const error = ref<string | null>(null)

async function submit(): Promise<void> {
  error.value = null
  loading.value = true
  try {
    await session.signIn({
      tenant_code: form.tenantCode.trim(),
      email: form.email.trim(),
      password: form.password,
    })
  } catch (err) {
    error.value = describeError(err)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card" shadow="never">
      <div class="login-brand">
        <span class="mark">AP</span>
        <div>
          <div class="title">Agent 中台</div>
          <div class="sub">登录后进入控制台</div>
        </div>
      </div>

      <el-alert
        v-if="error"
        :title="error"
        type="error"
        show-icon
        :closable="false"
        class="login-error"
      />

      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="租户编码">
          <el-input v-model="form.tenantCode" placeholder="例如 default" autocomplete="organization" />
        </el-form-item>
        <el-form-item label="邮箱">
          <el-input v-model="form.email" placeholder="admin@example.com" autocomplete="username" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            @keyup.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" class="login-submit" :loading="loading" @click="submit">
          登录
        </el-button>
      </el-form>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  display: grid;
  place-items: center;
  min-height: 100%;
  padding: 48px 16px;
}

.login-card {
  width: 360px;
  border-radius: 12px;
}

.login-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 20px;
}

.mark {
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  background: linear-gradient(135deg, #4f7cff, #7a5cff);
  color: #fff;
  font-weight: 700;
}

.title {
  font-size: 16px;
  font-weight: 600;
}

.sub {
  font-size: 12px;
  color: #7b8291;
}

.login-error {
  margin-bottom: 12px;
}

.login-submit {
  width: 100%;
}
</style>
