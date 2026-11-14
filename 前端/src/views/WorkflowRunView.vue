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
    <header class="run-header">
      <div class="run-header-main">
        <RouterLink class="back-link" :to="{ name: 'project-detail', params: { projectId } }"><ArrowLeft aria-hidden="true" /> 返回项目详情 <span v-if="store.detail?.current_stage === 'writing'"> / 论文工作台</span></RouterLink>
        <div v-if="store.detail?.current_stage !== 'writing'" class="heading">
          <p class="eyebrow">WORKSPACE / PAPERAGENT</p>
          <h1 id="run-title">工作流运行监控</h1>
          <p class="heading-sub">查看本次运行状态与事件记录。</p>
        </div>
      </div>
      <span class="connection" :class="store.connection" role="status"><span class="dot"></span>{{ connectionLabels[store.connection] }}</span>
    </header>

    <ol v-if="store.detail" class="stepper" aria-label="工作流阶段">
      <template v-for="cur in [['planning', 'research', 'writing', 'review'].indexOf(store.detail.current_stage === 'outline' ? 'writing' : store.detail.current_stage === 'export' ? 'review' : (store.detail.current_stage ?? ''))]" :key="`stepper-${cur}`">
        <li
          v-for="(stage, i) in ['planning', 'research', 'writing', 'review']"
          :key="stage"
          class="step"
          :class="i < cur || (i === cur && store.detail.status === 'completed') ? 'is-done' : i !== cur ? 'is-pending' : store.detail.status === 'waiting_human' ? 'is-blocked' : store.detail.status === 'failed' ? 'is-failed' : store.detail.status === 'cancelled' || store.detail.status === 'paused' ? 'is-halted' : 'is-active'"
          :aria-current="i === cur ? 'step' : undefined"
        >
          <span class="step-index" aria-hidden="true">{{ i < cur || (i === cur && store.detail.status === 'completed') ? '✓' : i + 1 }}</span>
          <span class="step-text">
            <span class="step-name">{{ stageLabel(stage) }}</span>
            <span class="step-state">{{ i < cur || (i === cur && store.detail.status === 'completed') ? 'DONE' : i !== cur ? 'PENDING' : store.detail.status === 'waiting_human' ? 'AWAITING INPUT' : store.detail.status === 'failed' ? 'FAILED' : store.detail.status === 'cancelled' ? 'HALTED' : store.detail.status === 'paused' ? 'PAUSED' : 'ACTIVE' }}</span>
          </span>
        </li>
      </template>
    </ol>

    <div v-if="store.loading && !store.detail" class="card state" role="status">正在读取运行状态…</div>
    <div v-else-if="store.error && !store.detail" class="card state" role="alert"><h2>{{ statusTitle }}</h2><p>{{ store.error.message }}</p><ElButton :icon="Refresh" @click="store.open(projectId, runId)">重试</ElButton></div>
    <template v-else-if="store.detail">
      <div v-if="store.error" class="alert" role="alert">状态刷新失败：{{ store.error.message }}。请重试以确认最新状态。</div>
      <div v-if="store.connectionMessage" class="alert" role="status">{{ store.connectionMessage }} <button v-if="store.connection === 'error' && store.connectionMessage.includes('游标')" type="button" @click="store.replay()">从头加载事件</button><button v-else-if="store.connection === 'error'" type="button" @click="store.open(projectId, runId)">重新连接</button></div>

      <div class="run-layout" :class="{ 'is-writing': store.detail.current_stage === 'writing' }">
        <div class="run-main">
          <details v-if="store.detail.current_stage !== 'writing'" id="run-progress" open class="progress-details"><summary>生成进度与运行状态</summary><div class="overview">
            <div class="card metric"><span>运行状态</span><strong>{{ runStatusLabel(store.detail.status) }}</strong><small>以服务端状态为准</small></div>
            <div class="card metric"><span>当前阶段</span><strong>{{ stageLabel(store.detail.current_stage) }}</strong><small>第 <span class="num">{{ store.detail.attempt_no }}</span> 次执行</small></div>
            <div class="card metric"><span>开始时间（本地）</span><strong class="num">{{ formatLocalTime(store.detail.started_at || store.detail.created_at) }}</strong><small>更新时间 <span class="num">{{ formatLocalTime(store.detail.updated_at) }}</span></small></div>
          </div></details>
          <div v-if="store.detail.status === 'waiting_human'" class="notice"><span class="notice-pulse" aria-hidden="true"></span><div><strong>等待人工审核</strong><p>请检查下方审核对象并提交决定。</p></div></div>
          <WorkflowApprovalPanel v-if="store.detail.status === 'waiting_human'" :key="`${projectId}:${runId}`" :project-id="projectId" :run-id="runId" :thread-id="store.detail.thread_id" />
          <WritingDraftPanel v-if="store.detail.current_stage === 'writing'" :key="`${projectId}:${runId}`" :project-id="projectId" :run-id="runId" :running="store.detail.status === 'running'" />
          <details v-if="store.detail.current_stage === 'writing'" id="run-progress" class="progress-details"><summary>生成进度与运行状态</summary><div class="overview"><div class="card metric"><span>运行状态</span><strong>{{ runStatusLabel(store.detail.status) }}</strong></div><div class="card metric"><span>当前阶段</span><strong>{{ stageLabel(store.detail.current_stage) }}</strong></div><div class="card metric"><span>更新时间（本地）</span><strong class="num">{{ formatLocalTime(store.detail.updated_at) }}</strong></div></div></details>
          <div v-if="store.detail.status === 'failed'" class="alert" role="alert">运行失败。{{ store.detail.current_stage === 'writing' ? '请核对下方人工重试条件。' : store.detail.retryable ? '此错误允许重试。' : '当前不支持重试。' }}</div>
          <div v-if="store.detail.status === 'failed' && store.detail.current_stage === 'writing'" class="card retry-card">
            <p>可继续生成未完成的内容；结果未知的调用需要人工核查。继续生成会调用真实模型并可能产生费用。</p>
            <ElButton :loading="retrying" :disabled="retrying" @click="retryDraft">继续生成论文</ElButton>
            <p v-if="retryError" class="retry-error" role="alert">{{ retryError }}</p>
          </div>
        </div>

        <aside class="run-side">
          <details class="card run-info" :open="store.detail.current_stage !== 'writing'"><summary>运行信息</summary><dl><div><dt>运行 ID</dt><dd>{{ store.detail.run_id }}</dd></div><div><dt>工作流线程 ID</dt><dd>{{ store.detail.thread_id }}</dd></div><div v-if="store.detail.finished_at"><dt>结束时间（本地）</dt><dd>{{ formatLocalTime(store.detail.finished_at) }}</dd></div></dl></details>
          <details class="card events" :open="store.detail.current_stage !== 'writing'"><summary>执行时间线</summary><div class="events-head"><p>展示当前会话中收到的事件；运行状态以服务端查询为准。</p><span class="cursor">游标 {{ store.lastSequence || '—' }}</span></div>
            <div v-if="!store.timeline.length" class="empty">暂无事件，正在等待工作流更新。</div>
            <ol v-else class="timeline">
              <li v-for="item in store.timeline" :key="item.event_id" :class="`ev-${item.event_type}`">
                <span class="event-dot"></span>
                <div class="event-content"><div class="event-head"><strong>{{ eventLabel(item) }}</strong><time>{{ formatLocalTime(item.timestamp) }}</time></div>
                  <p class="event-meta">{{ stageLabel(item.stage) }}<template v-if="item.agent_name"> · {{ item.agent_name }}</template><template v-if="item.sequence"> · #{{ item.sequence }}</template></p>
                  <p v-if="item.event_type === 'approval_required'" class="approval-note">请在上方审核区域提交决定。</p>
                  <pre v-if="item.event_type === 'text_delta'">{{ item.payload.delta }}</pre>
                  <p v-if="item.event_type === 'completed'">完成时间：{{ formatLocalTime(item.payload.finished_at) }}</p>
                  <p v-if="item.event_type === 'error'" class="error-note">工作流发生错误。{{ item.payload.retryable ? '此错误允许重试。' : '当前不支持重试。' }}</p>
                </div>
              </li>
            </ol>
          </details>
        </aside>
      </div>
    </template>
  </section>
