<script setup lang="ts">
import { computed, onMounted, onUnmounted } from 'vue'
import { ElButton, ElTable, ElTableColumn } from 'element-plus'
import { ArrowLeft, ArrowRight, Document, Plus, Refresh, WarningFilled } from '@element-plus/icons-vue'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/table/style/css'
import 'element-plus/es/components/table-column/style/css'
import { useProjectsStore } from '../stores/projects'
import type { ProjectFilter } from '../types/projects'
import { formatLocalTime, lifecycleLabel, paperTypeLabel, progressLabel } from '../utils/projectDisplay'

const store = useProjectsStore()
const filters: { label: string; value: ProjectFilter }[] = [
  { label: '全部项目', value: undefined },
  { label: '进行中', value: 'active' },
  { label: '已归档', value: 'archived' },
]
const pages = computed(() => Math.max(1, Math.ceil(store.total / store.pageSize)))
const errorTitle = computed(() => store.error?.status === 503 ? '服务暂时不可用' : '无法加载项目')
const errorDescription = computed(() => store.error?.status === 503
  ? '后端服务暂时无法处理请求，请稍后重试。'
  : '请检查网络连接或后端服务后重试。')
onMounted(() => { void store.load() })
onUnmounted(() => { store.cancel() })
</script>

<template>
  <section class="project-page" aria-labelledby="page-title">
    <header class="page-intro">
      <div class="intro-main">
        <h1 id="page-title">我的项目</h1>
        <p class="subtitle">在这里查看和管理你的论文项目。</p>
      </div>
      <div class="primary-action">
        <ElButton type="primary" :icon="Plus" @click="$router.push('/projects/new')">新建项目</ElButton>
      </div>
    </header>

    <div class="panel">
      <div class="toolbar">
        <div class="filters" role="group" aria-label="按项目状态筛选">
          <button v-for="filter in filters" :key="filter.label" type="button" :class="{ selected: store.lifecycleStatus === filter.value }" :aria-pressed="store.lifecycleStatus === filter.value" @click="store.setFilter(filter.value)">{{ filter.label }}</button>
        </div>
        <div class="toolbar-right">
          <span v-if="!store.loading && !store.error" class="result-count">共 <strong class="mono">{{ store.total }}</strong> 个项目</span>
          <ElButton :icon="Refresh" :disabled="store.loading" aria-label="刷新项目列表" @click="store.load()">刷新</ElButton>
        </div>
      </div>

      <div v-if="store.loading" class="loading-state" role="status" aria-live="polite">
        <div class="loading-line long"></div><div class="loading-line"></div><div class="loading-line short"></div>
        <span>正在加载项目…</span>
      </div>
      <div v-else-if="store.error" class="state-box" role="alert">
        <div class="state-icon warning"><WarningFilled aria-hidden="true" /></div>
        <h3>{{ errorTitle }}</h3><p>{{ errorDescription }}</p>
        <ElButton :icon="Refresh" @click="store.load()">重试</ElButton>
      </div>
      <div v-else-if="store.loaded && store.projects.length === 0" class="state-box">
        <div class="state-icon"><Document aria-hidden="true" /></div>
        <h3>{{ store.lifecycleStatus ? '当前状态下暂无项目' : '还没有论文项目' }}</h3>
        <p>{{ store.lifecycleStatus ? '可以切换其他状态查看项目。' : '创建你的第一个项目，开始管理论文研究。' }}</p>
        <button v-if="store.lifecycleStatus" class="text-action" type="button" @click="store.setFilter(undefined)">查看全部项目 <ArrowRight aria-hidden="true" /></button>
      </div>
      <div v-else-if="store.loaded" class="table-wrap">
        <ElTable :data="store.projects" row-key="id" aria-label="论文项目列表" style="width: 100%">
          <ElTableColumn prop="title" label="项目名称" min-width="200" show-overflow-tooltip>
            <template #default="scope"><RouterLink class="project-name" :to="{ name: 'project-detail', params: { projectId: scope.row.id } }"><span class="doc-icon"><Document aria-hidden="true" /></span><span>{{ scope.row.title }}</span></RouterLink></template>
          </ElTableColumn>
          <ElTableColumn prop="topic" label="研究主题" min-width="240" show-overflow-tooltip>
            <template #default="scope"><span class="topic-cell">{{ scope.row.topic }}</span></template>
          </ElTableColumn>
          <ElTableColumn label="论文类型" min-width="120"><template #default="scope"><span class="type-badge">{{ paperTypeLabel(scope.row.paper_type) }}</span></template></ElTableColumn>
          <ElTableColumn label="项目状态" min-width="115"><template #default="scope"><span class="status" :class="scope.row.lifecycle_status === 'active' ? 'status-active' : 'status-neutral'"><span class="status-dot"></span>{{ lifecycleLabel(scope.row.lifecycle_status) }}</span></template></ElTableColumn>
          <ElTableColumn label="项目进度" min-width="110"><template #default="scope"><span class="progress-cell">{{ progressLabel(scope.row.progress_status) }}</span></template></ElTableColumn>
          <ElTableColumn label="更新时间（本地时间）" min-width="175"><template #default="scope"><span class="mono time-cell">{{ formatLocalTime(scope.row.updated_at) }}</span></template></ElTableColumn>
        </ElTable>
      </div>
      <div v-if="!store.loading && !store.error && store.loaded && store.total > 0" class="pagination" aria-label="项目列表分页">
        <span class="mono">第 {{ store.page }} / {{ pages }} 页</span>
        <div class="page-actions">
          <ElButton :icon="ArrowLeft" :disabled="store.page <= 1" @click="store.setPage(store.page - 1)">上一页</ElButton>
          <ElButton :disabled="store.page >= pages" @click="store.setPage(store.page + 1)">下一页 <ArrowRight class="button-icon" aria-hidden="true" /></ElButton>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.project-page {
  max-width: 1320px;
  margin: 0 auto;
  color: var(--pa-text-body);
}

