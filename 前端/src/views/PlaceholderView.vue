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
    <p class="desc">路由已就绪，业务页面将在后续任务中实现。</p>
    <el-button type="primary" @click="$router.push('/projects')">返回项目入口</el-button>
  </section>
</template>

<style scoped>
.placeholder {
  width: min(100%, 42rem);
  margin: 0 auto;
  padding: var(--pa-space-card);
  background: var(--pa-bg-surface);
  border: var(--pa-border);
  border-radius: var(--pa-radius-card);
  box-shadow: var(--pa-shadow-card);
}

.eyebrow {
  margin: 0;
  font-family: var(--pa-font-mono);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.12em;
  color: var(--el-color-primary);
}

h1 {
  font-size: 20px;
  font-weight: 600;
  color: var(--pa-color-heading);
  margin: 6px 0 8px;
}

.desc {
  margin: 0 0 16px;
  color: var(--pa-text-meta);
  font-size: 13px;
  line-height: 1.65;
}
</style>
