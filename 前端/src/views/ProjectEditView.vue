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
    <header class="intro">
      <h1 id="edit-title">编辑项目信息</h1>
      <p>更新论文的基本信息。</p>
    </header>
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
.edit-page {
  max-width: 760px;
  margin: 0 auto;
  color: var(--pa-text-body);
}

.back-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 14px;
  padding: 4px 8px 4px 4px;
  margin-left: -4px;
  border-radius: var(--pa-radius-control);
  color: var(--pa-text-meta);
  font-size: 13px;
  text-decoration: none;
  transition: color 0.15s, background-color 0.15s;
}

.back-link:hover {
  color: var(--el-color-primary);
  background: var(--pa-bg-hover);
}

.back-link svg {
  width: 14px;
  height: 14px;
}

.intro {
  margin-bottom: 20px;
}

h1 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--pa-color-heading);
  letter-spacing: -0.02em;
}

.intro p {
  margin: 4px 0 0;
  color: var(--pa-text-meta);
  font-size: 13px;
}

.card {
  padding: var(--pa-space-card);
  background: var(--pa-bg-surface);
  border: var(--pa-border);
  border-radius: var(--pa-radius-card);
  box-shadow: var(--pa-shadow-card);
}

.state {
  min-height: 200px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  text-align: center;
  color: var(--pa-text-meta);
  font-size: 13px;
}

.state p {
  margin: 0;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  padding-bottom: 16px;
  margin-bottom: 20px;
  border-bottom: var(--pa-border);
}

.icon-box {
  display: grid;
  place-items: center;
  width: 34px;
  height: 34px;
  border-radius: 7px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-8);
  color: var(--el-color-primary);
  flex: none;
}

.icon-box svg {
  width: 17px;
  height: 17px;
}

h2 {
  margin: 0;
  color: var(--pa-color-heading);
  font-size: 15px;
  font-weight: 600;
}

.card-head p {
  margin: 3px 0 0;
  color: var(--pa-text-meta);
  font-size: 12px;
}

.field {
  margin-bottom: 18px;
}

.field label {
  display: block;
  margin-bottom: 6px;
  color: var(--pa-color-heading);
  font-weight: 600;
  font-size: 13px;
}

.field label span {
  color: var(--pa-color-danger-strong);
}

.field input,
.field textarea {
  display: block;
  width: 100%;
  border: var(--pa-border);
  border-radius: var(--pa-radius-control);
  padding: 8px 12px;
  color: var(--pa-color-heading);
  background: var(--pa-bg-surface);
  font: inherit;
  font-size: 13px;
  line-height: 1.5;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.field input:hover:not([readonly]),
.field textarea:hover:not([readonly]) {
  border-color: var(--el-border-color-dark);
}

.field input:focus-visible,
.field textarea:focus-visible {
  outline: none;
  border-color: var(--el-color-primary);
  box-shadow: var(--pa-ring-primary);
}

.field textarea {
  resize: vertical;
  min-height: 108px;
  line-height: 1.65;
}

.field input[readonly],
.field textarea[readonly] {
  background: var(--pa-bg-inset);
  color: var(--pa-text-meta);
  cursor: default;
}

.field input[aria-invalid="true"],
.field textarea[aria-invalid="true"] {
  border-color: var(--pa-color-danger-strong);
}

.field-error {
  margin: 5px 0 0;
  color: var(--pa-color-danger);
  font-size: 12px;
}

.alert {
  margin-bottom: 18px;
  padding: 10px 14px;
  border: 1px solid var(--pa-border-amber);
  border-left: 3px solid var(--pa-color-amber-strong);
  border-radius: var(--pa-radius-control);
  color: var(--pa-color-amber);
  background: var(--pa-bg-amber);
  font-size: 13px;
  line-height: 1.6;
}

.link-button {
  margin-left: 6px;
  border: 0;
  background: transparent;
  color: var(--el-color-primary);
  font-weight: 600;
  text-decoration: underline;
  cursor: pointer;
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  padding-top: 18px;
  border-top: var(--pa-border);
}

.actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.edit-page :deep(.el-button) {
  border-radius: var(--pa-radius-control);
  font-weight: 500;
  height: 32px;
  padding: 0 16px;
}

.edit-page :deep(.el-button:focus-visible) {
  outline: none;
  box-shadow: var(--pa-ring-primary);
}

@media (max-width: 640px) {
  .card { padding: 18px 16px; }
}
</style>