.mono {
  font-family: var(--pa-font-mono);
  font-variant-numeric: tabular-nums;
}

/* ---------- 紧凑现代页面顶栏（无冗余大白盒） ---------- */
.page-intro {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}

.intro-main {
  min-width: 0;
}

h1 {
  margin: 0;
  color: var(--pa-color-heading);
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
  letter-spacing: -0.02em;
}

.subtitle {
  margin: 4px 0 0;
  color: var(--pa-text-meta);
  font-size: 13px;
  line-height: 1.5;
}

.primary-action {
  flex: none;
}

/* ---------- 主工作台容器 ---------- */
.panel {
  background: var(--pa-bg-surface);
  border: var(--pa-border);
  border-radius: var(--pa-radius-card);
  box-shadow: var(--pa-shadow-card);
  overflow: hidden;
}

/* ---------- 整合筛选与刷新的紧凑工具栏 ---------- */
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 12px 20px;
  border-bottom: var(--pa-border);
  background: var(--pa-bg-surface);
  flex-wrap: wrap;
}

.filters {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  background: var(--pa-bg-hover);
  border-radius: var(--pa-radius-card);
}

.filters button {
  padding: 5px 12px;
  border: 0;
  border-radius: var(--pa-radius-control);
  background: transparent;
  color: var(--pa-text-meta);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
  transition: background-color 0.15s, color 0.15s, box-shadow 0.15s;
}

.filters button:hover {
  color: var(--pa-color-heading);
}

.filters button.selected {
  background: var(--pa-bg-surface);
  color: var(--pa-color-heading);
  font-weight: 600;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.06), 0 0 0 1px var(--pa-border-color);
}

.filters button:focus-visible {
  outline: none;
  box-shadow: var(--pa-ring-primary);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-left: auto;
}

.result-count {
  color: var(--pa-text-meta);
  font-size: 12px;
  white-space: nowrap;
}

.result-count strong {
  color: var(--pa-color-heading);
  font-weight: 600;
}

/* ---------- 空状态 / 错误态 / 加载态 ---------- */
.state-box {
  min-height: 260px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 20px;
  text-align: center;
}

.state-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border: var(--pa-border);
  border-radius: 10px;
  background: var(--pa-bg-inset);
  color: var(--pa-text-meta);
}

.state-icon svg {
  width: 20px;
  height: 20px;
}

