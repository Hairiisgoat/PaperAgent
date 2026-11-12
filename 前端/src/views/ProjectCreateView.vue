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
    <div class="intro"><p class="eyebrow">WORKSPACE / NEW PROJECT</p><h1 id="create-title">创建论文项目</h1><p>填写基础信息，为论文研究建立一个独立空间。</p></div>
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
.create-page { max-width: 850px; margin: 0 auto; }
.back-link { display: inline-flex; align-items: center; gap: 7px; margin-bottom: 30px; color: #537689; font-size: 13px; text-decoration: none; }
.back-link svg { width: 15px; height: 15px; }
.eyebrow { margin: 0 0 12px; color: #6c8c9b; font-size: 10px; font-weight: 700; letter-spacing: .16em; }
h1 { margin: 0; font-size: 30px; color: #173247; letter-spacing: -.03em; }
.intro > p:last-child { margin: 12px 0 28px; color: #718391; font-size: 14px; }
.card { padding: 30px 34px; background: #fff; border: 1px solid #e1e8ed; border-radius: 12px; box-shadow: 0 8px 28px rgba(32, 63, 82, .035); }
.card-head { display: flex; align-items: center; gap: 13px; padding-bottom: 25px; margin-bottom: 25px; border-bottom: 1px solid #edf1f4; }
.icon-box { display: grid; place-items: center; width: 42px; height: 42px; border-radius: 10px; background: #edf5f8; color: #53849a; }
.icon-box svg { width: 21px; height: 21px; }
h2 { margin: 0; color: #263f4d; font-size: 16px; }.card-head p { margin: 6px 0 0; color: #83939e; font-size: 12px; }
.field { margin-bottom: 24px; }.field label { display: block; margin-bottom: 9px; color: #324d5d; font-weight: 600; font-size: 13px; }.field label span { color: #b64747; }
.field input, .field textarea { display: block; width: 100%; border: 1px solid #d8e2e8; border-radius: 7px; padding: 11px 12px; color: #233e4f; background: #fff; font: inherit; font-size: 14px; }
.field textarea { resize: vertical; min-height: 110px; }.field input[readonly] { background: #f5f7f9; color: #617985; }.field input[aria-invalid="true"], .field textarea[aria-invalid="true"] { border-color: #bc5555; }
.hint { margin: 7px 0 0; color: #788c98; font-size: 12px; }.field-error { margin: 7px 0 0; color: #ad3939; font-size: 12px; }
.form-alert { margin-bottom: 22px; padding: 12px 14px; border: 1px solid #f1d7ca; border-radius: 7px; color: #8d4c32; background: #fff7f1; font-size: 13px; line-height: 1.6; }
.actions { display: flex; justify-content: flex-end; gap: 6px; padding-top: 24px; border-top: 1px solid #edf1f4; }
@media (max-width: 640px) { .card { padding: 24px 18px; } h1 { font-size: 25px; } }
</style>
