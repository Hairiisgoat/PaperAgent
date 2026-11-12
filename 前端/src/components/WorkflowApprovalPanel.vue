<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElButton, ElMessageBox } from 'element-plus'
import 'element-plus/es/components/button/style/css'
import 'element-plus/es/components/message-box/style/css'
import { HttpError } from '../services/http'
import { useWorkflowStore } from '../stores/workflows'
import type { ApprovalDecision, ApprovalDetail } from '../types/workflows'

const props = defineProps<{ projectId: string; runId: string; threadId: string }>()
const router = useRouter()
const store = useWorkflowStore()
const detail = ref<ApprovalDetail | null>(null)
const loading = ref(true)
const submitting = ref(false)
const confirming = ref(false)
const error = ref('')
const selected = ref<string[]>([])
const pending = ref<ApprovalDecision | null>(null)
const locked = ref(false)
const storageKey = `paperagent.approval.${props.projectId}.${props.runId}`
const controller = new AbortController()
let mounted = true
const idPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const label = computed(() => detail.value?.approval_type === 'source_approval' ? '资料审核' : '任务审核')
const validSelection = computed(() => !!detail.value && selected.value.length > 0 && new Set(selected.value).size === selected.value.length && selected.value.every(id => detail.value?.item_ids.includes(id)))
const relevanceText = (hint: 'more' | 'some' | 'unknown' | undefined): string => hint === 'more' ? '词面匹配较多' : hint === 'some' ? '词面匹配较少' : '未发现明显词面匹配'
function validDetail(value: ApprovalDetail): boolean {
  return value.project_id === props.projectId && value.run_id === props.runId && value.thread_id === props.threadId &&
    idPattern.test(value.checkpoint_id) && (value.approval_type === 'task_approval' || value.approval_type === 'source_approval') &&
    Array.isArray(value.item_ids) && value.item_ids.length > 0 && value.item_ids.length <= 1000 &&
    value.item_ids.every(id => typeof id === 'string' && idPattern.test(id)) && new Set(value.item_ids).size === value.item_ids.length &&
    (value.items === undefined || (value.approval_type === 'task_approval' && Array.isArray(value.items) && value.items.length === value.item_ids.length && value.items.every((item, index) => item.task_id === value.item_ids[index] && typeof item.title === 'string' && typeof item.description === 'string' && typeof item.expected_output === 'string'))) &&
    (value.sources === undefined || (value.approval_type === 'source_approval' && Array.isArray(value.sources) && value.sources.length === value.item_ids.length && value.sources.every((s, i) => s.source_id === value.item_ids[i] && typeof s.title === 'string' && typeof s.doi === 'string' && typeof s.url === 'string' && s.url.startsWith('https://doi.org/') && Array.isArray(s.authors) && s.authors.every(a => typeof a === 'string') && Array.isArray(s.tasks) && s.tasks.every(t => idPattern.test(t.task_id) && typeof t.title === 'string'))))
}
function restorePending(): void {
  try {
    const raw = sessionStorage.getItem(storageKey)
    if (!raw) return
    locked.value = true
    const value: unknown = JSON.parse(raw)
    if (typeof value !== 'object' || value === null || !('decision' in value)) throw new Error('invalid')
    if (value.decision === 'reject' && Object.keys(value).length === 1) pending.value = { decision: 'reject' }
    else if (value.decision === 'approve' && 'approved_ids' in value && Array.isArray(value.approved_ids) && Object.keys(value).every(k => ['decision', 'approved_ids', 'continue_research', 'continue_writing', 'segmented_writing'].includes(k)) && (!('continue_research' in value) || value.continue_research === true) && (!('continue_writing' in value) || value.continue_writing === true) && (!('segmented_writing' in value) || value.segmented_writing === true && 'continue_writing' in value) && !('continue_research' in value && 'continue_writing' in value) && value.approved_ids.length > 0 && value.approved_ids.every(id => typeof id === 'string' && idPattern.test(id)) && new Set(value.approved_ids).size === value.approved_ids.length) {
      pending.value = { decision: 'approve', approved_ids: value.approved_ids, ...('continue_research' in value ? { continue_research: true } : {}), ...('continue_writing' in value ? { continue_writing: true } : {}), ...('segmented_writing' in value ? { segmented_writing: true } : {}) }
      selected.value = [...value.approved_ids]
    } else throw new Error('invalid')
  } catch { locked.value = true; error.value = '无法读取待确认的审核决定，请先核对服务端运行状态。' }
}
async function load(): Promise<void> {
  loading.value = true
  try {
    const value = await store.readApproval(props.projectId, props.runId, controller.signal)
    if (!mounted) return
    if (Array.isArray(value.item_ids) && value.item_ids.length === 0) throw new HttpError('unknown', '当前没有待审核对象，请核对服务端运行状态。')
    if (!validDetail(value)) throw new HttpError('unknown', '审核详情无效，请刷新并核对运行状态。')
    detail.value = value
  } catch (cause: unknown) {
    if (!mounted) return
    const problem = cause instanceof HttpError ? cause : new HttpError('unknown', '无法读取审核详情')
    error.value = problem.status === 404 ? '项目或运行不存在，或已删除。' : problem.status === 409 ? '审核状态已变化，请返回运行监控核对。' : problem.message
  } finally { if (mounted) loading.value = false }
}
async function submit(decision: ApprovalDecision): Promise<void> {
  if (submitting.value || (locked.value && JSON.stringify(decision) !== JSON.stringify(pending.value)) || (!detail.value && !locked.value)) return
  if (decision.decision === 'approve' && (!decision.approved_ids.length || new Set(decision.approved_ids).size !== decision.approved_ids.length || (!locked.value && !decision.approved_ids.every(id => detail.value?.item_ids.includes(id))))) return
  try { sessionStorage.setItem(storageKey, JSON.stringify(decision)) } catch { error.value = '无法安全保存待确认决定，请启用浏览器会话存储后重试。'; return }
  pending.value = decision; locked.value = true; submitting.value = true; error.value = ''
  try {
    const result = await store.sendApproval(props.projectId, props.runId, props.threadId, decision)
    if (result.thread_id !== props.threadId) throw new HttpError('unknown', '审核结果无法确认，请先核对运行状态。')
    try { sessionStorage.removeItem(storageKey) } catch { /* no secret data */ }
    if (mounted) await router.push({ name: 'workflow-run', params: { projectId: props.projectId, runId: result.run_id } })
  } catch (cause: unknown) {
    if (!mounted) return
    const problem = cause instanceof HttpError ? cause : new HttpError('unknown', '审核结果无法确认')
    if (problem.code === 'writing_storage_missing') {
      error.value = '写作数据库尚未部署，本次决定未提交；选择已保留，完成部署后可重试相同决定。'
    } else if (problem.status === 422 || problem.status === 404) {
      try { sessionStorage.removeItem(storageKey) } catch { /* unavailable */ }
      pending.value = null; locked.value = false
      error.value = problem.status === 404 ? '项目或运行不存在，或已删除。' : '审核请求不符合要求，请检查后重试。'
    } else if (problem.status === 409) error.value = '审核状态或决定发生冲突，请返回运行监控核对服务端状态。'
    else error.value = '审核结果尚未确认，请核对服务端运行状态；只能重试本次相同决定。'
  } finally { if (mounted) submitting.value = false }
}
async function reject(): Promise<void> {
  if (locked.value || submitting.value || confirming.value) return
  confirming.value = true
  try { await ElMessageBox.confirm('确定拒绝全部审核对象？工作流将取消本次执行。', '确认拒绝', { confirmButtonText: '确认拒绝', cancelButtonText: '返回', type: 'warning' }) }
  catch { return } finally { confirming.value = false }
  await submit({ decision: 'reject' })
}
onMounted(() => { restorePending(); void load() })
onBeforeUnmount(() => { mounted = false; controller.abort() })
</script>

