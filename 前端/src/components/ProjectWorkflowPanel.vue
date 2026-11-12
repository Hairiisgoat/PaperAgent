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
    <div class="head"><div><p class="overline">YOUR RESEARCH</p><h2 id="workflow-heading">论文工作台</h2><p>继续阅读已有论文，或查看工作流进展。</p></div>
      <div><ElButton type="primary" :loading="store.starting" :disabled="store.starting || confirming || lifecycleStatus !== 'active'" @click="start(false)">启动模拟工作流</ElButton><ElButton :disabled="store.starting || confirming || lifecycleStatus !== 'active'" @click="start(true)">启动真实 Planner</ElButton></div>
    </div>
    <div v-if="completedPaper" class="paper-entry"><div><span class="entry-icon">文</span><div><strong>继续阅读论文</strong><p>查看完整初稿、引用和审查建议</p></div></div><RouterLink class="entry-link" :to="{ name: 'workflow-run', params: { projectId, runId: completedPaper.run_id } }">打开论文工作台 <span>→</span></RouterLink></div>
    <div v-if="runsLoading" class="empty" role="status">正在读取项目记录…</div>
    <p v-else-if="runsError" class="alert" role="alert">无法读取项目运行记录。<button type="button" @click="loadRuns(projectId)">重试</button></p>
    <details v-else-if="runs.length" class="run-history"><summary>查看最近 {{ runs.length }} 次运行</summary><div v-for="run in runs" :key="run.run_id" class="run-row"><span>{{ stageLabel(run.current_stage) }} · {{ runStatusLabel(run.status) }}</span><span>{{ formatLocalTime(run.started_at || run.created_at) }}</span><RouterLink :to="{ name: 'workflow-run', params: { projectId, runId: run.run_id } }">查看 →</RouterLink></div></details>
    <p v-if="lifecycleStatus !== 'active'" class="hint">{{ lifecycleStatus === 'archived' ? '已归档项目恢复后可启动工作流。' : '当前项目状态不允许启动工作流。' }}</p>
    <p v-if="errorMessage" class="alert" role="alert">{{ errorMessage }}</p>
    <p v-if="store.summaryError" class="alert" role="alert">最近运行状态读取失败，请打开运行详情重试。</p>
    <div v-if="store.summary" class="summary">
      <div><span>当前状态</span><strong>{{ runStatusLabel(store.summary.status) }}</strong></div>
      <div><span>当前阶段</span><strong>{{ stageLabel(store.summary.current_stage) }}</strong></div>
      <div><span>最近运行（本地时间）</span><strong>{{ formatLocalTime(store.summary.started_at || store.summary.created_at) }}</strong></div>
      <RouterLink :to="{ name: 'workflow-run', params: { projectId, runId: store.summary.run_id } }">查看运行详情 →</RouterLink>
    </div>
    <div v-else-if="store.summaryRunId"><RouterLink :to="{ name: 'workflow-run', params: { projectId, runId: store.summaryRunId } }">打开最近运行详情 →</RouterLink></div>
    <p v-else-if="!runsLoading && !runsError && !runs.length" class="empty">还没有运行记录，开始一次工作流后可在此查看。</p>
  </section>
</template>

<style scoped>
.workflow-card { margin-top:18px; padding:28px 34px; border:1px solid #e1e8ed; border-radius:12px; background:#fff; }
.overline { color:#789689; font-size:10px; font-weight:700; letter-spacing:.14em; margin:0 0 8px; }.paper-entry { display:flex; align-items:center; justify-content:space-between; gap:16px; padding:22px; margin:22px 0 0; background:#f2f7f2; border:1px solid #dce9df; border-radius:10px; }.paper-entry>div { display:flex; gap:14px; align-items:center; }.paper-entry strong { font-size:15px; color:#244a35; }.paper-entry p { margin:6px 0 0; font-size:12px; color:#748a7b; }.entry-icon { display:grid; place-items:center; width:42px; height:42px; color:#3e7453; background:white; border-radius:9px; font-weight:700; }.entry-link { padding:10px 14px; background:#376b4e; color:white; border-radius:7px; white-space:nowrap; }.run-history { margin-top:20px; font-size:13px; color:#5c7466; }.run-history summary { cursor:pointer; }.run-row { display:flex; flex-wrap:wrap; gap:15px; padding:12px 0; border-bottom:1px solid #eef2ef; }.run-row a { margin-left:auto; }
.head { display:flex; justify-content:space-between; align-items:center; gap:16px; }
h2 { margin:0; color:#263f4d; font-size:16px; } .head p,.hint,.empty { color:#758a97; font-size:13px; line-height:1.6; }
.summary { display:flex; flex-wrap:wrap; gap:28px; align-items:end; padding-top:20px; border-top:1px solid #edf1f4; margin-top:20px; }
.summary span,.summary strong { display:block; font-size:12px; } .summary span { color:#81929c; } .summary strong { margin-top:7px; color:#294a5b; font-weight:600; }
a { color:#246d8a; font-size:13px; text-decoration:none; }.alert { color:#9c4438; background:#fff5f1; padding:10px; border-radius:6px; font-size:13px; }
@media(max-width:640px) { .workflow-card { padding:22px 18px; } .head { align-items:flex-start; flex-direction:column; } .summary { gap:18px; } }
@media(max-width:640px) { .paper-entry { align-items:flex-start; flex-direction:column; } }
</style>
