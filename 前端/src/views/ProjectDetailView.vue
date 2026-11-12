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
      <div class="intro"><div><p class="eyebrow">WORKSPACE / PROJECT</p><h1>{{ store.currentProject.title }}</h1><p>项目详情 · 时间以本地时区显示</p></div><span class="status" :class="store.currentProject.lifecycle_status === 'active' ? 'status-active' : 'status-neutral'"><span class="status-dot"></span>{{ lifecycleLabel(store.currentProject.lifecycle_status) }}</span></div>
      <div class="detail-actions">
        <ElButton v-if="store.currentProject.lifecycle_status === 'active'" type="primary" :disabled="busy" @click="router.push({ name: 'project-edit', params: { projectId } })">编辑项目</ElButton>
        <ElButton v-if="store.currentProject.lifecycle_status === 'active'" :disabled="busy" @click="openAction('archive')">归档</ElButton>
        <ElButton v-if="store.currentProject.lifecycle_status === 'archived'" :disabled="busy" @click="openAction('restore')">恢复</ElButton>
        <ElButton v-if="['active', 'archived'].includes(store.currentProject.lifecycle_status)" type="danger" plain :disabled="busy" @click="openAction('delete')">删除</ElButton>
      </div>
      <ProjectWorkflowPanel :project-id="projectId" :lifecycle-status="store.currentProject.lifecycle_status" />
      <div class="section-line"><span>项目概览</span><span class="line"></span></div>
      <div class="card"><div class="card-head"><span class="icon-box"><Document aria-hidden="true" /></span><div><h2>项目信息</h2><p>查看论文的基础配置与记录。</p></div></div>
        <dl class="details"><div class="detail-item full"><dt>论文主题</dt><dd class="topic">{{ store.currentProject.topic }}</dd></div><div class="detail-item"><dt>论文类型</dt><dd>{{ paperTypeLabel(store.currentProject.paper_type) }}</dd></div><div class="detail-item"><dt>引用格式</dt><dd>{{ store.currentProject.citation_style }}</dd></div><div class="detail-item"><dt>项目状态</dt><dd>{{ lifecycleLabel(store.currentProject.lifecycle_status) }}</dd></div><div class="detail-item"><dt>项目进度</dt><dd>{{ progressLabel(store.currentProject.progress_status) }}</dd></div><div class="detail-item"><dt>创建时间（本地时间）</dt><dd>{{ formatLocalTime(store.currentProject.created_at) }}</dd></div><div class="detail-item"><dt>更新时间（本地时间）</dt><dd>{{ formatLocalTime(store.currentProject.updated_at) }}</dd></div><div v-if="store.currentProject.archived_at" class="detail-item"><dt>归档时间（本地时间）</dt><dd>{{ formatLocalTime(store.currentProject.archived_at) }}</dd></div></dl>
      </div>
      <div class="card requirements"><h2>论文要求</h2><p class="section-help">当前项目保存的要求配置，仅供查看。</p><pre v-if="requirementsText">{{ requirementsText }}</pre><p v-else class="empty-requirements">暂无配置</p></div>
    </template>
    <ElDialog v-model="dialogOpen" :title="dialogTitle" width="min(480px, 90vw)" :close-on-click-modal="!busy" :close-on-press-escape="!busy" :show-close="!busy">
      <p>项目：<strong>{{ store.currentProject?.title }}</strong></p>
      <p v-if="selectedAction === 'archive'">归档后项目将变为只读，可在恢复后继续编辑。</p>
      <p v-else-if="selectedAction === 'restore'">恢复后项目将重新变为可编辑状态。</p>
      <p v-else>删除后项目将从普通列表中移除。此操作为软删除，当前界面不提供恢复入口。</p>
      <template #footer><ElButton :disabled="busy" @click="dialogOpen = false">取消</ElButton><ElButton :type="selectedAction === 'delete' ? 'danger' : 'primary'" :loading="busy" :disabled="busy" @click="confirmAction">{{ selectedAction === 'delete' ? '确认删除' : selectedAction === 'archive' ? '确认归档' : '确认恢复' }}</ElButton></template>
    </ElDialog>
  </section>
</template>