.state-icon.warning {
  color: var(--pa-color-amber-strong);
  background: var(--pa-bg-amber);
  border-color: var(--pa-border-amber);
}

h3 {
  margin: 14px 0 0;
  color: var(--pa-color-heading);
  font-size: 15px;
  font-weight: 600;
}

.state-box p {
  max-width: 400px;
  margin: 6px 0 16px;
  color: var(--pa-text-meta);
  font-size: 13px;
  line-height: 1.6;
}

.text-action {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 6px 10px;
  border: 0;
  border-radius: var(--pa-radius-control);
  background: transparent;
  color: var(--el-color-primary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s;
}

.text-action:hover {
  background: var(--el-color-primary-light-9);
}

.text-action svg {
  width: 13px;
  height: 13px;
}

.loading-state {
  min-height: 240px;
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 12px;
  padding: 36px 24px;
  color: var(--pa-text-meta);
  font-size: 12px;
}

.loading-line {
  height: 12px;
  width: 68%;
  border-radius: 4px;
  background: var(--pa-bg-hover);
}

.loading-line.long { width: 88%; }
.loading-line.short { width: 48%; }

/* ---------- Notion / Linear 风格数据表 ---------- */
.table-wrap {
  overflow-x: auto;
}

.table-wrap :deep(.el-table) {
  --el-table-border-color: var(--pa-border-color);
  --el-table-header-bg-color: var(--pa-bg-inset);
  --el-table-header-text-color: var(--pa-text-meta);
  --el-table-row-hover-bg-color: var(--pa-bg-inset);
  font-size: 13px;
  color: var(--pa-text-body);
}

.table-wrap :deep(.el-table th.el-table__cell) {
  font-size: 12px;
  font-weight: 600;
  padding: 10px 0;
  background: var(--pa-bg-inset);
}

.table-wrap :deep(.el-table td.el-table__cell) {
  padding: 13px 0;
}

.table-wrap :deep(.el-table .cell) {
  padding: 0 16px;
}

.table-wrap :deep(.el-table__inner-wrapper::before) {
  display: none;
}

.project-name {
  text-decoration: none;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-weight: 600;
  color: var(--pa-color-heading);
  transition: color 0.15s;
}

.project-name:hover {
  color: var(--el-color-primary);
}

.doc-icon {
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-radius: 6px;
  background: var(--pa-bg-inset);
  border: var(--pa-border);
  color: var(--pa-text-meta);
  flex: none;
}

.project-name:hover .doc-icon {
  border-color: var(--el-color-primary-light-7);
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.doc-icon svg {
  width: 13px;
  height: 13px;
}

.topic-cell {
  color: var(--pa-text-body);
}

.type-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--pa-bg-inset);
  border: var(--pa-border);
  color: var(--pa-text-meta);
  font-size: 12px;
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 9px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.status-active {
  color: var(--pa-color-success);
  background: var(--pa-bg-success);
  border: 1px solid var(--pa-border-success);
}

.status-neutral {
  color: var(--pa-text-meta);
  background: var(--pa-bg-hover);
  border: var(--pa-border);
}

.status-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
}

.progress-cell {
  color: var(--pa-text-body);
  font-size: 13px;
}

.time-cell {
  color: var(--pa-text-meta);
  font-size: 12px;
}

/* ---------- 分页与按钮 ---------- */
.pagination {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  border-top: var(--pa-border);
  background: var(--pa-bg-inset);
  color: var(--pa-text-meta);
  font-size: 12px;
}

.page-actions {
  display: flex;
  gap: 6px;
}

.page-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.button-icon {
  width: 14px;
  height: 14px;
  margin-left: 4px;
}

.project-page :deep(.el-button) {
  border-radius: var(--pa-radius-control);
  font-weight: 500;
  height: 32px;
  padding: 0 14px;
}

.project-page :deep(.el-button:focus-visible) {
  outline: none;
  box-shadow: var(--pa-ring-primary);
}

@media (max-width: 640px) {
  .page-intro { align-items: flex-start; flex-direction: column; }
  .toolbar { padding: 10px 14px; }
  .pagination { padding: 12px 14px; }
}
</style>
