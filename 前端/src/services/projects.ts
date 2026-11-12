import { http } from './http'
import type { ProjectCreate, ProjectEdit, ProjectListQuery, ProjectPage, ProjectResponse } from '../types/projects'

export async function getProjects(query: ProjectListQuery, signal?: AbortSignal): Promise<ProjectPage> {
  const params: ProjectListQuery = { page: query.page, page_size: query.page_size }
  if (query.lifecycle_status) params.lifecycle_status = query.lifecycle_status
  const response = await http.get<ProjectPage>('/projects', { params, signal })
  return response.data
}


export async function createProject(data: ProjectCreate): Promise<ProjectResponse> {
  const response = await http.post<ProjectResponse>('/projects', data)
  return response.data
}

export async function getProject(projectId: string, signal?: AbortSignal): Promise<ProjectResponse> {
  const response = await http.get<ProjectResponse>(`/projects/${encodeURIComponent(projectId)}`, { signal })
  return response.data
}

export async function updateProject(projectId: string, data: ProjectEdit): Promise<ProjectResponse> {
  const response = await http.patch<ProjectResponse>(`/projects/${encodeURIComponent(projectId)}`, data)
  return response.data
}

export async function archiveProject(projectId: string): Promise<ProjectResponse> {
  const response = await http.post<ProjectResponse>(`/projects/${encodeURIComponent(projectId)}/archive`)
  return response.data
}

export async function restoreProject(projectId: string): Promise<ProjectResponse> {
  const response = await http.post<ProjectResponse>(`/projects/${encodeURIComponent(projectId)}/restore`)
  return response.data
}

export async function deleteProject(projectId: string): Promise<void> {
  await http.delete(`/projects/${encodeURIComponent(projectId)}`)
}