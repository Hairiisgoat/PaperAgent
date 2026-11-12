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
    <div class="page-intro">
      <div>
        <p class="eyebrow">WORKSPACE / PROJECTS</p>
        <h1 id="page-title">我的项目</h1>
        <p class="subtitle">在这里查看和管理你的论文项目。</p>
      </div>
      <div class="primary-action">
        <ElButton type="primary" :icon="Plus" @click="$router.push('/projects/new')">新建项目</ElButton>
      </div>
    </div>

    <div class="section-line"><span>项目概览</span><span class="line"></span><span class="section-caption">YOUR RESEARCH, ORGANIZED</span></div>
    <div class="panel">
      <div class="panel-head">
        <div><h2>项目列表</h2><p>按状态查看你的论文项目</p></div>
        <ElButton :icon="Refresh" :disabled="store.loading" aria-label="刷新项目列表" @click="store.load()">刷新</ElButton>
      </div>
      <div class="toolbar">
        <div class="filters" role="group" aria-label="按项目状态筛选">
          <button v-for="filter in filters" :key="filter.label" type="button" :class="{ selected: store.lifecycleStatus === filter.value }" :aria-pressed="store.lifecycleStatus === filter.value" @click="store.setFilter(filter.value)">{{ filter.label }}</button>
        </div>
        <span v-if="!store.loading && !store.error" class="result-count">共 <strong>{{ store.total }}</strong> 个项目</span>
      </div>

      <div v-if="store.loading" class="loading-state" role="status" aria-live="polite">
        <div class="loading-line long"></div><div class="loading-line"></div><div class="loading-line"></div>
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
          <ElTableColumn prop="title" label="项目名称" min-width="170" show-overflow-tooltip>
            <template #default="scope"><RouterLink class="project-name" :to="{ name: 'project-detail', params: { projectId: scope.row.id } }"><Document aria-hidden="true" />{{ scope.row.title }}</RouterLink></template>
          </ElTableColumn>
          <ElTableColumn prop="topic" label="研究主题" min-width="200" show-overflow-tooltip />
          <ElTableColumn label="论文类型" min-width="130"><template #default="scope">{{ paperTypeLabel(scope.row.paper_type) }}</template></ElTableColumn>
          <ElTableColumn label="项目状态" min-width="125"><template #default="scope"><span class="status" :class="scope.row.lifecycle_status === 'active' ? 'status-active' : 'status-neutral'"><span class="status-dot"></span>{{ lifecycleLabel(scope.row.lifecycle_status) }}</span></template></ElTableColumn>
          <ElTableColumn label="项目进度" min-width="110"><template #default="scope">{{ progressLabel(scope.row.progress_status) }}</template></ElTableColumn>
          <ElTableColumn label="更新时间（本地时间）" min-width="180"><template #default="scope">{{ formatLocalTime(scope.row.updated_at) }}</template></ElTableColumn>
        </ElTable>
      </div>
      <div v-if="!store.loading && !store.error && store.loaded && store.total > 0" class="pagination" aria-label="项目列表分页">
        <span>第 {{ store.page }} / {{ pages }} 页</span>
        <div class="page-actions">
          <ElButton :icon="ArrowLeft" :disabled="store.page <= 1" @click="store.setPage(store.page - 1)">上一页</ElButton>
          <ElButton :disabled="store.page >= pages" @click="store.setPage(store.page + 1)">下一页 <ArrowRight class="button-icon" aria-hidden="true" /></ElButton>
        </div>
      </div>
    </div>
    <p class="footer-note">项目列表按创建时间排序 · 数据来自你的工作空间</p>
  </section>
</template>

