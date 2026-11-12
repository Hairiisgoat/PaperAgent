<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus'
import { ArrowLeft, Refresh } from '@element-plus/icons-vue'
import 'element-plus/es/components/button/style/css'
import WorkflowApprovalPanel from '../components/WorkflowApprovalPanel.vue'
import WritingDraftPanel from '../components/WritingDraftPanel.vue'
import { useWorkflowStore } from '../stores/workflows'
import { formatLocalTime } from '../utils/projectDisplay'
import { eventLabel, runStatusLabel, stageLabel } from '../utils/workflowDisplay'
import type { ConnectionState } from '../types/workflows'
import { retryWriting } from '../services/workflows'

const route = useRoute()
const router = useRouter()
const store = useWorkflowStore()
const retrying = ref(false)
const retryError = ref('')
async function retryDraft(): Promise<void> {
  if (retrying.value) return
  retrying.value = true; retryError.value = ''
  try {
    const result = await retryWriting(projectId.value, runId.value)
    if (result.project_id !== projectId.value || result.thread_id !== store.detail?.thread_id || result.run_id === runId.value) throw new Error('inconsistent')
    await router.push({ name: 'workflow-run', params: { projectId: projectId.value, runId: result.run_id } })
  } catch {
    retryError.value = '无法确认重试结果，或当前失败不支持安全重试；请先检查运行记录。'
  } finally { retrying.value = false }
}
const projectId = computed(() => typeof route.params.projectId === 'string' ? route.params.projectId.toLowerCase() : '')
const runId = computed(() => typeof route.params.runId === 'string' ? route.params.runId.toLowerCase() : '')
const connectionLabels: Record<ConnectionState, string> = { idle: '未连接', connecting: '正在连接', connected: '实时连接中', reconnecting: '正在重连', closed: '订阅已结束', error: '连接不可用' }
const statusTitle = computed(() => store.error?.status === 404 ? '项目或运行不存在' : store.error?.status === 422 ? '运行地址无效' : store.error?.status === 503 ? '服务暂时不可用' : '无法读取运行状态')
watch([projectId, runId], ([project, run]) => { if (project && run) void store.open(project, run) }, { immediate: true })
onBeforeUnmount(() => store.stop())
</script>