</template>

<style scoped>
/* ---------- 页面骨架 ---------- */
.run-page { max-width: 1600px; margin: 0 auto; color: var(--pa-text-body); }
.num, time, .cursor, dd { font-family: var(--pa-font-mono); font-variant-numeric: tabular-nums; }

.run-header { display: flex; align-items: flex-end; justify-content: space-between; gap: 20px; margin-bottom: 16px; }
.run-header-main { min-width: 0; }
.back-link { display: inline-flex; align-items: center; gap: 6px; color: var(--pa-text-meta); font-size: 13px; text-decoration: none; border-radius: var(--pa-radius-control); padding: 4px 8px 4px 4px; margin-left: -4px; transition: color .15s, background-color .15s; }
.back-link:hover { color: var(--el-color-primary); background: var(--pa-bg-hover); }
.back-link svg { width: 14px; height: 14px; }
.heading { margin-top: 12px; }
.eyebrow { margin: 0 0 6px; color: var(--pa-text-meta); font-family: var(--pa-font-mono); font-size: 11px; font-weight: 600; letter-spacing: .14em; }
h1 { margin: 0; color: var(--pa-color-heading); font-size: 24px; font-weight: 600; letter-spacing: -.02em; line-height: 1.3; }
.heading-sub { margin: 6px 0 0; color: var(--pa-text-meta); font-size: 13px; line-height: 1.5; }

