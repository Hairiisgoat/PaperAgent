<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { getProject } from '../services/projects'
import ReviewPanel from './ReviewPanel.vue'
import { downloadExport, getChapterRevision, getChapterVersions, getDraft, restoreChapterVersion, startChapterRevision } from '../services/workflows'
import { HttpError } from '../services/http'
import type { ChapterVersion, DraftChapter, DraftParagraph, RevisionMode, RevisionRequest, RevisionResult, WritingDraft } from '../types/workflows'

const props = defineProps<{ projectId: string; runId: string; running: boolean }>()
const draft = ref<WritingDraft | null>(null)
const title = ref('论文初稿')
const view = ref<'reading' | 'review' | 'versions'>('reading')
const showContents = ref(true)
const showReview = ref(true)
const activeChapter = ref('')
const citation = ref<DraftParagraph['citations'][number] | null>(null)
const citationNumbers = computed(() => {
  const numbers = new Map<string, number>()
  for (const chapter of draft.value?.chapters ?? []) for (const paragraph of paragraphs(chapter)) for (const item of paragraph.citations) {
    if (!numbers.has(item.source_id)) numbers.set(item.source_id, numbers.size + 1)
  }
  return numbers
})
function navigateChapter(id: string): void {
  activeChapter.value = id
  view.value = 'reading'
  void nextTick(() => document.getElementById(`chapter-${id}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' }))
}
function showProgress(): void {
  const panel = document.querySelector<HTMLDetailsElement>('#run-progress')
  if (panel) { panel.open = true; panel.scrollIntoView({ behavior: 'smooth', block: 'start' }) }
}
const error = ref('')
const exportBusy = ref(false)
const exportError = ref('')
async function exportFile(kind: 'docx' | 'pdf' | 'review-docx'): Promise<void> {
  if (exportBusy.value) return
  exportBusy.value = true; exportError.value = ''
  try { await downloadExport(props.projectId, props.runId, kind) }
  catch (error) {
    exportError.value = error instanceof HttpError && error.status === 409
      ? (kind === 'review-docx' ? '当前论文没有匹配的完整审查报告。' : '论文尚未完成或引用资料不完整。')
      : error instanceof HttpError && error.status === 503 && kind === 'pdf'
        ? 'PDF 生成暂不可用，请检查服务器中文字体或稍后重试。'
        : error instanceof HttpError ? error.message : '导出失败，请稍后重试。'
  } finally { exportBusy.value = false }
}
let controller = new AbortController()
let timer: ReturnType<typeof setTimeout> | undefined
let mounted = true
let requestVersion = 0
let chapterObserver: IntersectionObserver | undefined
const histories = ref<Record<string, ChapterVersion[]>>({})
const selected = ref<Record<string, string>>({})
const editing = ref<Record<string, boolean>>({})
const modes = ref<Record<string, RevisionMode>>({})
const instructions = ref<Record<string, string>>({})
const busy = ref<Record<string, boolean>>({})
const notices = ref<Record<string, string>>({})
const pending = ref<Record<string, RevisionRequest>>({})
const restorePending = ref<Record<string, { version: string; requestId: string }>>({})
const revisionTick = ref(0)
const timers = new Map<string, ReturnType<typeof setTimeout>>()
const pendingKey = (chapter: string) => `paperagent.revision.${props.projectId}.${props.runId}.${chapter}`
const restoreKey = (chapter: string) => `paperagent.restore.${props.projectId}.${props.runId}.${chapter}`
type ReviewLocation = { chapter_id: string; version_id: string; paragraph_index: number; suggestion: string }
async function locateReview(issue: ReviewLocation): Promise<void> {
  view.value = 'reading'
  activeChapter.value = issue.chapter_id
  selected.value[issue.chapter_id] = issue.version_id
  await history(issue.chapter_id)
  if (!histories.value[issue.chapter_id]?.some(version => version.version_id === issue.version_id)) {
    notices.value[issue.chapter_id] = '无法读取该审查对应的章节版本，请重试。'
    return
  }
  await nextTick()
  document.getElementById(`paragraph-${issue.chapter_id}-${issue.paragraph_index}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}
async function editFromReview(issue: ReviewLocation): Promise<void> {
  await edit(issue.chapter_id)
  await locateReview(issue)
  if (!pending.value[issue.chapter_id]) instructions.value[issue.chapter_id] = issue.suggestion
  notices.value[issue.chapter_id] = '修改将基于当前最新章节版本；请核对审查报告对应版本。'
}
function paragraphs(chapter: DraftChapter): DraftParagraph[] {
  return histories.value[chapter.chapter_id]?.find(item => item.version_id === selected.value[chapter.chapter_id])?.paragraphs ?? chapter.paragraphs
}
async function history(chapterId: string): Promise<void> {
  try {
    const versions = await getChapterVersions(props.projectId, props.runId, chapterId)
    if (!mounted || versions.some(item => item.chapter_id !== chapterId)) return
    histories.value[chapterId] = versions
    if (!selected.value[chapterId] || !versions.some(item => item.version_id === selected.value[chapterId])) selected.value[chapterId] = versions[0]?.version_id ?? ''
    notices.value[chapterId] = ''
  } catch { if (mounted) notices.value[chapterId] = '无法读取章节版本，请稍后重试。' }
}
async function watchRevision(chapterId: string, revisionId: string): Promise<void> {
  try {
    const job: RevisionResult = await getChapterRevision(props.projectId, props.runId, chapterId, revisionId)
    if (!mounted) return
    if (job.chapter_id !== chapterId || job.revision_id !== revisionId) throw new Error('scope')
    if (job.status === 'queued' || job.status === 'running') {
      notices.value[chapterId] = '正在修改章节…'
      timers.set(chapterId, setTimeout(() => void watchRevision(chapterId, revisionId), 3000))
      return
    }
    busy.value[chapterId] = false
    if (job.status === 'completed') {
      delete pending.value[chapterId]
      try { sessionStorage.removeItem(pendingKey(chapterId)) } catch { /* unavailable */ }
      selected.value[chapterId] = ''
      await history(chapterId)
      await load()
      revisionTick.value++
      notices.value[chapterId] = '章节修改完成，原版本已保留。'
    } else {
      notices.value[chapterId] = job.status === 'needs_review' ? '修改结果需要核对；原版本保持不变。' : '修改未成功；原版本保持不变。'
      if (job.status === 'failed') {
        delete pending.value[chapterId]
        try { sessionStorage.removeItem(pendingKey(chapterId)) } catch { /* unavailable */ }
      }
    }
  } catch { if (mounted) { busy.value[chapterId] = false; notices.value[chapterId] = '修改状态未确认，请重试相同请求或核对运行状态。' } }
}
async function revise(chapterId: string): Promise<void> {
  if (busy.value[chapterId]) return
  const versions = histories.value[chapterId] ?? []
  const instruction = (instructions.value[chapterId] ?? '').trim()
  let request = pending.value[chapterId]
  if (!request) {
    if (!versions[0] || !instruction || instruction.length > 2000) { notices.value[chapterId] = '请填写修改要求（最多 2000 字）。'; return }
    request = { revision_id: crypto.randomUUID(), base_version_id: versions[0].version_id, mode: modes.value[chapterId] ?? 'rewrite', instruction }
    try { sessionStorage.setItem(pendingKey(chapterId), JSON.stringify(request)) } catch { notices.value[chapterId] = '无法保存修改请求，请检查浏览器会话存储。'; return }
    pending.value[chapterId] = request
  }
  busy.value[chapterId] = true; notices.value[chapterId] = ''
  try {
    const job = await startChapterRevision(props.projectId, props.runId, chapterId, request)
    if (job.chapter_id !== chapterId || job.revision_id !== request.revision_id) throw new Error('scope')
    await watchRevision(chapterId, request.revision_id)
  } catch { if (mounted) { busy.value[chapterId] = false; notices.value[chapterId] = '修改请求结果未确认；只能重试相同请求，请先核对运行状态。' } }
}
async function restore(chapterId: string, versionId: string): Promise<void> {
  if (busy.value[chapterId] || (restorePending.value[chapterId] && restorePending.value[chapterId].version !== versionId)) return
  if (!restorePending.value[chapterId] && !window.confirm('恢复此历史版本将生成新的当前版本，其他历史版本会保留。确定继续吗？')) return
  const request = restorePending.value[chapterId] ?? { version: versionId, requestId: crypto.randomUUID() }
  try { sessionStorage.setItem(restoreKey(chapterId), JSON.stringify(request)) } catch { notices.value[chapterId] = '无法保存恢复请求，请检查浏览器会话存储。'; return }
  restorePending.value[chapterId] = request; busy.value[chapterId] = true
  try {
    await restoreChapterVersion(props.projectId, props.runId, chapterId, versionId, request.requestId)
    delete restorePending.value[chapterId]; selected.value[chapterId] = ''
    try { sessionStorage.removeItem(restoreKey(chapterId)) } catch { /* unavailable */ }
    await history(chapterId); await load()
    revisionTick.value++
    notices.value[chapterId] = '历史版本已恢复，其他版本仍然保留。'
  } catch { notices.value[chapterId] = '恢复结果未确认，请重试相同版本。' }
  finally { busy.value[chapterId] = false }
}
async function edit(chapterId: string): Promise<void> {
  editing.value[chapterId] = true
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(pendingKey(chapterId)) ?? 'null')
    if (typeof value === 'object' && value !== null && 'revision_id' in value && 'base_version_id' in value && 'mode' in value && 'instruction' in value
        && typeof value.revision_id === 'string' && typeof value.base_version_id === 'string' && typeof value.instruction === 'string'
        && (value.mode === 'expand' || value.mode === 'condense' || value.mode === 'rewrite' || value.mode === 'restructure')) {
      pending.value[chapterId] = { revision_id: value.revision_id, base_version_id: value.base_version_id, mode: value.mode, instruction: value.instruction }
      instructions.value[chapterId] = value.instruction; modes.value[chapterId] = value.mode
    }
    const restoreValue: unknown = JSON.parse(sessionStorage.getItem(restoreKey(chapterId)) ?? 'null')
    if (typeof restoreValue === 'object' && restoreValue !== null && 'version' in restoreValue && 'requestId' in restoreValue
        && typeof restoreValue.version === 'string' && typeof restoreValue.requestId === 'string') {
      restorePending.value[chapterId] = { version: restoreValue.version, requestId: restoreValue.requestId }
      selected.value[chapterId] = restoreValue.version
    }
  } catch { notices.value[chapterId] = '待确认的修改请求无法读取，请先核对运行状态。'; return }
  await history(chapterId)
}
async function load(): Promise<void> {
  const version = ++requestVersion
  try {
    const value = await getDraft(props.projectId, props.runId, controller.signal)
    if (!mounted || version !== requestVersion) return
    if (value.project_id !== props.projectId || value.run_id !== props.runId) throw new Error('scope')
    draft.value = value; error.value = ''
  } catch {
    if (mounted && version === requestVersion) error.value = '暂时无法读取初稿，请稍后刷新。'
  } finally {
    if (mounted && version === requestVersion && props.running) timer = setTimeout(() => void load(), 4000)
  }
}
onMounted(() => { void load(); void getProject(props.projectId).then(project => { if (mounted) title.value = project.title }).catch(() => {}) })
watch(() => props.running, () => { clearTimeout(timer); void load() })
watch([draft, view], async () => {
  await nextTick()
  chapterObserver?.disconnect()
  if (view.value !== 'reading' || !('IntersectionObserver' in window)) return
  chapterObserver = new IntersectionObserver(entries => {
    const entry = entries.filter(item => item.isIntersecting).sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0]
    if (entry) activeChapter.value = entry.target.id.replace(/^chapter-/, '')
  }, { rootMargin: '-10% 0px -65% 0px' })
  document.querySelectorAll('.paper article[id^="chapter-"]').forEach(element => chapterObserver?.observe(element))
})
onBeforeUnmount(() => { mounted = false; requestVersion++; controller.abort(); clearTimeout(timer); timers.forEach(clearTimeout); chapterObserver?.disconnect() })
</script>