<template>
  <section class="run-page" aria-label="运行与论文工作台">
    <RouterLink class="back-link" :to="{ name: 'project-detail', params: { projectId } }"><ArrowLeft aria-hidden="true" /> 返回项目详情 <span v-if="store.detail?.current_stage === 'writing'"> / 论文工作台</span></RouterLink>
    <div v-if="store.detail?.current_stage !== 'writing'" class="heading"><div><p class="eyebrow">WORKSPACE / PAPERAGENT</p><h1 id="run-title">工作流运行监控</h1><p>查看本次运行状态与事件记录。</p></div>
      <span class="connection" :class="store.connection" role="status"><span class="dot"></span>{{ connectionLabels[store.connection] }}</span>
    </div>
    <div v-if="store.loading && !store.detail" class="card state" role="status">正在读取运行状态…</div>
    <div v-else-if="store.error && !store.detail" class="card state" role="alert"><h2>{{ statusTitle }}</h2><p>{{ store.error.message }}</p><ElButton :icon="Refresh" @click="store.open(projectId, runId)">重试</ElButton></div>
    <template v-else-if="store.detail">
      <div v-if="store.error" class="alert" role="alert">状态刷新失败：{{ store.error.message }}。请重试以确认最新状态。</div>
      <div v-if="store.connectionMessage" class="alert" role="status">{{ store.connectionMessage }} <button v-if="store.connection === 'error' && store.connectionMessage.includes('游标')" type="button" @click="store.replay()">从头加载事件</button><button v-else-if="store.connection === 'error'" type="button" @click="store.open(projectId, runId)">重新连接</button></div>
      <details v-if="store.detail.current_stage !== 'writing'" id="run-progress" open class="progress-details"><summary>生成进度与运行状态</summary><div class="overview">
        <div class="card metric"><span>运行状态</span><strong>{{ runStatusLabel(store.detail.status) }}</strong><small>以服务端状态为准</small></div>
        <div class="card metric"><span>当前阶段</span><strong>{{ stageLabel(store.detail.current_stage) }}</strong><small>第 {{ store.detail.attempt_no }} 次执行</small></div>
        <div class="card metric"><span>开始时间（本地）</span><strong>{{ formatLocalTime(store.detail.started_at || store.detail.created_at) }}</strong><small>更新时间 {{ formatLocalTime(store.detail.updated_at) }}</small></div>
      </div></details>
      <div v-if="store.detail.status === 'waiting_human'" class="notice"><strong>等待人工审核</strong><p>请检查下方审核对象并提交决定。</p></div>
      <WorkflowApprovalPanel v-if="store.detail.status === 'waiting_human'" :key="`${projectId}:${runId}`" :project-id="projectId" :run-id="runId" :thread-id="store.detail.thread_id" />
      <WritingDraftPanel v-if="store.detail.current_stage === 'writing'" :key="`${projectId}:${runId}`" :project-id="projectId" :run-id="runId" :running="store.detail.status === 'running'" />
      <details v-if="store.detail.current_stage === 'writing'" id="run-progress" class="progress-details"><summary>生成进度与运行状态</summary><div class="overview"><div class="card metric"><span>运行状态</span><strong>{{ runStatusLabel(store.detail.status) }}</strong></div><div class="card metric"><span>当前阶段</span><strong>{{ stageLabel(store.detail.current_stage) }}</strong></div><div class="card metric"><span>更新时间（本地）</span><strong>{{ formatLocalTime(store.detail.updated_at) }}</strong></div></div></details>
      <div v-if="store.detail.status === 'failed'" class="alert" role="alert">运行失败。{{ store.detail.current_stage === 'writing' ? '请核对下方人工重试条件。' : store.detail.retryable ? '此错误允许重试。' : '当前不支持重试。' }}</div>
      <div v-if="store.detail.status === 'failed' && store.detail.current_stage === 'writing'" class="card">
        <p>可继续生成未完成的内容；结果未知的调用需要人工核查。继续生成会调用真实模型并可能产生费用。</p>
        <ElButton :loading="retrying" :disabled="retrying" @click="retryDraft">继续生成论文</ElButton>
        <p v-if="retryError" role="alert">{{ retryError }}</p>
      </div>
      <details class="card run-info" :open="store.detail.current_stage !== 'writing'"><summary>运行信息</summary><dl><div><dt>运行 ID</dt><dd>{{ store.detail.run_id }}</dd></div><div><dt>工作流线程 ID</dt><dd>{{ store.detail.thread_id }}</dd></div><div v-if="store.detail.finished_at"><dt>结束时间（本地）</dt><dd>{{ formatLocalTime(store.detail.finished_at) }}</dd></div></dl></details>
      <details class="card events" :open="store.detail.current_stage !== 'writing'"><summary>执行时间线</summary><div class="events-head"><div><p>展示当前会话中收到的事件；运行状态以服务端查询为准。</p></div><span>游标 {{ store.lastSequence || '—' }}</span></div>
        <div v-if="!store.timeline.length" class="empty">暂无事件，正在等待工作流更新。</div>
        <ol v-else class="timeline">
          <li v-for="item in store.timeline" :key="item.event_id">
            <span class="event-dot"></span>
            <div class="event-content"><div class="event-head"><strong>{{ eventLabel(item) }}</strong><time>{{ formatLocalTime(item.timestamp) }}</time></div>
              <p>{{ stageLabel(item.stage) }}<template v-if="item.agent_name"> · {{ item.agent_name }}</template><template v-if="item.sequence"> · #{{ item.sequence }}</template></p>
              <p v-if="item.event_type === 'approval_required'" class="approval-note">请在上方审核区域提交决定。</p>
              <pre v-if="item.event_type === 'text_delta'">{{ item.payload.delta }}</pre>
              <p v-if="item.event_type === 'completed'">完成时间：{{ formatLocalTime(item.payload.finished_at) }}</p>
              <p v-if="item.event_type === 'error'" class="error-note">工作流发生错误。{{ item.payload.retryable ? '此错误允许重试。' : '当前不支持重试。' }}</p>
            </div>
          </li>
        </ol>
      </details>
    </template>
  </section>