.connection { display: inline-flex; align-items: center; gap: 8px; flex: none; border: var(--pa-border); border-radius: 999px; background: var(--pa-bg-surface); color: var(--pa-text-meta); padding: 5px 12px; font-size: 12px; white-space: nowrap; box-shadow: var(--pa-shadow-card); }
.connection .dot { width: 7px; height: 7px; border-radius: 50%; background: var(--pa-text-faint); }
.connection.connected { color: var(--pa-color-success); border-color: var(--pa-border-success); }
.connection.connected .dot { background: var(--pa-color-success-dot); animation: pa-breathe 2s ease-in-out infinite; }
.connection.connecting .dot, .connection.reconnecting .dot { background: var(--pa-color-amber-strong); animation: pa-breathe 1.2s ease-in-out infinite; }
.connection.error { color: var(--pa-color-danger); border-color: var(--pa-border-danger); background: var(--pa-bg-danger); }
.connection.error .dot { background: var(--pa-color-danger-strong); }

/* ---------- 阶段条 Stepper ---------- */
.stepper { list-style: none; margin: 0 0 20px; padding: 6px; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 6px; background: var(--pa-bg-surface); border: var(--pa-border); border-radius: var(--pa-radius-card); box-shadow: var(--pa-shadow-card); }
.step { position: relative; display: flex; align-items: center; gap: 10px; min-width: 0; padding: 10px 12px; border-radius: var(--pa-radius-control); border: 1px solid transparent; transition: background-color .2s, border-color .2s; }
.step:not(:last-child)::after { content: ''; position: absolute; right: -4px; top: 50%; width: 2px; height: 16px; transform: translateY(-50%); border-radius: 2px; background: var(--pa-border-color); }
.step-index { display: grid; place-items: center; flex: none; width: 24px; height: 24px; border-radius: 50%; font-family: var(--pa-font-mono); font-size: 12px; font-weight: 600; font-variant-numeric: tabular-nums; border: 1px solid var(--pa-border-color); background: var(--pa-bg-surface); color: var(--pa-text-faint); }
.step-text { display: flex; flex-direction: column; min-width: 0; }
.step-name { font-size: 13px; font-weight: 600; color: var(--pa-text-faint); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.step-state { margin-top: 2px; font-family: var(--pa-font-mono); font-size: 10px; letter-spacing: .08em; color: var(--pa-text-faint); }
.step.is-done .step-index { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); color: var(--el-color-primary); }
.step.is-done .step-name { color: var(--pa-text-body); }
.step.is-done .step-state { color: var(--pa-text-meta); }
.step.is-active { background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.step.is-active .step-index { background: var(--el-color-primary); border-color: var(--el-color-primary); color: var(--pa-bg-surface); animation: pa-ring 2s ease-out infinite; }
.step.is-active .step-name { color: var(--pa-color-heading); }
.step.is-active .step-state { color: var(--el-color-primary); }
.step.is-blocked { background: var(--pa-bg-amber); border-color: var(--pa-border-amber); }
.step.is-blocked .step-index { background: var(--pa-color-amber-strong); border-color: var(--pa-color-amber-strong); color: var(--pa-bg-surface); animation: pa-ring-amber 2s ease-out infinite; }
.step.is-blocked .step-name { color: var(--pa-color-heading); }
.step.is-blocked .step-state { color: var(--pa-color-amber); }
.step.is-failed { background: var(--pa-bg-danger); border-color: var(--pa-border-danger); }
.step.is-failed .step-index { background: var(--pa-color-danger-strong); border-color: var(--pa-color-danger-strong); color: var(--pa-bg-surface); }
.step.is-failed .step-name { color: var(--pa-color-heading); }
.step.is-failed .step-state { color: var(--pa-color-danger); }
.step.is-halted { background: var(--pa-bg-inset); border-color: var(--pa-border-color); }
.step.is-halted .step-index { color: var(--pa-text-meta); border-color: var(--el-border-color-dark); }
.step.is-halted .step-name { color: var(--pa-text-body); }
.step.is-halted .step-state { color: var(--pa-text-meta); }

@keyframes pa-breathe { 0%, 100% { opacity: 1; } 50% { opacity: .35; } }
@keyframes pa-ring { 0% { box-shadow: 0 0 0 0 rgba(37, 99, 235, .35); } 70%, 100% { box-shadow: 0 0 0 6px rgba(37, 99, 235, 0); } }
@keyframes pa-ring-amber { 0% { box-shadow: 0 0 0 0 rgba(217, 119, 6, .4); } 70%, 100% { box-shadow: 0 0 0 6px rgba(217, 119, 6, 0); } }
@media (prefers-reduced-motion: reduce) { .step-index, .dot, .notice-pulse { animation: none !important; } }

/* ---------- 双栏布局 ---------- */
.run-layout { display: grid; grid-template-columns: minmax(0, 1fr) minmax(300px, 380px); gap: 20px; align-items: start; }
.run-layout.is-writing { grid-template-columns: minmax(0, 1fr); gap: 0; }
.run-main, .run-side { min-width: 0; display: flex; flex-direction: column; gap: var(--pa-space-block); }
.run-layout.is-writing .run-side { margin-top: var(--pa-space-block); }

/* ---------- 卡片 ---------- */
.card { border: var(--pa-border); border-radius: var(--pa-radius-card); background: var(--pa-bg-surface); box-shadow: var(--pa-shadow-card); padding: 20px var(--pa-space-card); }
h2 { color: var(--pa-color-heading); font-size: 16px; font-weight: 600; margin: 0; }

details > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 8px; color: var(--pa-color-heading); font-size: 14px; font-weight: 600; user-select: none; }
details > summary::-webkit-details-marker { display: none; }
details > summary::before { content: ''; width: 6px; height: 6px; border-right: 1.5px solid var(--pa-text-meta); border-bottom: 1.5px solid var(--pa-text-meta); transform: rotate(-45deg); transition: transform .15s; margin-right: 2px; }
details[open] > summary::before { transform: rotate(45deg); }
details > summary:hover { color: var(--el-color-primary); }