<template>
  <section class="draft" aria-label="论文初稿">
    <header class="workbench-bar">
      <div class="workspace-header"><p class="workspace-name">论文工作台</p><p v-if="draft" class="workspace-status" role="status">{{ draft.status === 'completed' ? '初稿已完成' : draft.status === 'writing' ? '正在生成' : draft.status === 'failed' || draft.status === 'needs_review' ? '需要检查' : '准备中' }} · <span class="num">{{ draft.chapters.filter(chapter => chapter.status === 'completed').length }} / {{ draft.chapters.length }}</span> 章完成</p></div>
      <nav class="tabs" aria-label="工作台视图"><button type="button" :class="{active: view === 'reading'}" @click="view = 'reading'">论文阅读</button><button type="button" :class="{active: view === 'review'}" @click="view = 'review'">质量审查</button><button type="button" :class="{active: view === 'versions'}" @click="view = 'versions'">版本历史</button><button type="button" class="tab-aux" @click="showProgress">生成进度</button></nav>
      <div class="bar-actions">
        <div v-if="!error && draft" class="workspace-controls"><button type="button" @click="showContents = !showContents">{{ showContents ? '收起目录' : '显示目录' }}</button><button type="button" @click="showReview = !showReview">{{ showReview ? '收起审查' : '显示审查' }}</button></div>
        <div v-if="draft?.status === 'completed'" class="tools export-tools" role="group" aria-label="导出">
          <button type="button" :disabled="exportBusy" @click="exportFile('docx')">下载论文 Word</button>
          <button type="button" :disabled="exportBusy" @click="exportFile('pdf')">下载论文 PDF</button>
          <button type="button" :disabled="exportBusy" @click="exportFile('review-docx')">下载审查报告</button>
        </div>
      </div>
    </header>
    <p v-if="draft?.status === 'completed' && exportError" class="bar-alert" role="alert">{{ exportError }}</p>
    <p v-if="error" class="bar-alert" role="alert">{{ error }}</p>
    <p v-else-if="!draft" class="loading" role="status">正在读取初稿…</p>
    <template v-else>
      <div class="workspace-grid" :class="{'hide-contents': !showContents, 'hide-review': !showReview || view === 'versions', 'review-view': view === 'review'}">
      <aside v-show="showContents" class="contents"><p class="aside-title">论文目录</p><button v-for="chapter in draft.chapters" :key="chapter.chapter_id" type="button" class="chapter-link" :class="[{selected: activeChapter === chapter.chapter_id}, `st-${chapter.status}`]" @click="navigateChapter(chapter.chapter_id)"><span class="toc-no">{{ String(chapter.position).padStart(2, '0') }}</span><span class="toc-text">{{ chapter.title }}<small>{{ chapter.status === 'completed' ? '已完成' : chapter.status === 'writing' ? '生成中' : chapter.status === 'failed' || chapter.status === 'needs_review' ? '需要检查' : '等待生成' }}</small></span></button></aside>
      <div v-show="view !== 'review'" class="paper">
        <div class="paper-intro"><p class="eyebrow">RESEARCH PAPER</p><h2>{{ view === 'versions' ? '章节与版本' : title }}</h2><p class="paper-sub">{{ view === 'versions' ? '选择章节，查看版本或提出修改。' : '论文初稿 · 请核验论述与引用证据' }}</p></div>
        <div v-if="view === 'versions'" class="version-list"><div v-for="chapter in draft.chapters" :key="chapter.chapter_id"><span class="vl-title"><span class="num">{{ chapter.position }}.</span> {{ chapter.title }}</span><button type="button" @click="edit(chapter.chapter_id); navigateChapter(chapter.chapter_id)">查看版本与修改</button></div></div>
        <template v-else>
        <p v-if="!draft.chapters.length && (draft.status === 'failed' || draft.status === 'needs_review')" class="paper-alert" role="alert">大纲生成未完成，请检查运行状态；系统不会自动重试。</p>
        <p v-else-if="!draft.chapters.length" class="loading">正在生成论文大纲…</p>
        <article v-for="chapter in draft.chapters" :id="`chapter-${chapter.chapter_id}`" :key="chapter.chapter_id">
          <div class="chapter-title"><div><p class="eyebrow">CHAPTER {{ String(chapter.position).padStart(2, '0') }}</p><h3>{{ chapter.title }}</h3></div>
          <button v-if="draft.supports_versions && draft.status === 'completed' && !running" type="button" class="ghost-btn" @click="edit(chapter.chapter_id)">修改与历史版本</button></div>
          <p v-if="notices[chapter.chapter_id] && !editing[chapter.chapter_id]" class="chapter-notice" role="status">{{ notices[chapter.chapter_id] }}</p>
          <p v-if="selected[chapter.chapter_id] && histories[chapter.chapter_id]?.[0] && selected[chapter.chapter_id] !== histories[chapter.chapter_id]?.[0]?.version_id" class="history-notice">正在查看历史版本。<button type="button" @click="selected[chapter.chapter_id] = histories[chapter.chapter_id]?.[0]?.version_id ?? ''">返回当前版本</button></p>
          <div v-if="draft.supports_versions && draft.status === 'completed' && !running" class="tools edit-tools">
            <template v-if="editing[chapter.chapter_id]">
              <div class="edit-section">
                <p class="edit-label">版本</p>
                <div class="edit-row">
                  <label class="field">查看版本 <select v-model="selected[chapter.chapter_id]" :disabled="busy[chapter.chapter_id] || !!restorePending[chapter.chapter_id]">
                    <option v-for="version in histories[chapter.chapter_id] ?? []" :key="version.version_id" :value="version.version_id">第 {{ version.version_no }} 版 · {{ version.kind === 'generated' ? '初稿' : version.kind === 'revised' ? '修改' : '恢复' }}</option>
                  </select></label>
                  <button v-if="selected[chapter.chapter_id] && selected[chapter.chapter_id] !== histories[chapter.chapter_id]?.[0]?.version_id" type="button" class="btn btn-warn" :disabled="busy[chapter.chapter_id]" @click="restore(chapter.chapter_id, selected[chapter.chapter_id]!)">恢复所选版本</button>
                </div>
                <details v-if="selected[chapter.chapter_id] && selected[chapter.chapter_id] !== histories[chapter.chapter_id]?.[0]?.version_id" class="version-compare"><summary>对比当前版本</summary><div><section><strong>所选历史版本</strong><p v-for="(item, index) in paragraphs(chapter)" :key="index">{{ item.text }}</p></section><section><strong>当前版本</strong><p v-for="(item, index) in histories[chapter.chapter_id]?.[0]?.paragraphs ?? []" :key="index">{{ item.text }}</p></section></div></details>
              </div>
              <div class="edit-section">
                <p class="edit-label">修订</p>
                <p class="edit-caution">涉及研究方向或大纲结构的修改可能影响其他章节，请自行核对全文。</p>
                <label class="field">修改方式 <select v-model="modes[chapter.chapter_id]" :disabled="busy[chapter.chapter_id] || !!pending[chapter.chapter_id]"><option value="rewrite">重写</option><option value="expand">扩写</option><option value="condense">精简</option><option value="restructure">逻辑调整</option></select></label>
                <textarea v-model="instructions[chapter.chapter_id]" :disabled="busy[chapter.chapter_id] || !!pending[chapter.chapter_id]" maxlength="2000" placeholder="填写本章修改要求" aria-label="章节修改要求" />
                <div class="edit-submit"><button type="button" class="btn btn-primary" :disabled="busy[chapter.chapter_id] || !!restorePending[chapter.chapter_id]" @click="revise(chapter.chapter_id)">{{ pending[chapter.chapter_id] ? '重试相同修改请求' : '提交章节修改' }}</button></div>
              </div>
            </template><p v-if="notices[chapter.chapter_id]" class="chapter-notice" role="status">{{ notices[chapter.chapter_id] }}</p>
          </div>
          <div v-for="(paragraph, index) in paragraphs(chapter)" :id="`paragraph-${chapter.chapter_id}-${index + 1}`" :key="index" class="paragraph pa-prose"><p>{{ paragraph.text }}<button v-for="item in paragraph.citations" :key="item.source_id + item.evidence" type="button" class="citation" :aria-label="`查看文献 ${citationNumbers.get(item.source_id)}`" @click="citation = item">[{{ citationNumbers.get(item.source_id) }}]</button></p></div>
          <p v-if="!paragraphs(chapter).length" class="loading">暂无正文</p>
        </article></template>
      </div>
      <aside v-show="showReview || view === 'review'" class="review-side"><ReviewPanel v-if="draft.supports_versions && draft.status === 'completed' && !running" :project-id="projectId" :run-id="runId" :revision-tick="revisionTick" :chapter-ids="draft.chapters.map(chapter => ({ id: chapter.chapter_id, title: chapter.title }))" @locate="locateReview" @edit="editFromReview" />
      </aside></div>
    </template>
    <div v-if="citation" class="dialog-backdrop" @click.self="citation = null"><section class="citation-dialog" role="dialog" aria-modal="true" aria-label="文献详情"><div class="dialog-head"><span class="eyebrow">REFERENCE · {{ citationNumbers.get(citation.source_id) }}</span><button type="button" aria-label="关闭文献详情" @click="citation = null">×</button></div><h3>{{ citation.title }}</h3><p class="cite-meta">{{ citation.authors.join('、') || '作者未提供' }} · <span class="num">{{ citation.year ?? '年份未提供' }}</span></p><p v-if="citation.doi" class="cite-doi">DOI：<span class="num">{{ citation.doi }}</span></p><h4>本段引用的原文证据</h4><blockquote class="pa-prose">{{ citation.evidence }}</blockquote><a :href="citation.url" target="_blank" rel="noopener noreferrer">查看文献来源 ↗</a></section></div>
  </section>
