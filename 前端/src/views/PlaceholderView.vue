<script setup lang="ts">
import { useRoute } from 'vue-router'
import { ElButton } from 'element-plus'
import 'element-plus/es/components/button/style/css'

const route = useRoute()
</script>

<template>
  <section class="placeholder">
    <p class="eyebrow">PAPERAGENT</p>
    <h1>{{ route.meta.title }}</h1>
    <p>路由已就绪，业务页面将在后续任务中实现。</p>
    <el-button type="primary" @click="$router.push('/projects')">返回项目入口</el-button>
  </section>
</template>

<style scoped>
.placeholder { width: min(100%, 42rem); margin: 0 auto; padding: 4rem 2rem; }
.eyebrow { font-size: 0.8rem; font-weight: 700; letter-spacing: 0.12em; color: #3b657a; }
h1 { font-size: 2rem; margin: 0.5rem 0 1rem; }
p { line-height: 1.6; }
</style>
