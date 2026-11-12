<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { onBeforeRouteLeave, onBeforeRouteUpdate, useRoute, useRouter } from 'vue-router'
import { ElButton } from 'element-plus'
import { ArrowLeft, Document } from '@element-plus/icons-vue'
import 'element-plus/es/components/button/style/css'
import { useProjectsStore } from '../stores/projects'
import type { ProjectEdit, ProjectResponse } from '../types/projects'
import { paperTypeLabel } from '../utils/projectDisplay'
import { editableFields, trimProjectText, validateProjectField, type EditableField } from '../utils/projectForm'

const route = useRoute()
const router = useRouter()
const store = useProjectsStore()
const projectId = computed(() => typeof route.params.projectId === 'string' ? route.params.projectId : '')
const form = reactive<Record<EditableField, string>>({ title: '', topic: '', citation_style: '' })
const errors = reactive<Record<EditableField, string>>({ title: '', topic: '', citation_style: '' })
const baseline = ref<Record<EditableField, string> | null>(null)
const initialized = ref(false)
const reviewing = ref(false)
const reconciling = ref(false)
let sequence = 0
const isDirty = computed(() => !!baseline.value && editableFields.some(field => form[field] !== baseline.value?.[field]))
const changes = computed<ProjectEdit>(() => {
  const result: ProjectEdit = {}
  if (baseline.value) for (const field of editableFields) {
    const value = trimProjectText(form[field])
    if (value !== baseline.value[field]) result[field] = value
  }
  return result
})
const canSave = computed(() => initialized.value && store.currentProject?.lifecycle_status === 'active' && !store.detailError && !store.detailLoading && !store.mutationLoading && !reconciling.value && !reviewing.value && Object.keys(changes.value).length > 0)
const message = computed(() => {
  if (store.detailError?.status === 404) return '项目不存在或已删除，当前草稿未提交。'
  if (store.detailError?.status === 503) return '服务暂时不可用，无法读取最新状态；当前草稿已保留。'
  if (store.detailError) return '无法读取最新项目状态，请重试查询；当前草稿已保留。'
  if (store.currentProject?.lifecycle_status === 'archived') return '项目已归档，恢复后才能编辑；当前草稿已保留。'
  const error = store.mutationError
  if (reviewing.value) {
    const reason = error?.status === 409 ? '项目状态已变化。' : error?.status === 503 ? '服务暂时不可用。' : error?.kind === 'network' || error?.kind === 'timeout' ? '连接中断或超时，提交结果未确认。' : '提交结果未确认。'
    return `${reason}已读取最新项目，请核对草稿后再继续。`
  }
  if (error?.status === 422) return '部分输入未通过验证，请检查字段。'
  if (error) return '保存未完成，请检查输入或稍后重试。'
  return ''
})
function fields(project: ProjectResponse): Record<EditableField, string> {
  return { title: project.title, topic: project.topic, citation_style: project.citation_style }
}
async function initialize(id: string): Promise<void> {
  const current = ++sequence
  initialized.value = false
  baseline.value = null
  reviewing.value = false
  store.mutationError = null
  await store.loadDetail(id)
  if (current !== sequence || id !== projectId.value) return
  if (store.currentProject) {
    Object.assign(form, fields(store.currentProject))
    baseline.value = fields(store.currentProject)
    initialized.value = true
  }
}
watch(projectId, id => { if (id) void initialize(id) }, { immediate: true })
function validate(field: EditableField): boolean {
  errors[field] = validateProjectField(field, form[field])
  return !errors[field]
}
function fieldError(field: EditableField): string { return errors[field] || store.mutationError?.fieldErrors[field] || '' }
function clearField(field: EditableField): void {
  errors[field] = ''
  if (store.mutationError) delete store.mutationError.fieldErrors[field]
}
async function reconcile(): Promise<void> {
  reconciling.value = true
  reviewing.value = true
  try { await store.loadDetail(projectId.value) } finally { reconciling.value = false }
}
function acknowledgeLatest(): void {
  if (!store.currentProject || store.detailError || store.currentProject.lifecycle_status !== 'active') return
  baseline.value = fields(store.currentProject)
  reviewing.value = false
  store.mutationError = null
}
async function submit(): Promise<void> {
  if (!canSave.value || !editableFields.map(validate).every(Boolean)) return
  const ok = await store.mutate('edit', projectId.value, changes.value)
  if (ok) {
    baseline.value = { ...form }
    await router.push({ name: 'project-detail', params: { projectId: projectId.value } })
  } else {
    const e = store.mutationError
    if (!e || e.status === 404 || e.status === 409 || e.status === 503 || e.kind === 'network' || e.kind === 'timeout' || e.kind === 'unknown' || (e.status !== undefined && e.status >= 500)) await reconcile()
  }
}
function mayLeave(): boolean {
  if (store.mutationLoading || reconciling.value) return false
  return !isDirty.value || window.confirm('有未保存的修改，确定离开吗？')
}
function beforeUnload(event: BeforeUnloadEvent): void {
  if (isDirty.value || store.mutationLoading || reconciling.value) { event.preventDefault(); event.returnValue = '' }
}
onBeforeRouteLeave(mayLeave)
onBeforeRouteUpdate(mayLeave)
onMounted(() => window.addEventListener('beforeunload', beforeUnload))
onBeforeUnmount(() => { sequence++; store.cancelDetail(); window.removeEventListener('beforeunload', beforeUnload) })
</script>