</template>
<style scoped>
/* ==========================================================================
   论文工作台 · 三栏阅读布局（目录 / 纸张 / 审查）
   ========================================================================== */
.draft { --bar-offset: 72px; margin: 0 0 8px; color: var(--pa-text-body); }
.num { font-family: var(--pa-font-mono); font-variant-numeric: tabular-nums; }
.eyebrow { margin: 0; color: var(--pa-text-meta); font-family: var(--pa-font-mono); font-size: 11px; font-weight: 600; letter-spacing: .14em; }
button, select, textarea { font: inherit; }
button { cursor: pointer; }
button:disabled { opacity: .5; cursor: not-allowed; }

/* ---------- Sticky 工作台工具条 ---------- */
.workbench-bar { position: sticky; top: 0; z-index: 20; display: grid; grid-template-columns: minmax(160px, auto) 1fr auto; align-items: center; gap: 16px; margin: 0 0 16px; padding: 8px 16px; min-height: 56px; background: var(--pa-bar-bg); backdrop-filter: saturate(1.4) blur(10px); -webkit-backdrop-filter: saturate(1.4) blur(10px); border: var(--pa-border); border-radius: var(--pa-radius-card); box-shadow: var(--pa-shadow-card); }
.workspace-name { margin: 0; color: var(--pa-color-heading); font-size: 15px; font-weight: 600; letter-spacing: -.01em; }
.workspace-status { margin: 2px 0 0; color: var(--pa-text-meta); font-size: 12px; white-space: nowrap; }

