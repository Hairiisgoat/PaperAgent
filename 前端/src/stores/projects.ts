import { defineStore } from 'pinia'
import { ref } from 'vue'
import { HttpError } from '../services/http'
import { archiveProject, createProject, deleteProject, getProject, getProjects, restoreProject, updateProject } from '../services/projects'
import type { ProjectCreate, ProjectEdit, ProjectFilter, ProjectResponse } from '../types/projects'

export const useProjectsStore = defineStore('projects', () => {
  const projects = ref<ProjectResponse[]>([])
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(20)
  const lifecycleStatus = ref<ProjectFilter>(undefined)
  const loading = ref(false)
  const error = ref<HttpError | null>(null)
  const loaded = ref(false)
  const currentProject = ref<ProjectResponse | null>(null)
  const detailLoading = ref(false)
  const detailError = ref<HttpError | null>(null)
  const mutationLoading = ref(false)
  const mutationError = ref<HttpError | null>(null)
  const createLoading = ref(false)
  const createError = ref<HttpError | null>(null)
  let detailController: AbortController | undefined
  let detailRequestNumber = 0
  let controller: AbortController | undefined
  let requestNumber = 0
  let activeKey: string | undefined

  async function load(): Promise<void> {
    const key = `${page.value}:${pageSize.value}:${lifecycleStatus.value ?? 'all'}`
    if (loading.value && activeKey === key) return
    controller?.abort()
    const current = ++requestNumber
    const currentController = new AbortController()
    controller = currentController
    activeKey = key
    loading.value = true
    error.value = null
    try {
      const result = await getProjects({ page: page.value, page_size: pageSize.value, lifecycle_status: lifecycleStatus.value }, currentController.signal)
      if (current !== requestNumber) return
      projects.value = result.items
      total.value = result.total
      loaded.value = true
    } catch (cause: unknown) {
      if (current !== requestNumber) return
      if (cause instanceof HttpError && cause.kind === 'cancelled') return
      projects.value = []
      total.value = 0
      error.value = cause instanceof HttpError ? cause : new HttpError('unknown', '请求未成功，请稍后重试')
    } finally {
      if (current === requestNumber) {
        loading.value = false
        controller = undefined
        activeKey = undefined
      }
    }
  }

  function setFilter(value: ProjectFilter): void {
    if (lifecycleStatus.value === value) return
    lifecycleStatus.value = value
    page.value = 1
    void load()
  }

  function setPage(value: number): void {
    const maxPage = Math.max(1, Math.ceil(total.value / pageSize.value))
    if (value < 1 || value > maxPage || value === page.value) return
    page.value = value
    void load()
  }

  function cancel(): void {
    requestNumber++
    controller?.abort()
    controller = undefined
    activeKey = undefined
    loading.value = false
  }

  async function create(data: ProjectCreate): Promise<ProjectResponse | null> {
    if (createLoading.value) return null
    createLoading.value = true
    createError.value = null
    try {
      const project = await createProject(data)
      currentProject.value = project
      return project
    } catch (cause: unknown) {
      createError.value = cause instanceof HttpError ? cause : new HttpError('unknown', '提交结果未确认，请先检查项目列表')
      return null
    } finally {
      createLoading.value = false
    }
  }

  async function loadDetail(projectId: string): Promise<void> {
    detailController?.abort()
    const current = ++detailRequestNumber
    const nextController = new AbortController()
    detailController = nextController
    currentProject.value = null
    detailError.value = null
    detailLoading.value = true
    try {
      const project = await getProject(projectId, nextController.signal)
      if (current !== detailRequestNumber) return
      if (project === null) return
      if (typeof project !== 'object' || Array.isArray(project)) throw new HttpError('unknown', '项目数据不可用')
      currentProject.value = project
    } catch (cause: unknown) {
      if (current !== detailRequestNumber) return
      if (cause instanceof HttpError && cause.kind === 'cancelled') return
      detailError.value = cause instanceof HttpError ? cause : new HttpError('unknown', '项目数据不可用')
    } finally {
      if (current === detailRequestNumber) {
        detailLoading.value = false
        detailController = undefined
      }
    }
  }

  function cancelDetail(): void {
    detailRequestNumber++
    detailController?.abort()
    detailController = undefined
    detailLoading.value = false
  }

  async function mutate(operation: 'edit' | 'archive' | 'restore' | 'delete', projectId: string, data?: ProjectEdit): Promise<boolean> {
    if (mutationLoading.value) return false
    mutationLoading.value = true
    mutationError.value = null
    try {
      if (operation === 'edit') {
        if (!data || !Object.keys(data).length) throw new Error('Missing edit data')
        currentProject.value = await updateProject(projectId, data)
      } else if (operation === 'archive') {
        currentProject.value = await archiveProject(projectId)
      } else if (operation === 'restore') {
        currentProject.value = await restoreProject(projectId)
      } else {
        await deleteProject(projectId)
        currentProject.value = null
        page.value = 1
      }
      // No stale list data is shown while the next GET refresh is pending.
      cancel()
      projects.value = []
      total.value = 0
      loaded.value = false
      return true
    } catch (cause: unknown) {
      mutationError.value = cause instanceof HttpError ? cause : new HttpError('unknown', '操作结果未确认')
      return false
    } finally {
      mutationLoading.value = false
    }
  }
  return { projects, total, page, pageSize, lifecycleStatus, loading, error, loaded, load, setFilter, setPage, cancel,
    currentProject, detailLoading, detailError, createLoading, createError, create, loadDetail, cancelDetail, mutationLoading, mutationError, mutate }
})