<style scoped>
.detail-actions { display: flex; flex-wrap: wrap; gap: 8px; margin: -14px 0 28px; }.detail-actions :deep(.el-button + .el-button) { margin-left: 0; }.action-notice { margin-bottom: 18px; padding: 12px 14px; border: 1px solid #d4e4ec; border-radius: 8px; color: #365f73; background: #f0f7fa; font-size: 13px; line-height: 1.6; }
.detail-page { max-width: 1020px; margin: 0 auto; }.back-link { display: inline-flex; align-items: center; gap: 7px; margin-bottom: 30px; color: #537689; font-size: 13px; text-decoration: none; }.back-link svg { width: 15px; height: 15px; }
.intro { display: flex; justify-content: space-between; align-items: flex-end; gap: 18px; margin-bottom: 35px; }.eyebrow { margin: 0 0 12px; color: #6c8c9b; font-size: 10px; font-weight: 700; letter-spacing: .16em; }
h1 { margin: 0; color: #173247; font-size: 30px; overflow-wrap: anywhere; }.intro p:last-child { margin: 11px 0 0; color: #718391; font-size: 13px; }
.status { display: inline-flex; align-items: center; gap: 7px; padding: 7px 12px; border-radius: 20px; font-size: 12px; white-space: nowrap; }.status-active { color: #21617d; background: #eaf3f6; }.status-neutral { color: #647783; background: #edf1f3; }.status-dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
.section-line { display: flex; align-items: center; gap: 14px; margin-bottom: 15px; color: #405d6d; font-size: 13px; font-weight: 600; }.section-line .line { height: 1px; flex: 1; background: #dce5ea; }
.card, .state-card { background: #fff; border: 1px solid #e1e8ed; border-radius: 12px; padding: 30px 34px; box-shadow: 0 8px 28px rgba(32, 63, 82, .035); }
.card-head { display: flex; align-items: center; gap: 13px; padding-bottom: 25px; margin-bottom: 8px; border-bottom: 1px solid #edf1f4; }.icon-box { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 10px; background: #edf5f8; color: #53849a; }.icon-box svg { width: 21px; height: 21px; }
h2 { margin: 0; color: #263f4d; font-size: 16px; }.card-head p, .section-help { margin: 7px 0 0; color: #83939e; font-size: 12px; }
.details { display: grid; grid-template-columns: 1fr 1fr; margin: 0; gap: 0 32px; }.detail-item { min-width: 0; padding: 21px 0; border-bottom: 1px solid #f0f3f5; }.detail-item.full { grid-column: 1 / -1; }dt { color: #81929c; font-size: 12px; }dd { margin: 9px 0 0; color: #2e4959; font-size: 14px; overflow-wrap: anywhere; }.topic { line-height: 1.7; white-space: pre-wrap; }
.requirements { margin-top: 18px; }.requirements pre { margin: 20px 0 0; padding: 16px; border-radius: 7px; background: #f5f8fa; color: #405d6d; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; line-height: 1.7; }.empty-requirements { margin: 22px 0 0; color: #8b9ba5; font-size: 13px; }
.state-card { min-height: 280px; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }.state-card h1 { margin-top: 17px; font-size: 17px; }.state-card p { margin: 10px 0 18px; color: #80919c; font-size: 13px; }.state-icon { display: grid; place-items: center; width: 54px; height: 54px; border-radius: 16px; background: #f1f7f9; color: #6c97aa; }.state-icon.warning { background: #fbf6ec; color: #a17437; }.state-icon svg { width: 25px; height: 25px; }.loading-line { width: min(80%, 440px); height: 14px; margin: 9px; border-radius: 5px; background: #edf2f5; }.loading-line.short { width: min(55%, 280px); }
@media (max-width: 640px) { h1 { font-size: 25px; }.intro { align-items: flex-start; flex-direction: column; }.card, .state-card { padding: 24px 18px; }.details { grid-template-columns: 1fr; }.detail-item.full { grid-column: auto; } }
</style>
<style scoped>
.detail-page { max-width:1200px; }
.intro { margin-bottom:30px; }
.intro h1 { color:#203c2d; letter-spacing:-.025em; }
.eyebrow { color:#7d9987; }
.section-line { margin-top:34px; color:#63796a; }
.card { border-color:#e2ebe3; box-shadow:none; }
.icon-box { background:#edf5ee; color:#538567; }
.status-active { background:#e9f3e9; color:#39724f; }
.detail-actions :deep(.el-button--primary) { --el-button-bg-color:#396d50; --el-button-border-color:#396d50; }
</style>
