<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { http } from '../services/http'

type Issue = { issue_type: string; severity: string; chapter_id: string; version_id: string; paragraph_index: number; description: string; rationale: string; suggestion: string; source_ids: string[] }
type Unit = { chapter_id: string; chapter_title: string; version_id: string; version_no: number; stale: boolean; status: string; error_code: string | null; result: { summary: string; issues: Issue[] } | null; reused_from?: { report_id: string; unit_id: string; call_id: string } | null }
type Report = { report_id: string; status: string; created_at: string; context_stale: boolean; units: Unit[] }
const props = defineProps<{ projectId: string; runId: string; chapterIds: { id: string; title: string }[]; revisionTick: number }>()
const emit = defineEmits<{ locate: [issue: Issue]; edit: [issue: Issue] }>()
const reports = ref<Report[]>([]), selected = ref(''), scope = ref(''), severity = ref(''), kind = ref(''), message = ref(''), busy = ref(false)
const loaded = ref(false)
const active = computed(() => reports.value.find(r => r.report_id === selected.value))
const hasCurrentFullReport = computed(() => reports.value.some(report => report.status === 'completed' && !report.context_stale && report.units.length === props.chapterIds.length && props.chapterIds.every(chapter => report.units.some(unit => unit.chapter_id === chapter.id && unit.status === 'completed' && !unit.stale && !!unit.result))))
const issues = computed(() => active.value?.units.flatMap(u => u.result?.issues ?? []).filter(i => (!severity.value || i.severity === severity.value) && (!kind.value || i.issue_type === kind.value)) ?? [])
const statusLabel: Record<string, string> = { queued: '等待中', running: '审查中', completed: '已完成', failed: '审查失败', needs_review: '需要人工核对' }
const label: Record<string, string> = { high: '高', medium: '中', low: '低', evidence: '证据', structure: '结构', logic: '逻辑', expression: '表达', completeness: '完整性' }
const base = `/projects/${encodeURIComponent(props.projectId)}/workflow-runs/${encodeURIComponent(props.runId)}/reviews`
let timer: ReturnType<typeof setTimeout> | undefined
let mounted = true
const pendingKey = `paperagent.review.${props.projectId}.${props.runId}`
async function load(): Promise<void> {
  clearTimeout(timer)
  try {
    const response = await http.get<Report[]>(base)
    if (!mounted) return
    reports.value = response.data; loaded.value = true; message.value = ''
    if (!selected.value && reports.value[0]) selected.value = reports.value[0].report_id
    if (reports.value.some(r => r.status === 'queued' || r.status === 'running')) timer = setTimeout(() => void load(), 3000)
  } catch { if (mounted) message.value = '无法读取审查报告，请稍后重试。' }
}
async function start(): Promise<void> {
  if (busy.value) return
  busy.value = true; message.value = ''
  try {
    let raw = sessionStorage.getItem(pendingKey)
    if (!raw) { raw = JSON.stringify({ report_id: crypto.randomUUID(), chapter_id: scope.value || null }); sessionStorage.setItem(pendingKey, raw) }
    const request: unknown = JSON.parse(raw)
    const response = await http.post<Report>(base, request)
    sessionStorage.removeItem(pendingKey)
    selected.value = response.data.report_id
    await load()
  } catch { message.value = '请求结果未确认；请使用相同请求重试并核对报告列表。' }
  finally { busy.value = false }
}
async function retry(): Promise<void> {
  if (!active.value || busy.value) return
  busy.value = true
  try { await http.post(`${base}/${encodeURIComponent(active.value.report_id)}/retry`); await load() }
  catch { message.value = '无法恢复审查；请核对运行状态，结果未知时不得重复调用。' }
  finally { busy.value = false }
}
onMounted(() => void load())
watch(() => props.revisionTick, () => void load())
onBeforeUnmount(() => { mounted = false; clearTimeout(timer) })
</script>

