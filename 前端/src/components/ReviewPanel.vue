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
    <p class="review-kicker">AI REVIEW</p><h2>论文质量审查</h2>
    <p>模型建议仅供人工核验；摘要原文匹配不代表论点已获证明。</p>
    <p v-if="!loaded && !message" role="status">正在加载审查报告…</p>
    <p v-if="loaded && !hasCurrentFullReport" class="review-note">当前论文暂无匹配的完整审查报告。下方可查看已有的单章或历史报告。</p>
    <details class="review-actions"><summary>开始或恢复审查</summary>
    <label>审查范围 <select v-model="scope"><option value="">全文</option><option v-for="chapter in chapterIds" :key="chapter.id" :value="chapter.id">{{ chapter.title }}</option></select></label>
    <button type="button" :disabled="busy" @click="start">开始审查或重试相同请求</button>
    </details>
    <p v-if="message" role="alert">{{ message }}</p>
    <label v-if="reports.length">报告 <select v-model="selected"><option v-for="report in reports" :key="report.report_id" :value="report.report_id">{{ new Date(report.created_at).toLocaleString() }} · {{ statusLabel[report.status] ?? "状态未知" }}{{ report.context_stale ? " · 历史快照" : "" }}</option></select></label>
    <template v-if="active">
      <p>审查状态：{{ statusLabel[active.status] ?? "状态未知" }} <button v-if="active.status === 'failed'" type="button" :disabled="busy" @click="retry">恢复失败部分</button></p>
      <p v-if="active.context_stale">论文版本已变化，本报告的全文上下文属于历史快照。</p>
      <p v-if="active.status === 'needs_review'">模型调用结果不确定，需要人工核对；系统不会自动重试。</p>
      <details><summary>审查进度与章节摘要</summary><ul><li v-for="unit in active.units" :key="unit.chapter_id">{{ unit.chapter_title }} · 版本 {{ unit.version_no }} · {{ statusLabel[unit.status] ?? "状态未知" }} <strong v-if="unit.stale">（历史版本）</strong> <span v-if="unit.error_code"> · {{ unit.error_code }}</span><p v-if="unit.result">{{ unit.result.summary }}</p></li></ul></details>
      <label>严重程度 <select v-model="severity"><option value="">全部</option><option value="high">高</option><option value="medium">中</option><option value="low">低</option></select></label>
      <label>问题类型 <select v-model="kind"><option value="">全部</option><option value="evidence">证据</option><option value="structure">结构</option><option value="logic">逻辑</option><option value="expression">表达</option><option value="completeness">完整性</option></select></label>
      <p v-if="!issues.length && active.status === 'completed'" class="empty-issues">当前筛选条件下没有问题。</p>
      <article v-for="(issue, index) in issues" :key="index"><div class="issue-tags"><span>{{ label[issue.issue_type] ?? issue.issue_type }}</span><span :class="issue.severity">{{ label[issue.severity] ?? issue.severity }}严重度</span></div><h3>{{ active.units.find(unit => unit.chapter_id === issue.chapter_id)?.chapter_title }} · 第 {{ issue.paragraph_index }} 段</h3><p class="issue-description">{{ issue.description }}</p><details><summary>查看问题、依据与建议</summary><p>{{ issue.description }}</p><p>依据：{{ issue.rationale }}</p><p>建议：{{ issue.suggestion }}</p></details><button type="button" @click="emit('locate', issue)">定位正文</button> <button type="button" @click="emit('edit', issue)">修改该章</button></article>
    </template>
  </section>
</template>
<style scoped>
.review { min-width:0; } .review-kicker { color:#81998a; font-size:10px; letter-spacing:.15em; font-weight:700; } h2 { font-size:17px; color:#293f32; margin:4px 0 10px; } summary { cursor:pointer; color:#4d725c; font-size:12px; margin:10px 0; } ul { padding-left:18px; }
p,li,label { color:#506b7b; font-size:13px; line-height:1.7; }
label { display:block; margin:12px 0 5px; } button { margin:5px 5px 5px 0; }
select,button { border:1px solid #b9cbd6; border-radius:5px; background:white; color:#23455a; padding:6px 10px; max-width:100%; }
button { cursor:pointer; } button:hover { border-color:#34779b; } button:disabled { opacity:.6; cursor:not-allowed; }
article { border:1px solid #e5ece6; border-radius:8px; padding:12px; margin:12px 0; background:#fbfcfb; overflow-wrap:anywhere; }
h3 { font-size:14px; color:#23455a; }
.review-note { background:#fbf7eb; color:#806c3e; border-radius:6px; padding:9px; font-size:12px; }
.review-actions { margin:8px 0; }
.issue-tags { display:flex; flex-wrap:wrap; gap:6px; }
.issue-tags span { background:#edf4ee; color:#4b7758; padding:3px 7px; border-radius:4px; font-size:11px; }
.issue-tags .high { background:#fbece8; color:#a44f40; }
.issue-tags .medium { background:#fcf4e6; color:#997136; }
.issue-description { display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; overflow:hidden; }
.review article h3 { font-size:12px; line-height:1.55; color:#536a5b; margin:9px 0; }
.review select { width:100%; color:#3b5847; border-color:#dbe7dd; }
.review button { color:#39684b; border-color:#dce8df; }
.review button:hover { border-color:#7da38a; }
.empty-issues { color:#8b9a8f; }
</style>