.tabs { display: flex; justify-self: center; gap: 2px; padding: 3px; background: var(--pa-bg-hover); border-radius: var(--pa-radius-card); overflow-x: auto; }
.tabs button { border: 0; background: none; height: 30px; padding: 0 14px; border-radius: var(--pa-radius-control); white-space: nowrap; color: var(--pa-text-meta); font-size: 13px; font-weight: 500; transition: background-color .15s, color .15s, box-shadow .15s; }
.tabs button:hover { color: var(--pa-color-heading); }
.tabs .active { background: var(--pa-bg-surface); color: var(--pa-color-heading); font-weight: 600; box-shadow: 0 1px 2px rgba(15, 23, 42, .08), 0 0 0 1px var(--pa-border-color); }
.tabs .tab-aux { color: var(--pa-text-faint); }
.tabs button:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); }

.bar-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; flex-wrap: wrap; }
.workspace-controls { display: flex; gap: 2px; }
.workspace-controls button { border: 0; background: none; height: 30px; padding: 0 10px; border-radius: var(--pa-radius-control); color: var(--pa-text-meta); font-size: 12px; transition: background-color .15s, color .15s; }
.workspace-controls button:hover { background: var(--pa-bg-hover); color: var(--pa-color-heading); }
.export-tools { display: inline-flex; padding-left: 12px; border-left: var(--pa-border); }
.export-tools button { height: 30px; padding: 0 12px; margin-left: -1px; border: var(--pa-border); background: var(--pa-bg-surface); color: var(--pa-text-body); font-size: 12px; font-weight: 500; white-space: nowrap; transition: background-color .15s, color .15s; }
.export-tools button:first-child { margin-left: 0; border-radius: var(--pa-radius-control) 0 0 var(--pa-radius-control); }
.export-tools button:last-child { border-radius: 0 var(--pa-radius-control) var(--pa-radius-control) 0; }
.export-tools button:hover:not(:disabled) { background: var(--pa-bg-hover); color: var(--el-color-primary); }
.workspace-controls button:focus-visible, .export-tools button:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); position: relative; }