<template>
  <section class="edit-page" aria-labelledby="edit-title">
    <RouterLink class="back-link" :to="{ name: 'project-detail', params: { projectId } }"><ArrowLeft aria-hidden="true" /> 返回项目详情</RouterLink>
    <div class="intro"><p class="eyebrow">WORKSPACE / EDIT PROJECT</p><h1 id="edit-title">编辑项目信息</h1><p>更新论文的基本信息。</p></div>
    <div v-if="!initialized && store.detailLoading" class="card state" role="status">正在加载项目…</div>
    <div v-else-if="!initialized && !store.currentProject" class="card state" role="alert"><p>{{ store.detailError?.status === 404 ? '项目不存在或已删除' : store.detailError?.status === 422 ? '项目地址无效' : store.detailError?.status === 503 ? '服务暂时不可用，请稍后重试。' : '无法加载项目，请检查网络或后端服务。' }}</p><ElButton v-if="store.detailError?.status !== 404" @click="initialize(projectId)">重试</ElButton></div>
    <form v-else class="card" novalidate @submit.prevent="submit">
      <div class="card-head"><span class="icon-box"><Document aria-hidden="true" /></span><div><h2>基本信息</h2><p>论文类型只读，论文要求保持原值。</p></div></div>
      <div v-if="message" class="alert" role="alert">{{ message }} <button v-if="store.detailError && reviewing" class="link-button" type="button" :disabled="reconciling" @click="reconcile">重试读取</button><button v-else-if="reviewing && store.currentProject?.lifecycle_status === 'active'" class="link-button" type="button" @click="acknowledgeLatest">已检查，继续编辑</button></div>
      <div class="field"><label for="edit-title-field">项目名称 <span>*</span></label><input id="edit-title-field" v-model="form.title" :readonly="store.currentProject?.lifecycle_status !== 'active' || store.mutationLoading || reconciling" :aria-invalid="!!fieldError('title')" :aria-describedby="fieldError('title') ? 'edit-title-error' : undefined" @blur="validate('title')" @input="clearField('title')" /><p v-if="fieldError('title')" id="edit-title-error" class="field-error" role="alert">{{ fieldError('title') }}</p></div>
      <div class="field"><label for="edit-topic">论文主题 <span>*</span></label><textarea id="edit-topic" v-model="form.topic" rows="5" :readonly="store.currentProject?.lifecycle_status !== 'active' || store.mutationLoading || reconciling" :aria-invalid="!!fieldError('topic')" :aria-describedby="fieldError('topic') ? 'edit-topic-error' : undefined" @blur="validate('topic')" @input="clearField('topic')"></textarea><p v-if="fieldError('topic')" id="edit-topic-error" class="field-error" role="alert">{{ fieldError('topic') }}</p></div>
      <div class="field"><label for="edit-paper-type">论文类型</label><input id="edit-paper-type" :value="paperTypeLabel(store.currentProject?.paper_type ?? '')" readonly /></div>
      <div class="field"><label for="edit-citation">引用格式 <span>*</span></label><input id="edit-citation" v-model="form.citation_style" :readonly="store.currentProject?.lifecycle_status !== 'active' || store.mutationLoading || reconciling" :aria-invalid="!!fieldError('citation_style')" :aria-describedby="fieldError('citation_style') ? 'edit-citation-error' : undefined" @blur="validate('citation_style')" @input="clearField('citation_style')" /><p v-if="fieldError('citation_style')" id="edit-citation-error" class="field-error" role="alert">{{ fieldError('citation_style') }}</p></div>
      <div class="actions"><ElButton :disabled="store.mutationLoading || reconciling" @click="router.push({ name: 'project-detail', params: { projectId } })">取消</ElButton><ElButton native-type="submit" type="primary" :loading="store.mutationLoading" :disabled="!canSave">保存修改</ElButton></div>
    </form>
  </section>
