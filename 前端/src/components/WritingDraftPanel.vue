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
    <header class="workspace-header"><div><p class="workspace-name">论文工作台</p><p v-if="draft" class="workspace-status" role="status">{{ draft.status === 'completed' ? '初稿已完成' : draft.status === 'writing' ? '正在生成' : draft.status === 'failed' || draft.status === 'needs_review' ? '需要检查' : '准备中' }} · {{ draft.chapters.filter(chapter => chapter.status === 'completed').length }} / {{ draft.chapters.length }} 章完成</p></div>
    <div v-if="draft?.status === 'completed'" class="tools">
      <button type="button" :disabled="exportBusy" @click="exportFile('docx')">下载论文 Word</button>
      <button type="button" :disabled="exportBusy" @click="exportFile('pdf')">下载论文 PDF</button>
      <button type="button" :disabled="exportBusy" @click="exportFile('review-docx')">下载审查报告</button>
      <p v-if="exportError" role="alert">{{ exportError }}</p>
    </div>
    </header>
    <nav class="tabs" aria-label="工作台视图"><button :class="{active: view === 'reading'}" @click="view = 'reading'">论文阅读</button><button :class="{active: view === 'review'}" @click="view = 'review'">质量审查</button><button :class="{active: view === 'versions'}" @click="view = 'versions'">版本历史</button><button @click="showProgress">生成进度</button></nav>
    <p v-if="error" role="alert">{{ error }}</p>
    <p v-else-if="!draft" role="status">正在读取初稿…</p>
    <template v-else>
      <div class="workspace-controls"><button @click="showContents = !showContents">{{ showContents ? '收起目录' : '显示目录' }}</button><button @click="showReview = !showReview">{{ showReview ? '收起审查' : '显示审查' }}</button></div>
      <div class="workspace-grid" :class="{'hide-contents': !showContents, 'hide-review': !showReview || view === 'versions', 'review-view': view === 'review'}">
      <aside v-show="showContents" class="contents"><p class="aside-title">论文目录</p><button v-for="chapter in draft.chapters" :key="chapter.chapter_id" class="chapter-link" :class="{selected: activeChapter === chapter.chapter_id}" @click="navigateChapter(chapter.chapter_id)"><span>{{ String(chapter.position).padStart(2, '0') }}</span><span>{{ chapter.title }}<small>{{ chapter.status === 'completed' ? '已完成' : chapter.status === 'writing' ? '生成中' : chapter.status === 'failed' || chapter.status === 'needs_review' ? '需要检查' : '等待生成' }}</small></span></button></aside>
      <div v-show="view !== 'review'" class="paper"><div class="paper-intro"><p class="eyebrow">RESEARCH PAPER</p><h2>{{ view === 'versions' ? '章节与版本' : title }}</h2><p>{{ view === 'versions' ? '选择章节，查看版本或提出修改。' : '论文初稿 · 请核验论述与引用证据' }}</p></div>
      <div v-if="view === 'versions'" class="version-list"><div v-for="chapter in draft.chapters" :key="chapter.chapter_id"><span>{{ chapter.position }}. {{ chapter.title }}</span><button @click="edit(chapter.chapter_id); navigateChapter(chapter.chapter_id)">查看版本与修改</button></div></div>
      <template v-else>
      <p v-if="!draft.chapters.length && (draft.status === 'failed' || draft.status === 'needs_review')" role="alert">大纲生成未完成，请检查运行状态；系统不会自动重试。</p>
      <p v-else-if="!draft.chapters.length">正在生成论文大纲…</p>
      <article v-for="chapter in draft.chapters" :id="`chapter-${chapter.chapter_id}`" :key="chapter.chapter_id">
        <div class="chapter-title"><div><p class="eyebrow">CHAPTER {{ String(chapter.position).padStart(2, '0') }}</p><h3>{{ chapter.title }}</h3></div>
        <button v-if="draft.supports_versions && draft.status === 'completed' && !running" @click="edit(chapter.chapter_id)">修改与历史版本</button></div>
        <p v-if="notices[chapter.chapter_id] && !editing[chapter.chapter_id]" role="status">{{ notices[chapter.chapter_id] }}</p>
        <p v-if="selected[chapter.chapter_id] && histories[chapter.chapter_id]?.[0] && selected[chapter.chapter_id] !== histories[chapter.chapter_id]?.[0]?.version_id" class="history-notice">正在查看历史版本。<button @click="selected[chapter.chapter_id] = histories[chapter.chapter_id]?.[0]?.version_id ?? ''">返回当前版本</button></p>
        <div v-if="draft.supports_versions && draft.status === 'completed' && !running" class="tools edit-tools">
          <template v-if="editing[chapter.chapter_id]">
            <label>查看版本 <select v-model="selected[chapter.chapter_id]" :disabled="busy[chapter.chapter_id] || !!restorePending[chapter.chapter_id]">
              <option v-for="version in histories[chapter.chapter_id] ?? []" :key="version.version_id" :value="version.version_id">第 {{ version.version_no }} 版 · {{ version.kind === 'generated' ? '初稿' : version.kind === 'revised' ? '修改' : '恢复' }}</option>
            </select></label>
            <button v-if="selected[chapter.chapter_id] && selected[chapter.chapter_id] !== histories[chapter.chapter_id]?.[0]?.version_id" type="button" :disabled="busy[chapter.chapter_id]" @click="restore(chapter.chapter_id, selected[chapter.chapter_id]!)">恢复所选版本</button>
            <details v-if="selected[chapter.chapter_id] && selected[chapter.chapter_id] !== histories[chapter.chapter_id]?.[0]?.version_id" class="version-compare"><summary>对比当前版本</summary><div><section><strong>所选历史版本</strong><p v-for="(item, index) in paragraphs(chapter)" :key="index">{{ item.text }}</p></section><section><strong>当前版本</strong><p v-for="(item, index) in histories[chapter.chapter_id]?.[0]?.paragraphs ?? []" :key="index">{{ item.text }}</p></section></div></details>
            <p>涉及研究方向或大纲结构的修改可能影响其他章节，请自行核对全文。</p>
            <label>修改方式 <select v-model="modes[chapter.chapter_id]" :disabled="busy[chapter.chapter_id] || !!pending[chapter.chapter_id]"><option value="rewrite">重写</option><option value="expand">扩写</option><option value="condense">精简</option><option value="restructure">逻辑调整</option></select></label>
            <textarea v-model="instructions[chapter.chapter_id]" :disabled="busy[chapter.chapter_id] || !!pending[chapter.chapter_id]" maxlength="2000" placeholder="填写本章修改要求" aria-label="章节修改要求" />
            <button type="button" :disabled="busy[chapter.chapter_id] || !!restorePending[chapter.chapter_id]" @click="revise(chapter.chapter_id)">{{ pending[chapter.chapter_id] ? '重试相同修改请求' : '提交章节修改' }}</button>
          </template><p v-if="notices[chapter.chapter_id]" role="status">{{ notices[chapter.chapter_id] }}</p>
        </div>
        <div v-for="(paragraph, index) in paragraphs(chapter)" :id="`paragraph-${chapter.chapter_id}-${index + 1}`" :key="index" class="paragraph"><p>{{ paragraph.text }}<button v-for="item in paragraph.citations" :key="item.source_id + item.evidence" class="citation" :aria-label="`查看文献 ${citationNumbers.get(item.source_id)}`" @click="citation = item">[{{ citationNumbers.get(item.source_id) }}]</button></p></div>
        <p v-if="!paragraphs(chapter).length">暂无正文</p>
      </article></template></div>
      <aside v-show="showReview || view === 'review'" class="review-side"><ReviewPanel v-if="draft.supports_versions && draft.status === 'completed' && !running" :project-id="projectId" :run-id="runId" :revision-tick="revisionTick" :chapter-ids="draft.chapters.map(chapter => ({ id: chapter.chapter_id, title: chapter.title }))" @locate="locateReview" @edit="editFromReview" />
      </aside></div>
    </template>
    <div v-if="citation" class="dialog-backdrop" @click.self="citation = null"><section class="citation-dialog" role="dialog" aria-modal="true" aria-label="文献详情"><div class="dialog-head"><span class="eyebrow">REFERENCE · {{ citationNumbers.get(citation.source_id) }}</span><button aria-label="关闭文献详情" @click="citation = null">×</button></div><h3>{{ citation.title }}</h3><p>{{ citation.authors.join('、') || '作者未提供' }} · {{ citation.year ?? '年份未提供' }}</p><p v-if="citation.doi">DOI：{{ citation.doi }}</p><h4>本段引用的原文证据</h4><blockquote>{{ citation.evidence }}</blockquote><a :href="citation.url" target="_blank" rel="noopener noreferrer">查看文献来源 ↗</a></section></div>
  </section>
