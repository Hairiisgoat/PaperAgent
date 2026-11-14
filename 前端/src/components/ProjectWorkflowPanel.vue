<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/message-box/style/css'
import { useWorkflowStore } from '../stores/workflows'
import { formatLocalTime } from '../utils/projectDisplay'
import { runStatusLabel, stageLabel } from '../utils/workflowDisplay'
import { http } from '../services/http'
import type { RunDetail } from '../types/workflows'

const props = defineProps<{ projectId: string; lifecycleStatus: string }>()
const store = useWorkflowStore()
const router = useRouter()
const confirming = ref(false)
const runs = ref<RunDetail[]>([])
const runsError = ref(false)
const runsLoading = ref(false)
let runsController: AbortController | undefined
const completedPaper = computed(() => runs.value.find(run => run.status === 'completed' && run.current_stage === 'writing'))
async function loadRuns(projectId: string): Promise<void> {
  runsController?.abort()
  const controller = new AbortController()
  runsController = controller; runs.value = []; runsError.value = false; runsLoading.value = true
  try {
    const response = await http.get<RunDetail[]>(`/projects/${encodeURIComponent(projectId)}/workflow-runs`, { signal: controller.signal })
    if (controller.signal.aborted) return
    if (!Array.isArray(response.data) || response.data.some(run => run.project_id !== projectId)) throw new Error('scope')
    runs.value = response.data
  } catch { if (!controller.signal.aborted) runsError.value = true }
  finally { if (!controller.signal.aborted) runsLoading.value = false }
}
const errorMessage = computed(() => {
  const error = store.startError
  if (!error) return ''
  if (error.kind === 'timeout' || error.kind === 'network' || error.kind === 'unknown' || (error.status !== undefined && error.status >= 500))
    return '启动结果未确认，请先核对运行记录；系统不会自动重复提交。'
  return error.message
})
watch(() => props.projectId, id => { store.startError = null; void store.loadLatest(id); void loadRuns(id) }, { immediate: true })
onBeforeUnmount(() => { store.cancelSummary(); runsController?.abort() })
async function start(realPlanner = false): Promise<void> {
  if (props.lifecycleStatus !== 'active' || store.starting || confirming.value) return
  if (realPlanner) {
    confirming.value = true
    try { await ElMessageBox.confirm('将调用真实模型，可能产生费用。确认启动？', '启动真实 Planner', { confirmButtonText: '确认启动', cancelButtonText: '取消', type: 'warning' }) }
    catch { return } finally { confirming.value = false }
    if (store.starting) return
  }
  const project = props.projectId
  const run = await store.start(project, realPlanner)
  if (run && router.currentRoute.value.name === 'project-detail' && typeof router.currentRoute.value.params.projectId === 'string' && router.currentRoute.value.params.projectId.toLowerCase() === project)
    await router.push({ name: 'workflow-run', params: { projectId: project, runId: run.run_id } })
}
</script>

<template>
  <section class="workflow-card" aria-labelledby="workflow-heading">
    <div class="head">
      <div>
        <h2 id="workflow-heading">论文工作台</h2>
        <p>继续阅读已有论文，或查看工作流进展。</p>
      </div>
      <div class="head-actions">
        <ElButton type="primary" :loading="store.starting" :disabled="store.starting || confirming || lifecycleStatus !== 'active'" @click="start(false)">启动模拟工作流</ElButton>
        <ElButton :disabled="store.starting || confirming || lifecycleStatus !== 'active'" @click="start(true)">启动真实 Planner</ElButton>
      </div>
    </div>
    <div v-if="completedPaper" class="paper-entry">
      <div class="entry-main">
        <span class="entry-icon">文</span>
        <div>
          <strong>继续阅读论文</strong>
          <p>查看完整初稿、引用和审查建议</p>
        </div>
      </div>
      <RouterLink class="entry-link" :to="{ name: 'workflow-run', params: { projectId, runId: completedPaper.run_id } }">打开论文工作台 <span>→</span></RouterLink>
    </div>
    <div v-if="runsLoading" class="empty" role="status">正在读取项目记录…</div>
    <p v-else-if="runsError" class="alert" role="alert">无法读取项目运行记录。<button type="button" @click="loadRuns(projectId)">重试</button></p>
    <details v-else-if="runs.length" class="run-history"><summary>查看最近 {{ runs.length }} 次运行</summary><div class="run-list"><div v-for="run in runs" :key="run.run_id" class="run-row"><span class="run-stage">{{ stageLabel(run.current_stage) }} · {{ runStatusLabel(run.status) }}</span><span class="mono run-time">{{ formatLocalTime(run.started_at || run.created_at) }}</span><RouterLink :to="{ name: 'workflow-run', params: { projectId, runId: run.run_id } }">查看 →</RouterLink></div></div></details>
    <p v-if="lifecycleStatus !== 'active'" class="hint">{{ lifecycleStatus === 'archived' ? '已归档项目恢复后可启动工作流。' : '当前项目状态不允许启动工作流。' }}</p>
    <p v-if="errorMessage" class="alert" role="alert">{{ errorMessage }}</p>
    <p v-if="store.summaryError" class="alert" role="alert">最近运行状态读取失败，请打开运行详情重试。</p>
    <div v-if="store.summary" class="summary">
      <div><span>当前状态</span><strong>{{ runStatusLabel(store.summary.status) }}</strong></div>
      <div><span>当前阶段</span><strong>{{ stageLabel(store.summary.current_stage) }}</strong></div>
      <div><span>最近运行（本地时间）</span><strong class="mono">{{ formatLocalTime(store.summary.started_at || store.summary.created_at) }}</strong></div>
      <RouterLink class="summary-link" :to="{ name: 'workflow-run', params: { projectId, runId: store.summary.run_id } }">查看运行详情 →</RouterLink>
    </div>
    <div v-else-if="store.summaryRunId" class="summary-fallback"><RouterLink :to="{ name: 'workflow-run', params: { projectId, runId: store.summaryRunId } }">打开最近运行详情 →</RouterLink></div>
    <p v-else-if="!runsLoading && !runsError && !runs.length" class="empty">还没有运行记录，开始一次工作流后可在此查看。</p>
  </section>