</template>

<style scoped>
.run-page { max-width:1600px; margin:0 auto; } .back-link { display:inline-flex; align-items:center; gap:7px; margin-bottom:30px; color:#537689; font-size:13px; text-decoration:none; }.back-link svg { width:15px; height:15px; }
.heading { display:flex; align-items:flex-end; justify-content:space-between; gap:20px; margin-bottom:30px; }.eyebrow { color:#6c8c9b; font-size:10px; font-weight:700; letter-spacing:.16em; } h1 { margin:0; color:#173247; font-size:30px; }.heading p:last-child { color:#718391; font-size:13px; margin:10px 0 0; }
.connection { display:inline-flex; align-items:center; gap:7px; border-radius:20px; background:#edf2f5; color:#647b88; padding:8px 12px; font-size:12px; white-space:nowrap; }.connection.connected { color:#246b60; background:#eaf6f0; }.connection.error { color:#9b4943; background:#fff2ee; }.dot,.event-dot { width:7px; height:7px; border-radius:50%; background:currentColor; }
.overview { display:grid; grid-template-columns:repeat(3,1fr); gap:16px; }.card { border:1px solid #e1e8ed; border-radius:12px; background:#fff; padding:25px; }.metric span,.metric strong,.metric small { display:block; }.metric span { color:#7b8d98; font-size:12px; }.metric strong { color:#23455a; font-size:18px; margin:13px 0 8px; }.metric small { color:#91a0a8; font-size:11px; }
.notice,.alert { margin:18px 0; padding:15px 18px; border-radius:9px; font-size:13px; line-height:1.6; }.notice { color:#735b28; background:#fff9e9; border:1px solid #f0e4bc; }.notice p { margin:5px 0 0; }.alert { color:#8d493c; background:#fff5f1; border:1px solid #f0d9d1; }.alert button { color:inherit; border:0; text-decoration:underline; background:none; cursor:pointer; }
.run-info,.events { margin-top:18px; } h2 { color:#263f4d; font-size:16px; margin:0; }.run-info dl { display:flex; flex-wrap:wrap; gap:24px; margin:20px 0 0; }.run-info dt { color:#82939e; font-size:12px; }.run-info dd { color:#375465; margin:7px 0 0; font-size:12px; overflow-wrap:anywhere; }
.events-head { display:flex; justify-content:space-between; gap:16px; }.events-head p,.events-head span { color:#8798a2; font-size:12px; }.events-head span { white-space:nowrap; }.timeline { padding:0; margin:24px 0 0; list-style:none; }.timeline li { display:flex; gap:16px; border-left:1px solid #dce7eb; padding:0 0 22px 0; margin-left:4px; }.event-dot { margin:-1px 0 0 -4px; width:8px; height:8px; flex:none; background:#54879a; }.event-content { min-width:0; flex:1; }.event-head { display:flex; justify-content:space-between; gap:12px; }.event-head strong { font-size:13px; color:#2e4d5e; }.event-head time,.event-content p { color:#82939d; font-size:12px; }.event-content p { margin:7px 0 0; }.event-content .approval-note { color:#8c6a2d; }.event-content .error-note { color:#a45144; } pre { white-space:pre-wrap; overflow-wrap:anywhere; font:inherit; font-size:13px; color:#375465; background:#f5f8fa; padding:12px; border-radius:6px; }
.empty,.state { color:#82949f; text-align:center; padding:36px; font-size:13px; }.state h2 { margin-bottom:12px; }
@media(max-width:700px) { .overview { grid-template-columns:1fr; }.heading { align-items:flex-start; flex-direction:column; } .card { padding:20px; } h1 { font-size:25px; }.event-head { flex-direction:column; gap:5px; } }
</style>
<style scoped>
.back-link { margin-bottom:12px; color:#64806f; }
.progress-details > summary,.run-info > summary,.events > summary { color:#587461; cursor:pointer; font-size:13px; }
.progress-details { margin:10px 0; }
.progress-details[open] > summary { margin-bottom:12px; }
</style>