.bar-alert, .paper-alert { margin: 0 0 12px; padding: 10px 14px; border-radius: var(--pa-radius-control); font-size: 13px; line-height: 1.6; color: var(--pa-color-danger); background: var(--pa-bg-danger); border: 1px solid var(--pa-border-danger); }
.loading { color: var(--pa-text-meta); font-size: 13px; padding: 12px 0; }

/* ---------- 三栏网格 ---------- */
.workspace-grid { display: grid; grid-template-columns: 200px minmax(0, 760px) minmax(280px, 340px); justify-content: center; gap: 20px; align-items: start; }
.workspace-grid.hide-contents { grid-template-columns: minmax(0, 760px) minmax(280px, 340px); }
.workspace-grid.hide-review { grid-template-columns: 200px minmax(0, 860px); }
.workspace-grid.review-view { grid-template-columns: 200px minmax(0, 960px); }
.workspace-grid.hide-contents.hide-review { grid-template-columns: minmax(0, 860px); }
.workspace-grid.review-view.hide-contents { grid-template-columns: minmax(0, 960px); }
.workspace-grid.hide-review:not(.review-view) .review-side { grid-column: 1 / -1; position: static; max-height: none; }

/* ---------- 目录 ---------- */
.contents, .review-side { position: sticky; top: var(--bar-offset); max-height: calc(100vh - var(--bar-offset) - 16px); overflow: auto; }
.contents { padding: 4px 0; }
.aside-title { margin: 0 0 8px; padding: 0 10px; color: var(--pa-text-meta); font-family: var(--pa-font-mono); font-size: 11px; font-weight: 600; letter-spacing: .12em; }
.chapter-link { position: relative; display: flex; width: 100%; gap: 10px; padding: 8px 10px; border: 0; background: none; border-radius: var(--pa-radius-control); text-align: left; color: var(--pa-text-body); font-size: 13px; line-height: 1.5; transition: background-color .15s, color .15s; }
.chapter-link:hover { background: var(--pa-bg-hover); color: var(--pa-color-heading); }
.chapter-link.selected { background: var(--pa-bg-surface); color: var(--pa-color-heading); font-weight: 600; box-shadow: 0 0 0 1px var(--pa-border-color), var(--pa-shadow-card); }
.chapter-link.selected::before { content: ''; position: absolute; left: 0; top: 8px; bottom: 8px; width: 3px; border-radius: 0 2px 2px 0; background: var(--el-color-primary); }
.toc-no { flex: none; color: var(--pa-text-faint); font-family: var(--pa-font-mono); font-size: 12px; font-variant-numeric: tabular-nums; padding-top: 1px; }
.chapter-link.selected .toc-no { color: var(--el-color-primary); }
.toc-text { min-width: 0; overflow-wrap: anywhere; }
.chapter-link small { display: block; margin-top: 1px; color: var(--pa-text-faint); font-size: 11px; font-weight: 400; }
.chapter-link.st-writing small { color: var(--el-color-primary); }
.chapter-link.st-failed small, .chapter-link.st-needs_review small { color: var(--pa-color-amber); }
.chapter-link:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); }