<template>
  <section class="approval card" aria-labelledby="approval-title">
    <h2 id="approval-title">{{ label }}</h2>
    <p v-if="detail && !detail.items && !detail.sources">当前为 Mock 审核数据，仅展示后端返回的 ID。</p>
    <p v-else-if="detail?.items">请审核真实 Planner 生成的任务；批准后将使用所选任务标题及描述、预期产出的部分文字查询 Crossref。</p>
    <p v-else-if="detail?.sources">以下是 Crossref 返回的候选文献，检索命中不代表相关性或研究结论已核实。批准后会调用模型生成初稿并可能产生费用；至少选择一条有摘要的资料。系统使用已批准资料的可用摘要，摘要过长时会停止并提示处理。</p>
    <p v-if="loading" role="status">正在读取审核详情…</p>
    <p v-if="error" class="problem" role="alert">{{ error }}</p>
    <template v-if="detail">
      <p>{{ detail.approval_type === 'task_approval' ? '请选择允许执行的任务。' : '请选择允许写作使用的资料。' }}</p>
      <div class="selection"><button type="button" :disabled="locked || submitting" @click="selected = [...detail.item_ids]">全选</button><button type="button" :disabled="locked || submitting" @click="selected = []">取消选择</button><span>已选 {{ selected.length }} / {{ detail.item_ids.length }}</span></div>
      <ul><li v-for="(id, index) in detail.item_ids" :key="id"><label><input v-model="selected" type="checkbox" :value="id" :disabled="locked || submitting"><span v-if="detail.items"><strong>{{ detail.items[index]?.title }}</strong><br>{{ detail.items[index]?.description }}<br>预期产出：{{ detail.items[index]?.expected_output }}<br>{{ id }}</span><span v-else-if="detail.sources"><strong>{{ detail.sources[index]?.title }}</strong><br><span v-if="detail.sources[index]?.relevance_hint">{{ relevanceText(detail.sources[index]?.relevance_hint) }}，请人工核验<br></span><span v-if="(detail.sources[index]?.same_title_count ?? 1) > 1">疑似同标题不同 DOI（{{ detail.sources[index]?.same_title_count }} 条），请核对版本<br></span>作者：{{ detail.sources[index]?.authors.join('、') || '暂无' }}；年份：{{ detail.sources[index]?.year || '暂无' }}；期刊：{{ detail.sources[index]?.journal || '暂无' }}<br>DOI：{{ detail.sources[index]?.doi }}<br>摘要：{{ detail.sources[index]?.abstract || '暂无' }}<br>关联任务：{{ detail.sources[index]?.tasks.map(t => t.title).join('；') }}<br><a :href="detail.sources[index]?.url" target="_blank" rel="noopener noreferrer" @click.stop>查看文献来源</a></span><span v-else>{{ id }}</span></label></li></ul>
      <p v-if="locked" class="warning">审核决定待确认，选择已锁定。请先核对服务端状态，可重试相同决定。</p>
      <div class="actions">
        <ElButton v-if="!locked" type="primary" :loading="submitting" :disabled="!validSelection || submitting || confirming || (!!detail.sources && !detail.sources.some(source => selected.includes(source.source_id) && !!source.abstract && source.abstract.trim().length >= 8))" @click="submit({ decision: 'approve', approved_ids: [...selected], ...(detail.items ? { continue_research: true } : detail.sources ? { continue_writing: true, segmented_writing: true } : {}) })">{{ detail.items ? '批准并启动资料检索' : detail.sources ? '批准并生成初稿' : '批准所选' }}</ElButton>
        <ElButton v-if="!locked" type="danger" plain :disabled="submitting || confirming" @click="reject">拒绝全部</ElButton>
        <ElButton v-if="locked && pending" type="primary" :loading="submitting" :disabled="submitting || (pending.decision === 'approve' && !pending.approved_ids.every(id => detail?.item_ids.includes(id)))" @click="submit(pending)">重试相同决定</ElButton>
      </div>
    </template>
    <div v-else-if="!loading" class="actions"><ElButton @click="load">重新读取审核详情</ElButton><ElButton v-if="locked && pending" type="primary" :loading="submitting" :disabled="submitting" @click="submit(pending)">重试相同决定</ElButton></div>
  </section>
</template>
<style scoped>
.approval { margin-top:18px; } h2 { margin:0; color:#263f4d; font-size:16px; } p { color:#657e8b; font-size:13px; line-height:1.6; } .selection { display:flex; align-items:center; gap:15px; flex-wrap:wrap; font-size:13px; color:#617b89; } .selection button { background:none; border:0; color:#246d8a; padding:5px 0; cursor:pointer; } .selection button:disabled { opacity:.5; cursor:not-allowed; } ul { list-style:none; padding:0; margin:12px 0 20px; display:grid; gap:8px; } li { border:1px solid #dce6ec; border-radius:8px; } label { display:flex; align-items:center; gap:10px; padding:12px; cursor:pointer; } label span { overflow-wrap:anywhere; color:#294a5b; font-size:13px; } input { accent-color:#246d8a; flex:none; } .problem { color:#9c4438; } .warning { color:#8c6a2d; } .actions { display:flex; flex-wrap:wrap; gap:8px; } .actions :deep(.el-button + .el-button) { margin-left:0; }
</style>