<style scoped>
.project-page { max-width: 1420px; margin: 0 auto; }
.page-intro { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; margin-bottom: 43px; }
.eyebrow { margin: 0 0 12px; color: #6c8c9b; font-size: 10px; font-weight: 700; letter-spacing: .16em; }
h1 { margin: 0; color: #173247; font-size: 30px; line-height: 1.25; letter-spacing: -.03em; }
.subtitle { margin: 11px 0 0; color: #718391; font-size: 14px; }
.primary-action { display: flex; flex-direction: column; align-items: flex-end; gap: 7px; }
.primary-action span { color: #899aa5; font-size: 11px; }
.section-line { display: flex; align-items: center; gap: 14px; margin-bottom: 15px; color: #405d6d; font-size: 13px; font-weight: 600; }
.section-line .line { height: 1px; flex: 1; background: #dce5ea; }
.section-caption { color: #a0afb8; font-size: 10px; letter-spacing: .1em; font-weight: 500; }
.panel { background: #fff; border: 1px solid #e1e8ed; border-radius: 12px; box-shadow: 0 8px 28px rgba(32, 63, 82, .035); overflow: hidden; }
.panel-head { display: flex; justify-content: space-between; align-items: center; gap: 20px; padding: 26px 28px 22px; }
h2 { margin: 0; font-size: 16px; color: #263f4d; }
.panel-head p { margin: 7px 0 0; font-size: 12px; color: #83939e; }
.toolbar { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 0 28px; border-bottom: 1px solid #edf1f4; }
.filters { display: flex; gap: 22px; }
.filters button { padding: 13px 1px 12px; border: 0; border-bottom: 2px solid transparent; background: none; color: #71848f; cursor: pointer; font-size: 13px; }
.filters button.selected { color: #1b5b79; border-bottom-color: #2a6c8a; font-weight: 600; }
.result-count { color: #8495a0; font-size: 12px; white-space: nowrap; }
.result-count strong { color: #405d6d; }
.state-box { min-height: 292px; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 40px 20px; text-align: center; }
.state-icon { display: grid; place-items: center; width: 54px; height: 54px; border: 1px solid #dce9ef; border-radius: 16px; background: #f1f7f9; color: #6c97aa; }
.state-icon svg { width: 25px; height: 25px; }
.state-icon.warning { color: #a17437; background: #fbf6ec; border-color: #eee3ce; }
h3 { margin: 17px 0 0; color: #304c5d; font-size: 15px; }
.state-box p { max-width: 430px; margin: 9px 0 18px; color: #8798a2; font-size: 13px; line-height: 1.6; }
.text-action { display: inline-flex; align-items: center; gap: 5px; padding: 6px; border: 0; background: transparent; color: #226482; font-size: 13px; cursor: pointer; }
.text-action svg { width: 13px; height: 13px; }
.loading-state { min-height: 292px; display: flex; flex-direction: column; justify-content: center; gap: 18px; padding: 42px 9%; color: #81939e; font-size: 12px; }
.loading-line { height: 14px; width: 72%; border-radius: 5px; background: #edf2f5; }
.loading-line.long { width: 92%; }
.table-wrap { padding: 0 10px; overflow-x: auto; }
.project-name { text-decoration: none; display: flex; align-items: center; gap: 9px; font-weight: 600; color: #23485c; }
.project-name svg { width: 16px; height: 16px; color: #7195a6; flex: none; }
.status { display: inline-flex; align-items: center; gap: 6px; padding: 4px 9px; border-radius: 20px; font-size: 12px; white-space: nowrap; }
.status-active { color: #21617d; background: #eaf3f6; }
.status-neutral { color: #647783; background: #f0f3f5; }
.status-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.pagination { display: flex; justify-content: space-between; align-items: center; padding: 18px 28px; color: #7d8f9a; font-size: 12px; }
.button-icon { width: 14px; height: 14px; margin-left: 5px; }
.footer-note { margin: 18px 3px; color: #99a9b2; font-size: 11px; }
@media (max-width: 640px) { .page-intro { align-items: flex-start; flex-direction: column; margin-bottom: 30px; } .primary-action { align-items: flex-start; } .panel-head { padding: 22px 18px; } .toolbar { padding: 0 18px; } .filters { gap: 14px; } .section-caption { display: none; } .pagination { padding: 16px 18px; } }
</style>
<style scoped>
.project-page { max-width:1320px; }
.page-intro { margin-bottom:30px; padding:28px 30px; border:1px solid #e3ebe4; background:#fff; border-radius:12px; }
.page-intro h1 { color:#203c2d; }
.eyebrow { color:#789583; }
.panel { border-color:#e2ebe3; box-shadow:none; }
.section-line { color:#63796a; }
.filters button.selected { color:#356c4d; border-bottom-color:#57936b; }
.project-name { color:#2c6042; }
.status-active { background:#edf5ed; color:#3b7650; }
.primary-action :deep(.el-button--primary) { --el-button-bg-color:#396d50; --el-button-border-color:#396d50; }
@media(max-width:640px) { .page-intro { padding:22px 18px; } }
</style>