/* ---------- 纸张 ---------- */
.paper, .review-side { min-width: 0; background: var(--pa-bg-surface); border: var(--pa-border); border-radius: var(--pa-radius-card); box-shadow: var(--pa-shadow-card); }
.paper { padding: 40px clamp(24px, 4vw, 56px) 56px; }
.review-side { padding: 20px; }
.review-view .review-side { position: static; max-height: none; padding: var(--pa-space-card); }
.paper-intro { padding-bottom: 20px; margin-bottom: 28px; border-bottom: var(--pa-border); }
.paper-intro h2 { margin: 8px 0 6px; color: var(--pa-color-heading); font-size: 24px; font-weight: 600; line-height: 1.4; letter-spacing: -.02em; }
.paper-sub { margin: 0; font-size: 12px; color: var(--pa-text-meta); }

article { margin-bottom: 44px; scroll-margin-top: calc(var(--bar-offset) + 8px); }
article:last-child { margin-bottom: 0; }
.chapter-title { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; margin-bottom: 16px; }
h3 { margin: 6px 0 0; color: var(--pa-color-heading); font-size: 18px; font-weight: 600; line-height: 1.45; letter-spacing: -.01em; }
.ghost-btn { flex: none; height: 28px; padding: 0 10px; border: var(--pa-border); background: var(--pa-bg-surface); color: var(--pa-text-meta); border-radius: var(--pa-radius-control); font-size: 12px; opacity: .8; transition: opacity .15s, color .15s, border-color .15s, background-color .15s; }
article:hover .ghost-btn, .ghost-btn:focus-visible { opacity: 1; }
.ghost-btn:hover { color: var(--el-color-primary); border-color: var(--el-color-primary-light-5); background: var(--el-color-primary-light-9); }
.ghost-btn:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); }

