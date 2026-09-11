<template>
  <p v-if="error" role="alert">{{ error }}</p>
  <DemoWorkspace :base-url="baseUrl" :engine="engine" />
</template>

<script setup>
import { onMounted, ref } from 'vue'
import DemoWorkspace from './DemoWorkspace.vue'

const baseUrl = (import.meta.env.VITE_PYTHON_API_URL || '/api/python').replace(/\/+$/, '')
const engine = ref('')
const error = ref('')
onMounted(async () => {
  try {
    const response = await fetch(`${baseUrl}/health`)
    if (!response.ok) throw new Error(`HTTP ${response.status}`)
    engine.value = (await response.json()).engine
  } catch {
    error.value = '无法连接服务，请检查后端是否启动。'
  }
})
</script>