<template>
  <section class="review" aria-label="论文审查">
    <header class="review-head">
      <p class="review-kicker">AI REVIEW</p><h2>论文质量审查</h2>
      <p class="disclaimer">模型建议仅供人工核验；摘要原文匹配不代表论点已获证明。</p>
    </header>
    <p v-if="!loaded && !message" class="muted" role="status">正在加载审查报告…</p>
    <p v-if="loaded && !hasCurrentFullReport" class="review-note">当前论文暂无匹配的完整审查报告。下方可查看已有的单章或历史报告。</p>
    <details class="review-actions"><summary>开始或恢复审查</summary>
      <div class="review-actions-body">
        <label class="field">审查范围 <select v-model="scope"><option value="">全文</option><option v-for="chapter in chapterIds" :key="chapter.id" :value="chapter.id">{{ chapter.title }}</option></select></label>
        <button type="button" class="btn btn-primary" :disabled="busy" @click="start">开始审查或重试相同请求</button>
      </div>
    </details>
    <p v-if="message" class="problem" role="alert">{{ message }}</p>
    <label v-if="reports.length" class="field">报告 <select v-model="selected"><option v-for="report in reports" :key="report.report_id" :value="report.report_id">{{ new Date(report.created_at).toLocaleString() }} · {{ statusLabel[report.status] ?? "状态未知" }}{{ report.context_stale ? " · 历史快照" : "" }}</option></select></label>
    <template v-if="active">
      <p class="status-line">审查状态：<span class="status-chip" :class="`s-${active.status}`">{{ statusLabel[active.status] ?? "状态未知" }}</span> <button v-if="active.status === 'failed'" type="button" class="btn btn-sm" :disabled="busy" @click="retry">恢复失败部分</button></p>
      <p v-if="active.context_stale" class="inline-note">论文版本已变化，本报告的全文上下文属于历史快照。</p>
      <p v-if="active.status === 'needs_review'" class="inline-note">模型调用结果不确定，需要人工核对；系统不会自动重试。</p>
      <details class="units"><summary>审查进度与章节摘要</summary><ul><li v-for="unit in active.units" :key="unit.chapter_id"><span class="unit-head">{{ unit.chapter_title }} · <span class="mono">版本 {{ unit.version_no }}</span> · {{ statusLabel[unit.status] ?? "状态未知" }} <strong v-if="unit.stale">（历史版本）</strong> <span v-if="unit.error_code" class="mono"> · {{ unit.error_code }}</span></span><p v-if="unit.result" class="unit-summary">{{ unit.result.summary }}</p></li></ul></details>
      <div class="filters">
        <label class="field">严重程度 <select v-model="severity"><option value="">全部</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label>
        <label class="field">问题类型 <select v-model="kind"><option value="">全部</option><option value="evidence">证据</option><option value="structure">结构</option><option value="logic">逻辑</option><option value="expression">表达</option><option value="completeness">完整性</option></select></label>
      </div>
      <p v-if="!issues.length && active.status === 'completed'" class="empty-issues">当前筛选条件下没有问题。</p>
      <article v-for="(issue, index) in issues" :key="index" :class="[`sev-${issue.severity}`, { 'is-evidence': issue.issue_type === 'evidence' }]">
        <div class="issue-tags"><span class="tag">{{ label[issue.issue_type] ?? issue.issue_type }}</span><span class="tag" :class="issue.severity">{{ label[issue.severity] ?? issue.severity }}严重度</span></div>
        <h3>{{ active.units.find(unit => unit.chapter_id === issue.chapter_id)?.chapter_title }} · <span class="mono">第 {{ issue.paragraph_index }} 段</span></h3>
        <p class="issue-description">{{ issue.description }}</p>
        <details><summary>查看问题、依据与建议</summary><div class="issue-detail"><p>{{ issue.description }}</p><p class="rationale">依据：{{ issue.rationale }}</p><p class="suggestion">建议：{{ issue.suggestion }}</p></div></details>
        <div class="issue-actions"><button type="button" class="btn btn-sm" @click="emit('locate', issue)">定位正文</button> <button type="button" class="btn btn-sm" @click="emit('edit', issue)">修改该章</button></div>
      </article>
    </template>
  </section>