/* 学术长文：行高 1.7 / 段距 14px / 限宽 */
.paragraph { scroll-margin-top: calc(var(--bar-offset) + 8px); max-width: 42em; }
.paragraph p { margin: 0 0 14px; font-size: 15px; line-height: 1.7; color: var(--pa-text-body); text-align: justify; overflow-wrap: anywhere; }
.paragraph:target p { background: var(--pa-bg-amber); }
.citation { display: inline-block; margin: 0 1px; padding: 0 4px; border: 0; border-radius: 4px; background: var(--el-color-primary-light-9); color: var(--el-color-primary); font-family: var(--pa-font-mono); font-size: 11px; font-weight: 600; line-height: 16px; vertical-align: super; font-variant-numeric: tabular-nums; transition: background-color .15s, color .15s; }
.citation:hover { background: var(--el-color-primary); color: var(--pa-bg-surface); }
.citation:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); }

.chapter-notice { margin: 0 0 12px; padding: 8px 12px; border-radius: var(--pa-radius-control); background: var(--pa-bg-inset); border-left: 3px solid var(--el-color-primary-light-5); color: var(--pa-text-body); font-size: 12px; line-height: 1.6; }
.history-notice { margin: 0 0 12px; padding: 8px 12px; border-radius: var(--pa-radius-control); background: var(--pa-bg-amber); border-left: 3px solid var(--pa-color-amber-strong); color: var(--pa-color-amber); font-size: 12px; }
.history-notice button { border: 0; padding: 0 0 0 6px; background: none; color: var(--el-color-primary); font-weight: 600; text-decoration: underline; text-underline-offset: 3px; }

/* ---------- 章节修订面板 ---------- */
.tools { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
.edit-tools:has(textarea) { display: grid; gap: 16px; margin: 0 0 20px; padding: 16px; background: var(--pa-bg-inset); border: var(--pa-border); border-radius: var(--pa-radius-card); }
.edit-tools > .chapter-notice { margin: 0; width: 100%; }
.edit-section { display: flex; flex-direction: column; gap: 10px; min-width: 0; }
.edit-section + .edit-section { padding-top: 16px; border-top: var(--pa-border); }
.edit-label { margin: 0; color: var(--pa-text-meta); font-family: var(--pa-font-mono); font-size: 11px; font-weight: 600; letter-spacing: .12em; }
.edit-row { display: flex; align-items: flex-end; gap: 8px; flex-wrap: wrap; }
.edit-caution { margin: 0; color: var(--pa-text-meta); font-size: 12px; line-height: 1.6; }
.field { display: flex; flex-direction: column; gap: 4px; color: var(--pa-text-meta); font-size: 12px; font-weight: 500; }
.edit-tools select { height: 32px; min-width: 200px; padding: 0 10px; border: var(--pa-border); border-radius: var(--pa-radius-control); background: var(--pa-bg-surface); color: var(--pa-text-body); font-size: 13px; font-weight: 400; }
.edit-tools textarea { width: 100%; min-height: 96px; padding: 10px 12px; border: var(--pa-border); border-radius: var(--pa-radius-control); background: var(--pa-bg-surface); color: var(--pa-text-body); font-size: 13px; line-height: 1.65; resize: vertical; }
.edit-tools select:focus-visible, .edit-tools textarea:focus-visible { outline: none; border-color: var(--el-color-primary); box-shadow: var(--pa-ring-primary); }
.edit-submit { display: flex; justify-content: flex-end; }
.btn { height: 32px; padding: 0 14px; border: var(--pa-border); border-radius: var(--pa-radius-control); background: var(--pa-bg-surface); color: var(--pa-text-body); font-size: 13px; font-weight: 500; transition: background-color .15s, border-color .15s, color .15s; }
.btn:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); }
.btn-primary { background: var(--el-color-primary); border-color: var(--el-color-primary); color: var(--pa-bg-surface); }
.btn-primary:hover:not(:disabled) { background: var(--el-color-primary-dark-2); border-color: var(--el-color-primary-dark-2); }
.btn-warn { color: var(--pa-color-amber); border-color: var(--pa-border-amber); }
.btn-warn:hover:not(:disabled) { background: var(--pa-bg-amber); border-color: var(--pa-color-amber-strong); }

.version-compare { width: 100%; }
.version-compare summary { cursor: pointer; color: var(--el-color-primary); font-size: 12px; font-weight: 500; }
.version-compare > div { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 10px; }
.version-compare section { min-width: 0; max-height: 320px; overflow: auto; padding: 12px 14px; background: var(--pa-bg-surface); border: var(--pa-border); border-radius: var(--pa-radius-control); }
.version-compare section:last-child { border-top: 2px solid var(--el-color-primary-light-5); }
.version-compare section:first-child { border-top: 2px solid var(--pa-border-amber); }
.version-compare strong { display: block; margin-bottom: 6px; color: var(--pa-color-heading); font-size: 12px; }
.version-compare section p { margin: 0 0 8px; font-size: 13px; line-height: 1.7; overflow-wrap: anywhere; }

