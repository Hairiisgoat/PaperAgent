<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { onBeforeRouteLeave, useRoute, useRouter } from 'vue-router'
import { ElButton, ElDialog } from 'element-plus'
import { ArrowLeft, Document, Refresh, WarningFilled } from '@element-plus/icons-vue'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/dialog/style/css'
import { useProjectsStore } from '../stores/projects'
import ProjectWorkflowPanel from '../components/ProjectWorkflowPanel.vue'
import { formatLocalTime, lifecycleLabel, paperTypeLabel, progressLabel } from '../utils/projectDisplay'

const route = useRoute()
const router = useRouter()
const store = useProjectsStore()
const projectId = computed(() => typeof route.params.projectId === 'string' ? route.params.projectId.toLowerCase() : '')
const detailTitle = computed(() => store.detailError?.status === 404 ? '项目不存在或已删除' : store.detailError?.status === 422 ? '项目地址无效' : store.detailError?.status === 503 ? '服务暂时不可用' : '无法加载项目')
const detailDescription = computed(() => store.detailError?.status === 404 ? '请返回项目列表查看其他项目。' : store.detailError?.status === 422 ? '请检查项目地址后重试。' : '请检查网络或后端服务，然后重试。')
const requirementsText = computed(() => {
  const value = store.currentProject?.requirements
  return value && Object.keys(value).length ? JSON.stringify(value, null, 2) : ''
})
type LifecycleAction = 'archive' | 'restore' | 'delete'
const dialogOpen = ref(false)
const selectedAction = ref<LifecycleAction>('archive')
const busy = ref(false)
const notice = ref('')
let allowLeave = false
const dialogTitle = computed(() => selectedAction.value === 'archive' ? '确认归档项目' : selectedAction.value === 'restore' ? '确认恢复项目' : '确认删除项目')
function openAction(action: LifecycleAction): void {
  if (busy.value || store.mutationLoading) return
  selectedAction.value = action
  dialogOpen.value = true
}
async function confirmAction(): Promise<void> {
  if (busy.value || store.mutationLoading || !store.currentProject) return
  busy.value = true
  notice.value = ''
  const action = selectedAction.value
  const id = projectId.value
  try {
    const ok = await store.mutate(action, id)
    dialogOpen.value = false
    if (ok) {
      if (action === 'delete') {
        allowLeave = true
        await router.push('/projects')
        return
      }
      await Promise.all([store.loadDetail(id), store.load()])
      notice.value = store.detailError ? '操作已成功，但最新详情读取失败，请重试。' : action === 'archive' ? '项目已归档。' : '项目已恢复。'
    } else {
      const error = store.mutationError
      if (!error || error.status === 404 || error.status === 409 || error.status === 503 || error.kind === 'network' || error.kind === 'timeout' || error.kind === 'unknown' || (error.status !== undefined && error.status >= 500)) {
        await Promise.all([store.loadDetail(id), store.load()])
        notice.value = error?.status === 409 ? '项目状态已变化，请查看最新状态后再操作。' : error?.status === 503 ? '服务暂时不可用，操作结果未确认；已尝试重新读取，请检查最新状态。' : '操作结果未确认，已尝试重新读取；请检查最新状态后再操作。'
      } else {
        notice.value = '操作未完成，请检查请求后重试。'
      }
    }
  } finally {
    busy.value = false
  }
}
onBeforeRouteLeave(() => !busy.value || allowLeave)
watch(projectId, id => { if (id) void store.loadDetail(id) }, { immediate: true })
onBeforeUnmount(() => store.cancelDetail())
</script>