</template>

<style scoped>
.edit-page { max-width: 850px; margin: 0 auto; }.back-link { display: inline-flex; align-items: center; gap: 7px; margin-bottom: 30px; color: #537689; font-size: 13px; text-decoration: none; }.back-link svg { width: 15px; height: 15px; }
.eyebrow { margin: 0 0 12px; color: #6c8c9b; font-size: 10px; font-weight: 700; letter-spacing: .16em; }h1 { margin: 0; font-size: 30px; color: #173247; }.intro > p:last-child { margin: 12px 0 28px; color: #718391; font-size: 14px; }
.card { padding: 30px 34px; background: #fff; border: 1px solid #e1e8ed; border-radius: 12px; }.state { min-height: 200px; text-align: center; color: #617985; }.card-head { display: flex; align-items: center; gap: 13px; padding-bottom: 25px; margin-bottom: 25px; border-bottom: 1px solid #edf1f4; }.icon-box { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 10px; background: #edf5f8; color: #53849a; }.icon-box svg { width: 21px; height: 21px; }h2 { margin: 0; color: #263f4d; font-size: 16px; }.card-head p { margin: 6px 0 0; color: #83939e; font-size: 12px; }
.field { margin-bottom: 24px; }.field label { display: block; margin-bottom: 9px; color: #324d5d; font-weight: 600; font-size: 13px; }.field label span { color: #b64747; }.field input, .field textarea { display: block; width: 100%; border: 1px solid #d8e2e8; border-radius: 7px; padding: 11px 12px; color: #233e4f; background: #fff; font: inherit; font-size: 14px; }.field textarea { resize: vertical; min-height: 110px; }.field input[readonly], .field textarea[readonly] { background: #f5f7f9; color: #617985; }.field input[aria-invalid="true"], .field textarea[aria-invalid="true"] { border-color: #bc5555; }.field-error { margin: 7px 0 0; color: #ad3939; font-size: 12px; }.alert { margin-bottom: 22px; padding: 12px 14px; border: 1px solid #f1d7ca; border-radius: 7px; color: #8d4c32; background: #fff7f1; font-size: 13px; line-height: 1.6; }.link-button { border: 0; background: transparent; color: #245b78; text-decoration: underline; cursor: pointer; }.actions { display: flex; justify-content: flex-end; gap: 6px; padding-top: 24px; border-top: 1px solid #edf1f4; }@media (max-width: 640px) { .card { padding: 24px 18px; } h1 { font-size: 25px; } }
</style>