/* ---------- 版本列表 ---------- */
.version-list > div { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 14px 4px; border-bottom: var(--pa-border); font-size: 14px; color: var(--pa-color-heading); }
.version-list > div:last-child { border-bottom: 0; }
.vl-title .num { color: var(--pa-text-faint); margin-right: 4px; }
.version-list button { flex: none; height: 30px; padding: 0 12px; border: var(--pa-border); border-radius: var(--pa-radius-control); background: var(--pa-bg-surface); color: var(--pa-text-body); font-size: 12px; font-weight: 500; transition: color .15s, border-color .15s, background-color .15s; }
.version-list button:hover { color: var(--el-color-primary); border-color: var(--el-color-primary-light-5); background: var(--el-color-primary-light-9); }

/* ---------- 文献引文卡片 ---------- */
.dialog-backdrop { position: fixed; inset: 0; z-index: 100; display: grid; place-items: center; padding: 20px; background: var(--pa-backdrop); backdrop-filter: blur(2px); }
.citation-dialog { width: min(560px, 100%); max-height: 85vh; overflow: auto; padding: var(--pa-space-card); background: var(--pa-bg-surface); border: var(--pa-border); border-radius: var(--pa-radius-card); box-shadow: var(--pa-shadow-float); }
.dialog-head { display: flex; justify-content: space-between; align-items: center; }
.dialog-head .eyebrow { color: var(--el-color-primary); }
.dialog-head button { display: grid; place-items: center; width: 28px; height: 28px; border: 0; border-radius: var(--pa-radius-control); background: none; color: var(--pa-text-meta); font-size: 18px; line-height: 1; }
.dialog-head button:hover { background: var(--pa-bg-hover); color: var(--pa-color-heading); }
.dialog-head button:focus-visible { outline: none; box-shadow: var(--pa-ring-primary); }
.citation-dialog h3 { margin: 12px 0 8px; font-size: 16px; line-height: 1.5; }
.cite-meta, .cite-doi { margin: 0 0 4px; color: var(--pa-text-meta); font-size: 12px; line-height: 1.6; }
.cite-doi .num { color: var(--pa-text-body); }
.citation-dialog h4 { margin: 20px 0 8px; color: var(--pa-text-meta); font-family: var(--pa-font-mono); font-size: 11px; font-weight: 600; letter-spacing: .1em; }
.citation-dialog blockquote { position: relative; margin: 0; padding: 14px 16px 14px 40px; font-size: 14px; line-height: 1.7; color: var(--pa-text-body); background: var(--pa-bg-inset); border: var(--pa-border); border-left: 3px solid var(--el-color-primary); border-radius: 0 var(--pa-radius-control) var(--pa-radius-control) 0; overflow-wrap: anywhere; }
.citation-dialog blockquote::before { content: '“'; position: absolute; left: 12px; top: 6px; color: var(--el-color-primary-light-5); font-family: Georgia, serif; font-size: 34px; line-height: 1; }
.citation-dialog a { display: inline-flex; margin-top: 16px; color: var(--el-color-primary); font-size: 13px; text-decoration: none; border-bottom: 1px solid var(--el-color-primary-light-7); }
.citation-dialog a:hover { border-bottom-color: var(--el-color-primary); }

/* ---------- 响应式 ---------- */
@media (max-width: 1280px) {
  .workbench-bar { grid-template-columns: 1fr auto; }
  .tabs { grid-column: 1 / -1; grid-row: 2; justify-self: start; }
  .draft { --bar-offset: 112px; }
  .workspace-grid { grid-template-columns: 168px minmax(0, 1fr) 280px; gap: 14px; }
  .workspace-grid.hide-contents { grid-template-columns: minmax(0, 1fr) 280px; }
  .workspace-grid.hide-review, .workspace-grid.review-view { grid-template-columns: 168px minmax(0, 1fr); }
  .workspace-grid.hide-contents.hide-review, .workspace-grid.review-view.hide-contents { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 1024px) {
  .workbench-bar { position: static; grid-template-columns: 1fr; }
  .bar-actions { justify-content: flex-start; }
  .export-tools { padding-left: 0; border-left: 0; }
  .draft { --bar-offset: 16px; }
  .workspace-grid, .workspace-grid.hide-contents, .workspace-grid.hide-review, .workspace-grid.review-view { display: flex; flex-direction: column; }
  .contents, .review-side { position: static; max-height: none; width: 100%; }
  .contents { display: flex; gap: 6px; overflow-x: auto; }
  .aside-title { min-width: 60px; padding-top: 10px; }
  .chapter-link { min-width: 168px; }
  .paper { width: 100%; padding: 28px 22px 40px; }
  .paper-intro h2 { font-size: 21px; }
  .review-side { order: 2; }
}
@media (max-width: 640px) { .version-compare > div { grid-template-columns: 1fr; } .export-tools { flex-wrap: wrap; } }
</style>