</template>

<style scoped>
.workflow-card {
  padding: 20px var(--pa-space-card);
  border: var(--pa-border);
  border-radius: var(--pa-radius-card);
  background: var(--pa-bg-surface);
  box-shadow: var(--pa-shadow-card);
}

.mono {
  font-family: var(--pa-font-mono);
  font-variant-numeric: tabular-nums;
}

.head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
}

h2 {
  margin: 0;
  color: var(--pa-color-heading);
  font-size: 15px;
  font-weight: 600;
}

.head p {
  margin: 3px 0 0;
  color: var(--pa-text-meta);
  font-size: 12px;
}

.head-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.head-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

/* ---------- 论文工作台直达卡片 ---------- */
.paper-entry {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  margin: 16px 0 0;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: var(--pa-radius-card);
}

.entry-main {
  display: flex;
  gap: 12px;
  align-items: center;
}

.entry-icon {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  color: var(--el-color-primary);
  background: var(--pa-bg-surface);
  border: 1px solid var(--el-color-primary-light-7);
  border-radius: 7px;
  font-size: 13px;
  font-weight: 600;
  flex: none;
}

.paper-entry strong {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: var(--pa-color-heading);
}

.paper-entry p {
  margin: 2px 0 0;
  font-size: 12px;
  color: var(--pa-text-meta);
}

.entry-link {
  padding: 7px 14px;
  background: var(--el-color-primary);
  color: var(--pa-bg-surface);
  border-radius: var(--pa-radius-control);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
  text-decoration: none;
  transition: background-color 0.15s;
}

.entry-link:hover {
  background: var(--el-color-primary-dark-2);
}

/* ---------- 运行历史 ---------- */
.run-history {
  margin-top: 16px;
  font-size: 13px;
  color: var(--pa-text-body);
}

.run-history summary {
  list-style: none;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--pa-text-meta);
  font-size: 12px;
  font-weight: 500;
  user-select: none;
}

.run-history summary::-webkit-details-marker {
  display: none;
}

.run-history summary::before {
  content: '';
  width: 5px;
  height: 5px;
  border-right: 1.5px solid var(--pa-text-meta);
  border-bottom: 1.5px solid var(--pa-text-meta);
  transform: rotate(-45deg);
  transition: transform 0.15s;
}

.run-history[open] summary::before {
  transform: rotate(45deg);
}

.run-history summary:hover {
  color: var(--el-color-primary);
}

.run-list {
  margin-top: 10px;
  border: var(--pa-border);
  border-radius: var(--pa-radius-control);
  background: var(--pa-bg-inset);
  padding: 0 12px;
}

.run-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  padding: 10px 0;
  border-bottom: var(--pa-border);
  font-size: 12px;
}

.run-row:last-child {
  border-bottom: 0;
}

.run-stage {
  color: var(--pa-color-heading);
  font-weight: 500;
}

.run-time {
  color: var(--pa-text-meta);
}

.run-row a {
  margin-left: auto;
  color: var(--el-color-primary);
  font-weight: 500;
}

.hint, .empty {
  margin: 14px 0 0;
  color: var(--pa-text-meta);
  font-size: 13px;
  line-height: 1.6;
}

.alert {
  margin: 12px 0 0;
  color: var(--pa-color-danger);
  background: var(--pa-bg-danger);
  border: 1px solid var(--pa-border-danger);
  padding: 9px 12px;
  border-radius: var(--pa-radius-control);
  font-size: 13px;
}

.alert button {
  margin-left: 6px;
  border: 0;
  background: none;
  color: inherit;
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
}

/* ---------- 摘要行 ---------- */
.summary {
  display: flex;
  flex-wrap: wrap;
  gap: 28px;
  align-items: center;
  padding-top: 16px;
  border-top: var(--pa-border);
  margin-top: 16px;
}

.summary span, .summary strong {
  display: block;
  font-size: 12px;
}

.summary span {
  color: var(--pa-text-meta);
}

.summary strong {
  margin-top: 4px;
  color: var(--pa-color-heading);
  font-size: 13px;
  font-weight: 600;
}

.summary-link {
  margin-left: auto;
  font-weight: 500;
}

.summary-fallback {
  margin-top: 14px;
}

a {
  color: var(--el-color-primary);
  font-size: 13px;
  text-decoration: none;
}

a:hover {
  text-decoration: underline;
}

@media (max-width: 640px) {
  .workflow-card { padding: 18px 16px; }
  .head { align-items: flex-start; flex-direction: column; }
  .paper-entry { align-items: flex-start; flex-direction: column; }
  .summary { gap: 16px; }
  .summary-link { margin-left: 0; }
}
</style>
