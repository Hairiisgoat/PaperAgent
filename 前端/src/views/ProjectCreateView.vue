<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { onBeforeRouteLeave, useRouter } from 'vue-router'
import { ElButton } from 'element-plus'
import { ArrowLeft, DocumentAdd } from '@element-plus/icons-vue'
import 'element-plus/es/components/button/style/css'
import { useProjectsStore } from '../stores/projects'
import type { ProjectValidationField } from '../services/http'
import { trimProjectText as trimInput, validateProjectField } from '../utils/projectForm'

const router = useRouter()
const store = useProjectsStore()
const form = reactive({ title: '', topic: '', citation_style: '' })
type EditableField = keyof typeof form
const errors = reactive<Record<EditableField, string>>({ title: '', topic: '', citation_style: '' })
const invalidResponse = ref(false)
const submissionError = computed(() => {
  if (invalidResponse.value) return '服务器已响应，但无法确认项目地址。请先返回列表检查，避免重复创建。'
  const error = store.createError
  if (!error) return ''
  if (error.status === 422) return '部分输入未通过验证，请检查字段后重试。'
  if (error.status === 409) return '项目状态发生冲突，请检查后重试。'
  if (error.status === 503) return '服务暂不可用，请先检查项目列表确认提交结果。'
  if (error.kind === 'network' || error.kind === 'timeout') return '连接中断或超时，请先检查项目列表确认提交结果。'
  return '提交结果未确认，请先检查项目列表后再决定是否重试。'
})

function validate(field: EditableField): boolean {
  errors[field] = validateProjectField(field, form[field])
  return !errors[field]
}
function fieldError(field: EditableField): string {
  return errors[field] || store.createError?.fieldErrors[field as ProjectValidationField] || ''
}
function clearField(field: EditableField): void {
  errors[field] = ''
  if (store.createError) delete store.createError.fieldErrors[field]
}
async function submit(): Promise<void> {
  if (store.createLoading) return
  invalidResponse.value = false
  store.createError = null
  const valid = (['title', 'topic', 'citation_style'] as EditableField[]).map(validate).every(Boolean)
  if (!valid) {
    const first = (['title', 'topic', 'citation_style'] as EditableField[]).find(field => errors[field])
    const ids: Record<EditableField, string> = { title: 'title', topic: 'topic', citation_style: 'citation-style' }
    if (first) document.getElementById(ids[first])?.focus()
    return
  }
  const project = await store.create({ title: trimInput(form.title), topic: trimInput(form.topic), citation_style: trimInput(form.citation_style), paper_type: 'course_paper' })
  if (!project) return
  if (typeof project.id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(project.id)) {
    invalidResponse.value = true
    return
  }
  await router.push({ name: 'project-detail', params: { projectId: project.id } })
}
onMounted(() => { store.createError = null })
onBeforeRouteLeave(() => !store.createLoading)
</script>

<template>
  <section class="create-page" aria-labelledby="create-title">
    <RouterLink class="back-link" to="/projects"><ArrowLeft aria-hidden="true" /> 返回项目列表</RouterLink>
    <header class="intro">
      <h1 id="create-title">创建论文项目</h1>
      <p>填写基础信息，为论文研究建立一个独立空间。</p>
    </header>
    <form class="card" novalidate @submit.prevent="submit">
      <div class="card-head"><span class="icon-box"><DocumentAdd aria-hidden="true" /></span><div><h2>基本信息</h2><p>请填写论文的基本信息。</p></div></div>
      <div v-if="submissionError" class="form-alert" role="alert">{{ submissionError }}</div>
      <div class="field"><label for="title">项目名称 <span>*</span></label><input id="title" v-model="form.title" type="text" autocomplete="off" placeholder="例如：人工智能与教育研究" :aria-invalid="!!fieldError('title')" :aria-describedby="fieldError('title') ? 'title-hint title-error' : 'title-hint'" @blur="validate('title')" @input="clearField('title')" /><p id="title-hint" class="hint">为项目起一个便于识别的名称，最多 200 个字符。</p><p v-if="fieldError('title')" id="title-error" class="field-error" role="alert">{{ fieldError('title') }}</p></div>
      <div class="field"><label for="topic">论文主题 <span>*</span></label><textarea id="topic" v-model="form.topic" rows="5" placeholder="描述你希望研究的主题或问题" :aria-invalid="!!fieldError('topic')" :aria-describedby="fieldError('topic') ? 'topic-hint topic-error' : 'topic-hint'" @blur="validate('topic')" @input="clearField('topic')"></textarea><p id="topic-hint" class="hint">简要描述论文的研究方向。</p><p v-if="fieldError('topic')" id="topic-error" class="field-error" role="alert">{{ fieldError('topic') }}</p></div>
      <div class="field"><label for="paper-type">论文类型</label><input id="paper-type" value="课程论文" readonly aria-describedby="paper-type-hint" /><p id="paper-type-hint" class="hint">当前开放课程论文场景。</p></div>
      <div class="field"><label for="citation-style">引用格式 <span>*</span></label><input id="citation-style" v-model="form.citation_style" type="text" autocomplete="off" placeholder="例如：GB/T 7714 或 APA" :aria-invalid="!!fieldError('citation_style')" :aria-describedby="fieldError('citation_style') ? 'citation-hint citation-error' : 'citation-hint'" @blur="validate('citation_style')" @input="clearField('citation_style')" /><p id="citation-hint" class="hint">请输入所需引用格式，最多 40 个字符。</p><p v-if="fieldError('citation_style')" id="citation-error" class="field-error" role="alert">{{ fieldError('citation_style') }}</p></div>
      <div class="actions"><ElButton :disabled="store.createLoading" @click="router.push('/projects')">返回</ElButton><ElButton native-type="submit" type="primary" :loading="store.createLoading" :disabled="store.createLoading">{{ store.createLoading ? '正在创建' : '创建项目' }}</ElButton></div>
    </form>
  </section>
</template>

<style scoped>
.create-page {
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

.field input::placeholder,
.field textarea::placeholder {
  color: var(--pa-text-faint);
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

.field input[readonly] {
  background: var(--pa-bg-inset);
  color: var(--pa-text-meta);
  cursor: default;
}

.field input[aria-invalid="true"],
.field textarea[aria-invalid="true"] {
  border-color: var(--pa-color-danger-strong);
}

.hint {
  margin: 5px 0 0;
  color: var(--pa-text-meta);
  font-size: 12px;
}

.field-error {
  margin: 5px 0 0;
  color: var(--pa-color-danger);
  font-size: 12px;
}

.form-alert {
  margin-bottom: 18px;
  padding: 10px 14px;
  border: 1px solid var(--pa-border-danger);
  border-radius: var(--pa-radius-control);
  color: var(--pa-color-danger);
  background: var(--pa-bg-danger);
  font-size: 13px;
  line-height: 1.6;
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

.create-page :deep(.el-button) {
  border-radius: var(--pa-radius-control);
  font-weight: 500;
  height: 32px;
  padding: 0 16px;
}

.create-page :deep(.el-button:focus-visible) {
  outline: none;
  box-shadow: var(--pa-ring-primary);
}

@media (max-width: 640px) {
  .card { padding: 18px 16px; }
}
</style>