</template>
<style scoped>
.review { min-width: 0; color: var(--pa-text-body); }
.mono { font-family: var(--pa-font-mono); font-variant-numeric: tabular-nums; font-size: .92em; }

/* ---------- 头部 ---------- */
.review-head { padding-bottom: 14px; margin-bottom: 14px; border-bottom: var(--pa-border); }
.review-kicker { margin: 0; color: var(--el-color-primary); font-family: var(--pa-font-mono); font-size: 11px; font-weight: 600; letter-spacing: .14em; }
h2 { margin: 4px 0 6px; color: var(--pa-color-heading); font-size: 16px; font-weight: 600; letter-spacing: -.01em; }
.disclaimer { margin: 0; color: var(--pa-text-meta); font-size: 12px; line-height: 1.6; }
p { margin: 8px 0; font-size: 13px; line-height: 1.65; color: var(--pa-text-body); }
.muted { color: var(--pa-text-meta); }

/* ---------- 提示 ---------- */
.review-note, .inline-note { padding: 8px 12px; border-radius: var(--pa-radius-control); font-size: 12px; line-height: 1.6; color: var(--pa-color-amber); background: var(--pa-bg-amber); border-left: 3px solid var(--pa-color-amber-strong); }
.problem { padding: 8px 12px; border-radius: var(--pa-radius-control); font-size: 12px; color: var(--pa-color-danger); background: var(--pa-bg-danger); border: 1px solid var(--pa-border-danger); }

/* ---------- 折叠区 ---------- */
summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 6px; color: var(--pa-text-body); font-size: 12px; font-weight: 500; padding: 4px 0; user-select: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: ''; width: 5px; height: 5px; border-right: 1.5px solid var(--pa-text-meta); border-bottom: 1.5px solid var(--pa-text-meta); transform: rotate(-45deg); transition: transform .15s; margin-right: 2px; }
details[open] > summary::before { transform: rotate(45deg); }
summary:hover { color: var(--el-color-primary); }
summary:focus-visible { outline: 2px solid var(--el-color-primary-light-5); outline-offset: 2px; border-radius: 4px; }
.review-actions { margin: 4px 0 12px; padding: 8px 12px; border: var(--pa-border); border-radius: var(--pa-radius-card); background: var(--pa-bg-inset); }
.review-actions-body { display: flex; flex-direction: column; gap: 10px; padding: 6px 0 4px; }
.units { margin: 8px 0 12px; }
.units ul { list-style: none; padding: 0; margin: 6px 0 0; display: grid; gap: 6px; }
.units li { padding: 8px 10px; border-radius: var(--pa-radius-control); background: var(--pa-bg-inset); font-size: 12px; line-height: 1.6; color: var(--pa-text-body); }
.units li strong { color: var(--pa-color-amber); font-weight: 500; }
.unit-summary { margin: 4px 0 0; font-size: 12px; color: var(--pa-text-meta); line-height: 1.65; }

/* ---------- 表单 ---------- */
.field { display: flex; flex-direction: column; gap: 4px; margin: 10px 0 0; color: var(--pa-text-meta); font-size: 12px; font-weight: 500; }
.filters { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 4px; }
select { width: 100%; max-width: 100%; height: 32px; padding: 0 28px 0 10px; border: var(--pa-border); border-radius: var(--pa-radius-control); background: var(--pa-bg-surface); color: var(--pa-text-body); font: inherit; font-size: 13px; font-weight: 400; cursor: pointer; transition: border-color .15s, box-shadow .15s; }
select:hover { border-color: var(--el-border-color-dark); }
select:focus-visible { outline: none; border-color: var(--el-color-primary); box-shadow: var(--pa-ring-primary); }