<template>
  <section class="detail-page" aria-label="项目详情">
    <RouterLink class="back-link" to="/projects"><ArrowLeft aria-hidden="true" /> 返回项目列表</RouterLink>
    <div v-if="notice" class="action-notice" role="status">{{ notice }}</div>
    <div v-if="store.detailLoading" class="state-card" role="status" aria-live="polite"><div class="loading-line"></div><div class="loading-line short"></div><p>正在加载项目…</p></div>
    <div v-else-if="store.detailError" class="state-card" role="alert"><div class="state-icon warning"><WarningFilled aria-hidden="true" /></div><h1>{{ detailTitle }}</h1><p>{{ detailDescription }}</p><ElButton v-if="store.detailError.status !== 404 && store.detailError.status !== 422" :icon="Refresh" @click="store.loadDetail(projectId)">重试</ElButton></div>
    <div v-else-if="!store.currentProject" class="state-card"><div class="state-icon"><Document aria-hidden="true" /></div><h1>暂无项目数据</h1><p>请返回项目列表后重试。</p></div>
    <template v-else>
      <header class="intro">
        <div class="intro-main">
          <div class="intro-title-row">
            <h1>{{ store.currentProject.title }}</h1>
            <span class="status" :class="store.currentProject.lifecycle_status === 'active' ? 'status-active' : 'status-neutral'"><span class="status-dot"></span>{{ lifecycleLabel(store.currentProject.lifecycle_status) }}</span>
          </div>
          <p class="intro-sub">项目详情 · 时间以本地时区显示</p>
        </div>
        <div class="detail-actions">
          <ElButton v-if="store.currentProject.lifecycle_status === 'active'" type="primary" :disabled="busy" @click="router.push({ name: 'project-edit', params: { projectId } })">编辑项目</ElButton>
          <ElButton v-if="store.currentProject.lifecycle_status === 'active'" :disabled="busy" @click="openAction('archive')">归档</ElButton>
          <ElButton v-if="store.currentProject.lifecycle_status === 'archived'" :disabled="busy" @click="openAction('restore')">恢复</ElButton>
          <ElButton v-if="['active', 'archived'].includes(store.currentProject.lifecycle_status)" type="danger" plain :disabled="busy" @click="openAction('delete')">删除</ElButton>
        </div>
      </header>
      <ProjectWorkflowPanel :project-id="projectId" :lifecycle-status="store.currentProject.lifecycle_status" />
      <div class="card">
        <div class="card-head"><span class="icon-box"><Document aria-hidden="true" /></span><div><h2>项目信息</h2><p>查看论文的基础配置与记录。</p></div></div>
        <dl class="details">
          <div class="detail-item full"><dt>论文主题</dt><dd class="topic pa-prose">{{ store.currentProject.topic }}</dd></div>
          <div class="detail-item"><dt>论文类型</dt><dd>{{ paperTypeLabel(store.currentProject.paper_type) }}</dd></div>
          <div class="detail-item"><dt>引用格式</dt><dd class="mono">{{ store.currentProject.citation_style }}</dd></div>
          <div class="detail-item"><dt>项目状态</dt><dd>{{ lifecycleLabel(store.currentProject.lifecycle_status) }}</dd></div>
          <div class="detail-item"><dt>项目进度</dt><dd>{{ progressLabel(store.currentProject.progress_status) }}</dd></div>
          <div class="detail-item"><dt>创建时间（本地时间）</dt><dd class="mono">{{ formatLocalTime(store.currentProject.created_at) }}</dd></div>
          <div class="detail-item"><dt>更新时间（本地时间）</dt><dd class="mono">{{ formatLocalTime(store.currentProject.updated_at) }}</dd></div>
          <div v-if="store.currentProject.archived_at" class="detail-item"><dt>归档时间（本地时间）</dt><dd class="mono">{{ formatLocalTime(store.currentProject.archived_at) }}</dd></div>
        </dl>
      </div>
      <div class="card requirements"><h2>论文要求</h2><p class="section-help">当前项目保存的要求配置，仅供查看。</p><pre v-if="requirementsText">{{ requirementsText }}</pre><p v-else class="empty-requirements">暂无配置</p></div>
    </template>
    <ElDialog v-model="dialogOpen" :title="dialogTitle" width="min(460px, 90vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <p class="dialog-line">项目：<strong>{{ store.currentProject?.title }}</strong></p>
      <p v-if="selectedAction === 'archive'" class="dialog-desc">归档后项目将变为只读，可在恢复后继续编辑。</p>
      <p v-else-if="selectedAction === 'restore'" class="dialog-desc">恢复后项目将重新变为可编辑状态。</p>
      <p v-else class="dialog-desc">删除后项目将从普通列表中移除。此操作为软删除，当前界面不提供恢复入口。</p>
      <template #footer><div class="dialog-footer"><ElButton :disabled="busy" @click="dialogOpen = false">取消</ElButton><ElButton :type="selectedAction === 'delete' ? 'danger' : 'primary'" :loading="busy" :disabled="busy" @click="confirmAction">{{ selectedAction === 'delete' ? '确认删除' : selectedAction === 'archive' ? '确认归档' : '确认恢复' }}</ElButton></div></template>
    </ElDialog>
  </section>
</template>

<style scoped>
.detail-page {
  max-width: 1120px;
  margin: 0 auto;
  color: var(--pa-text-body);
}

.mono {
  font-family: var(--pa-font-mono);
  font-variant-numeric: tabular-nums;
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 14px;
  padding: 4px 8px 4px 4px;
  margin-left: -4px;
  border-radius: var(--pa-radius-control);
  color: var(--pa-text-meta);
  font-size: 13px;
  text-decoration: none;
  transition: color 0.15s, background-color 0.15s;
}

.back-link:hover {
  color: var(--el-color-primary);
  background: var(--pa-bg-hover);
}

.back-link svg {
  width: 14px;
  height: 14px;
}

.action-notice {
  margin-bottom: 16px;
  padding: 10px 14px;
  border: var(--pa-border);
  border-left: 3px solid var(--el-color-primary);
  border-radius: var(--pa-radius-control);
  color: var(--pa-text-body);
  background: var(--pa-bg-surface);
  box-shadow: var(--pa-shadow-card);
  font-size: 13px;
  line-height: 1.6;
}

/* ---------- 紧凑现代详情页顶栏 ---------- */
.intro {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 20px;
  margin-bottom: 20px;
  flex-wrap: wrap;
}