.progress-details > summary { font-size: 13px; color: var(--pa-text-meta); font-weight: 500; }
.progress-details[open] > summary { margin-bottom: 12px; }
.overview { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; }
.metric { padding: 16px 20px; }
.metric span, .metric strong, .metric small { display: block; }
.metric > span { color: var(--pa-text-meta); font-size: 12px; }
.metric strong { color: var(--pa-color-heading); font-size: 17px; font-weight: 600; margin: 8px 0 6px; line-height: 1.35; overflow-wrap: anywhere; }
.metric strong.num { font-size: 15px; }
.metric small { color: var(--pa-text-faint); font-size: 12px; }
.metric small .num { display: inline; }

/* ---------- 提示条 ---------- */
.notice, .alert { padding: 12px 16px; border-radius: var(--pa-radius-card); font-size: 13px; line-height: 1.6; }
.alert { margin-bottom: 12px; color: var(--pa-color-danger); background: var(--pa-bg-danger); border: 1px solid var(--pa-border-danger); }
.run-main .alert { margin-bottom: 0; }
.alert button { color: inherit; border: 0; padding: 0 0 0 6px; text-decoration: underline; text-underline-offset: 3px; background: none; cursor: pointer; font-weight: 600; }
.notice { display: flex; align-items: flex-start; gap: 12px; color: var(--pa-color-amber); background: var(--pa-bg-amber); border: 1px solid var(--pa-border-amber); }
.notice strong { color: var(--pa-color-heading); font-weight: 600; }
.notice p { margin: 2px 0 0; color: var(--pa-color-amber); }
.notice-pulse { flex: none; width: 8px; height: 8px; margin-top: 7px; border-radius: 50%; background: var(--pa-color-amber-strong); animation: pa-ring-amber 2s ease-out infinite; }

.retry-card { display: flex; flex-direction: column; align-items: flex-start; gap: 12px; border-left: 3px solid var(--pa-color-amber-strong); }
.retry-card p { margin: 0; font-size: 14px; line-height: 1.65; color: var(--pa-text-body); }
.retry-card .retry-error { color: var(--pa-color-danger); font-size: 13px; }

/* ---------- 运行信息 ---------- */
.run-info dl { display: grid; gap: 14px; margin: 16px 0 0; }
.run-info dt { color: var(--pa-text-meta); font-size: 12px; }
.run-info dd { margin: 4px 0 0; padding: 6px 10px; background: var(--pa-bg-inset); border: var(--pa-border); border-radius: var(--pa-radius-control); color: var(--pa-text-body); font-size: 12px; overflow-wrap: anywhere; }