/* ---------- 按钮 ---------- */
.btn { display: inline-flex; align-items: center; justify-content: center; height: 30px; padding: 0 12px; margin: 0; border: var(--pa-border); border-radius: var(--pa-radius-control); background: var(--pa-bg-surface); color: var(--pa-text-body); font: inherit; font-size: 12px; font-weight: 500; cursor: pointer; transition: background-color .15s, border-color .15s, color .15s; }
.btn:hover:not(:disabled) { border-color: var(--el-color-primary-light-5); color: var(--el-color-primary); background: var(--el-color-primary-light-9); }
.btn:disabled { opacity: .5; cursor: not-allowed; }
.btn:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); }
.btn-primary { align-self: flex-start; background: var(--el-color-primary); border-color: var(--el-color-primary); color: var(--pa-bg-surface); }
.btn-primary:hover:not(:disabled) { background: var(--el-color-primary-dark-2); border-color: var(--el-color-primary-dark-2); color: var(--pa-bg-surface); }
.btn-sm { height: 26px; padding: 0 10px; }

/* ---------- 审查状态 ---------- */
.status-line { margin-top: 14px; display: block; }
.status-line .btn { margin-left: 6px; vertical-align: middle; }
.status-chip { display: inline-block; padding: 0 8px; border-radius: 999px; font-size: 12px; line-height: 20px; color: var(--pa-text-meta); background: var(--pa-bg-hover); border: var(--pa-border); }
.status-chip.s-completed { color: var(--pa-color-success); background: var(--pa-bg-success); border-color: var(--pa-border-success); }
.status-chip.s-running, .status-chip.s-queued { color: var(--el-color-primary); background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-7); }
.status-chip.s-failed { color: var(--pa-color-danger); background: var(--pa-bg-danger); border-color: var(--pa-border-danger); }
.status-chip.s-needs_review { color: var(--pa-color-amber); background: var(--pa-bg-amber); border-color: var(--pa-border-amber); }

/* ---------- 问题卡片 ---------- */
article { position: relative; margin: 10px 0; padding: 12px 14px; border: var(--pa-border); border-left: 3px solid var(--el-border-color-dark); border-radius: var(--pa-radius-control); background: var(--pa-bg-surface); overflow-wrap: anywhere; transition: box-shadow .15s, border-color .15s; }
article:hover { box-shadow: var(--pa-shadow-card); }
article.sev-high { border-left-color: var(--pa-color-danger-strong); }
article.sev-medium { border-left-color: var(--pa-color-amber-strong); }
article.sev-low { border-left-color: var(--el-color-primary-light-5); }
article.is-evidence { background: var(--pa-bg-inset); }
article.is-evidence .issue-description::before { content: '“'; margin-right: 2px; color: var(--el-color-primary-light-5); font-family: Georgia, serif; font-size: 18px; line-height: 0; vertical-align: -4px; }
.issue-tags { display: flex; flex-wrap: wrap; gap: 6px; }
.tag { display: inline-block; padding: 0 7px; border-radius: 4px; font-size: 11px; line-height: 20px; color: var(--pa-text-meta); background: var(--pa-bg-hover); border: var(--pa-border); }
.tag.high { color: var(--pa-color-danger); background: var(--pa-bg-danger); border-color: var(--pa-border-danger); }
.tag.medium { color: var(--pa-color-amber); background: var(--pa-bg-amber); border-color: var(--pa-border-amber); }
.tag.low { color: var(--el-color-primary); background: var(--el-color-primary-light-9); border-color: var(--el-color-primary-light-8); }
h3 { margin: 8px 0 4px; font-size: 13px; font-weight: 600; line-height: 1.5; color: var(--pa-color-heading); }
h3 .mono { color: var(--pa-text-meta); font-weight: 400; }
.issue-description { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; margin: 4px 0 6px; font-size: 13px; line-height: 1.7; color: var(--pa-text-body); }
.issue-detail { margin-top: 4px; padding: 8px 12px; border-radius: var(--pa-radius-control); background: var(--pa-bg-inset); border: var(--pa-border); }
article.is-evidence .issue-detail { background: var(--pa-bg-surface); }
.issue-detail p { margin: 6px 0; font-size: 12px; line-height: 1.7; }
.issue-detail .suggestion { padding-left: 10px; border-left: 2px solid var(--el-color-primary-light-5); color: var(--pa-color-heading); }
.issue-actions { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.empty-issues { padding: 16px; text-align: center; color: var(--pa-text-meta); font-size: 12px; border: 1px dashed var(--pa-border-color); border-radius: var(--pa-radius-control); }
</style>