.intro-main {
  min-width: 0;
  flex: 1;
}

.intro-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

h1 {
  margin: 0;
  color: var(--pa-color-heading);
  font-size: 20px;
  font-weight: 600;
  letter-spacing: -0.02em;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.intro-sub {
  margin: 4px 0 0;
  color: var(--pa-text-meta);
  font-size: 13px;
}

.status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 2px 10px;
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

.detail-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}

.detail-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

/* ---------- 卡片 ---------- */
.card, .state-card {
  margin-top: 16px;
  background: var(--pa-bg-surface);
  border: var(--pa-border);
  border-radius: var(--pa-radius-card);
  padding: 20px var(--pa-space-card);
  box-shadow: var(--pa-shadow-card);
}

.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 16px;
  margin-bottom: 4px;
  border-bottom: var(--pa-border);
}

.icon-box {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 7px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-8);
  color: var(--el-color-primary);
  flex: none;
}

.icon-box svg {
  width: 17px;
  height: 17px;
}

h2 {
  margin: 0;
  color: var(--pa-color-heading);
  font-size: 15px;
  font-weight: 600;
}

.card-head p, .section-help {
  margin: 3px 0 0;
  color: var(--pa-text-meta);
  font-size: 12px;
}

.details {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  margin: 0;
  gap: 0 24px;
}

.detail-item {
  min-width: 0;
  padding: 16px 0;
  border-bottom: var(--pa-border);
}

.detail-item.full {
  grid-column: 1 / -1;
}

dt {
  color: var(--pa-text-meta);
  font-size: 12px;
  font-weight: 500;
}

dd {
  margin: 6px 0 0;
  color: var(--pa-color-heading);
  font-size: 14px;
  overflow-wrap: anywhere;
}

.topic {
  color: var(--pa-text-body);
  line-height: 1.7;
  white-space: pre-wrap;
  max-width: 76ch;
}

.requirements pre {
  margin: 14px 0 0;
  padding: 12px 14px;
  border-radius: var(--pa-radius-control);
  background: var(--pa-bg-inset);
  border: var(--pa-border);
  color: var(--pa-text-body);
  font-family: var(--pa-font-mono);
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.65;
}

.empty-requirements {
  margin: 14px 0 0;
  color: var(--pa-text-faint);
  font-size: 13px;
}

/* ---------- 状态卡片 ---------- */
.state-card {
  min-height: 240px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
}

.state-card h1 {
  margin-top: 14px;
  font-size: 16px;
}

.state-card p {
  margin: 6px 0 16px;
  color: var(--pa-text-meta);
  font-size: 13px;
}

.state-icon {
  display: grid;
  place-items: center;
  width: 44px;
  height: 44px;
  border-radius: 10px;
  border: var(--pa-border);
  background: var(--pa-bg-inset);
  color: var(--pa-text-meta);
}

.state-icon.warning {
  background: var(--pa-bg-amber);
  border-color: var(--pa-border-amber);
  color: var(--pa-color-amber-strong);
}

.state-icon svg {
  width: 20px;
  height: 20px;
}

.loading-line {
  width: min(76%, 400px);
  height: 12px;
  margin: 6px;
  border-radius: 4px;
  background: var(--pa-bg-hover);
}

.loading-line.short {
  width: min(50%, 240px);
}

/* ---------- 弹窗与按钮 ---------- */
.dialog-line {
  margin: 0 0 8px;
  color: var(--pa-color-heading);
  font-size: 14px;
}

.dialog-desc {
  margin: 0;
  color: var(--pa-text-meta);
  font-size: 13px;
  line-height: 1.6;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}

.dialog-footer :deep(.el-button + .el-button) {
  margin-left: 0;
}

.detail-page :deep(.el-button) {
  border-radius: var(--pa-radius-control);
  font-weight: 500;
  height: 32px;
  padding: 0 14px;
}

.detail-page :deep(.el-button:focus-visible) {
  outline: none;
  box-shadow: var(--pa-ring-primary);
}

.detail-page :deep(.el-dialog) {
  border-radius: var(--pa-radius-card);
  border: var(--pa-border);
  box-shadow: var(--pa-shadow-float);
  padding: 20px 24px;
}

.detail-page :deep(.el-dialog__header) {
  padding: 0 0 12px;
  margin-right: 0;
  border-bottom: var(--pa-border);
}

.detail-page :deep(.el-dialog__title) {
  font-size: 15px;
  font-weight: 600;
  color: var(--pa-color-heading);
}

.detail-page :deep(.el-dialog__body) {
  padding: 16px 0;
}

.detail-page :deep(.el-dialog__footer) {
  padding: 12px 0 0;
  border-top: var(--pa-border);
}

@media (max-width: 760px) {
  .details { grid-template-columns: 1fr 1fr; }
}

@media (max-width: 640px) {
  .intro { flex-direction: column; align-items: flex-start; }
  .card, .state-card { padding: 18px 16px; }
  .details { grid-template-columns: 1fr; }
  .detail-item.full { grid-column: auto; }
}
</style>