/* ---------- 时间线 ---------- */
.events-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-top: 12px; }
.events-head p { margin: 0; color: var(--pa-text-meta); font-size: 12px; line-height: 1.55; }
.cursor { flex: none; color: var(--pa-text-meta); font-size: 11px; padding: 2px 8px; border: var(--pa-border); border-radius: 999px; background: var(--pa-bg-inset); white-space: nowrap; }
.timeline { padding: 0; margin: 18px 0 0; list-style: none; }
.timeline li { position: relative; display: flex; gap: 14px; padding: 0 0 18px; }
.timeline li::before { content: ''; position: absolute; left: 4px; top: 14px; bottom: 0; width: 1px; background: var(--pa-border-color); }
.timeline li:last-child { padding-bottom: 0; }
.timeline li:last-child::before { display: none; }
.event-dot { position: relative; z-index: 1; flex: none; width: 9px; height: 9px; margin-top: 5px; border-radius: 50%; background: var(--pa-bg-surface); border: 2px solid var(--el-color-primary-light-5); }
.ev-approval_required .event-dot { border-color: var(--pa-color-amber-strong); background: var(--pa-bg-amber); }
.ev-completed .event-dot { border-color: var(--pa-color-success-dot); background: var(--pa-bg-success); }
.ev-error .event-dot { border-color: var(--pa-color-danger-strong); background: var(--pa-bg-danger); }
.ev-heartbeat .event-dot { border-color: var(--el-border-color-dark); }
.event-content { min-width: 0; flex: 1; }
.event-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.event-head strong { font-size: 13px; font-weight: 600; color: var(--pa-color-heading); }
.event-head time { flex: none; color: var(--pa-text-faint); font-size: 11px; }
.event-content p { margin: 4px 0 0; color: var(--pa-text-meta); font-size: 12px; line-height: 1.55; }
.event-content .approval-note { color: var(--pa-color-amber); }
.event-content .error-note { color: var(--pa-color-danger); }
pre { margin: 8px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; font-family: var(--pa-font-mono); font-size: 12px; line-height: 1.6; color: var(--pa-text-body); background: var(--pa-bg-inset); border: var(--pa-border); border-left: 3px solid var(--el-color-primary-light-5); padding: 10px 12px; border-radius: var(--pa-radius-control); }

.empty, .state { color: var(--pa-text-meta); text-align: center; padding: 36px; font-size: 13px; }
.empty { padding: 28px 12px; }
.state h2 { margin-bottom: 8px; }
.state p { margin: 0 0 16px; }

/* ---------- Element Plus 覆写 ---------- */
.run-page :deep(.el-button) { border-radius: var(--pa-radius-control); font-weight: 500; height: 34px; padding: 0 16px; transition: background-color .15s, border-color .15s, color .15s, box-shadow .15s; }
.run-page :deep(.el-button:not(.is-disabled):hover) { box-shadow: var(--pa-shadow-card); }
.run-page :deep(.el-button:focus-visible) { outline: none; box-shadow: var(--pa-ring-primary); }
.run-page :deep(.el-tag) { border-radius: 4px; border-color: var(--pa-border-color); font-weight: 500; padding: 0 8px; }
.run-page :deep(.el-dialog) { border-radius: var(--pa-radius-card); border: var(--pa-border); box-shadow: var(--pa-shadow-float); padding: var(--pa-space-card); }
.run-page :deep(.el-table) { border-radius: var(--pa-radius-card); overflow: hidden; border: var(--pa-border); }
.run-page :deep(.el-collapse), .run-page :deep(.el-collapse-item__header), .run-page :deep(.el-collapse-item__wrap) { border-color: var(--pa-border-color); }
.run-page :deep(.el-tabs__item) { color: var(--pa-text-meta); font-weight: 500; transition: color .15s; }
.run-page :deep(.el-tabs__item.is-active), .run-page :deep(.el-tabs__item:hover) { color: var(--el-color-primary); }
.run-page :deep(.el-timeline-item__tail) { border-left-color: var(--pa-border-color); }

/* ---------- 焦点 ---------- */
.alert button:focus-visible, details > summary:focus-visible { outline: 2px solid var(--el-color-primary-light-5); outline-offset: 2px; border-radius: 4px; }

/* ---------- 响应式 ---------- */
@media (max-width: 1100px) { .run-layout { grid-template-columns: minmax(0, 1fr); } }
@media (max-width: 760px) {
  .run-header { align-items: flex-start; flex-direction: column; gap: 12px; }
  .stepper { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .step:nth-child(2)::after { display: none; }
  .overview { grid-template-columns: 1fr; }
  .card { padding: 16px 18px; }
  h1 { font-size: 21px; }
  .event-head { flex-direction: column; gap: 2px; }
}
</style>
