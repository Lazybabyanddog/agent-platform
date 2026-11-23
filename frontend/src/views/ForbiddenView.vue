<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()
</script>

<template>
  <el-result icon="warning" title="无权访问" sub-title="当前账号缺少访问该页面所需的权限，请联系管理员。">
    <template #extra>
      <el-button type="primary" @click="router.push('/agents')">返回 Agent 列表</el-button>
      <el-button @click="router.back()">返回上一页</el-button>
    </template>
  </el-result>
</template>