</template>
<style scoped>
.draft { margin:28px 0; color:#263b30; }.workspace-header {display:flex; justify-content:space-between; align-items:end; gap:20px}.workspace-header h2{font-size:25px; margin:8px 0}.workspace-header p,.caution{font-size:12px;color:#84958a}.eyebrow{color:#789082;font-size:10px;font-weight:700;letter-spacing:.15em}.tools{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.tools p,.tools textarea{width:100%}button,select,textarea{font:inherit}button{cursor:pointer}button:disabled{opacity:.5;cursor:not-allowed}.tools button,.workspace-controls button,.chapter-title button,.version-list button,.edit-tools select{border:1px solid #dce6df;background:#fff;color:#42634f;border-radius:6px;padding:8px 10px;font-size:12px}.tabs{display:flex;gap:25px;border-bottom:1px solid #dfe7e1;overflow:auto;margin:18px 0}.tabs button,.tabs a{border:0;background:none;padding:12px 0;text-decoration:none;white-space:nowrap;color:#8c9a91;font-size:13px}.tabs .active{color:#32674b;border-bottom:2px solid #558668;font-weight:600}.workspace-controls{display:flex;justify-content:space-between;margin:12px 0}.workspace-grid{display:grid;grid-template-columns:170px minmax(0,1fr) 280px;gap:14px;align-items:start}.workspace-grid.hide-contents{grid-template-columns:minmax(0,1fr) 280px}.workspace-grid.hide-review,.workspace-grid.review-view{grid-template-columns:170px minmax(0,1fr)}.workspace-grid.hide-contents.hide-review{grid-template-columns:minmax(0,1fr)}.contents,.review-side{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}.contents{padding:16px 0}.aside-title{font-size:12px;font-weight:700;color:#667a6d}.chapter-link{display:flex;width:100%;gap:9px;padding:10px 7px;border:0;background:none;border-radius:6px;text-align:left;color:#6b7b70;font-size:12px;line-height:1.5}.chapter-link.selected,.chapter-link:hover{background:#eaf1eb;color:#315f46}.chapter-link small{display:block;color:#9aa9a0;font-size:10px}.paper,.review-side{background:#fff;border:1px solid #e4ebe5;border-radius:9px;min-width:0}.paper{padding:48px clamp(24px,4vw,65px) 70px}.review-side{padding:20px}.review-view .review-side{position:static;max-height:none}.paper-intro{border-bottom:1px solid #e9eeea;padding-bottom:30px;margin-bottom:40px}.paper-intro h2{font-family:Georgia,'Microsoft YaHei',serif;font-size:26px;color:#253b2e;line-height:1.5}.paper-intro p:not(.eyebrow){font-size:12px;color:#98a59d}.chapter-title{display:flex;justify-content:space-between;align-items:start;gap:12px}article{margin-bottom:54px;scroll-margin-top:20px}h3{color:#253e30;font-size:19px;line-height:1.5;margin:8px 0 25px}.paragraph{scroll-margin-top:20px}.paragraph p{font-family:Georgia,'Microsoft YaHei',serif;font-size:15px;line-height:2.05;color:#394b40;text-align:justify;overflow-wrap:anywhere;margin:0 0 22px}.citation{border:0;background:none;color:#418262;font-size:11px;font-weight:700;vertical-align:super;padding:0 2px}.edit-tools:has(textarea){background:#f4f8f4;border:1px solid #e1eae2;border-radius:8px;padding:15px;margin:15px 0;font-size:12px}.edit-tools textarea{min-height:80px;border:1px solid #d9e4db;border-radius:6px;padding:10px}.version-list>div{display:flex;justify-content:space-between;gap:15px;border-bottom:1px solid #edf1ed;padding:16px 0;font-size:13px}.dialog-backdrop{position:fixed;inset:0;z-index:100;background:#18271db3;display:grid;place-items:center;padding:20px}.citation-dialog{width:min(520px,100%);max-height:85vh;overflow:auto;background:white;border-radius:10px;padding:25px}.dialog-head{display:flex;justify-content:space-between}.dialog-head button{border:0;background:none;font-size:20px}.citation-dialog p,.citation-dialog blockquote{font-size:13px;line-height:1.8}.citation-dialog blockquote{margin:0;background:#f1f6f2;border-left:3px solid #9cbaa5;padding:14px}.citation-dialog a{display:block;margin-top:20px;color:#39785b}@media(max-width:1200px){.workspace-grid{grid-template-columns:140px minmax(0,1fr) 240px;gap:8px}.workspace-grid.hide-contents{grid-template-columns:minmax(0,1fr) 240px}.workspace-grid.hide-review,.workspace-grid.review-view{grid-template-columns:140px minmax(0,1fr)}.paper{padding:35px 25px}}@media(max-width:900px){.workspace-header{align-items:start;flex-direction:column}.workspace-grid,.workspace-grid.hide-contents,.workspace-grid.hide-review,.workspace-grid.review-view{display:flex;flex-direction:column}.contents,.review-side{position:static;max-height:none;width:100%}.contents{display:flex;gap:5px;overflow:auto}.aside-title{min-width:55px}.chapter-link{min-width:160px}.paper{width:100%}.review-side{order:2}}
</style>
<style scoped>
.version-compare { width:100%; margin:10px 0; }
.workspace-grid.review-view.hide-contents { grid-template-columns:minmax(0,1fr); }
.draft { margin:4px 0 24px; }
.workspace-header { align-items:center; }
.workspace-name { color:#2a4736 !important; font-size:15px !important; font-weight:700; margin:0 0 3px; }
.workspace-status { color:#7a8f80 !important; font-size:12px !important; margin:0; }
.tabs { margin:8px 0 10px; }
.tabs button { padding:10px 0; }
.workspace-controls { margin:8px 0; }
.workspace-grid { grid-template-columns:180px minmax(0, 760px) minmax(250px, 300px); justify-content:center; gap:16px; }
.paper { padding:32px clamp(24px, 4vw, 56px) 65px; }
.paper-intro { padding-bottom:16px; margin-bottom:22px; }
.paper-intro h2 { margin:8px 0; }
.paper-intro p { margin:4px 0; }
.chapter h3, .chapter-title h3 { margin-bottom:16px; }
.chapter-link:focus-visible,.tabs button:focus-visible { outline:2px solid #72a584; outline-offset:2px; }
.history-notice { background:#fcf7eb; color:#806635; border-radius:6px; padding:9px 12px; font-size:12px; }
.history-notice button { border:0; color:#356b4a; background:none; text-decoration:underline; }
@media(max-width:1200px) { .workspace-grid { grid-template-columns:150px minmax(0,1fr) 250px; gap:10px; } }
@media(max-width:900px) { .workspace-header { gap:10px; } .paper { padding:28px 22px 50px; } .paper-intro h2 { font-size:23px; } }
.version-compare summary { cursor:pointer; color:#39785b; }
.version-compare > div { display:grid; grid-template-columns:1fr 1fr; gap:14px; }
.version-compare section { min-width:0; background:#fff; border-radius:6px; padding:12px; max-height:320px; overflow:auto; }
.version-compare section p { line-height:1.7; overflow-wrap:anywhere; }
@media(max-width:640px) { .version-compare > div { grid-template-columns:1fr; } }
</style>
