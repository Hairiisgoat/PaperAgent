import { http, HttpError } from './http'
import type { ApprovalDecision, ApprovalDetail, ApprovalResult, ChapterVersion, RevisionRequest, RevisionResult, RunDetail, StartRun, WritingDraft } from '../types/workflows'

export async function downloadExport(project: string, run: string, kind: 'docx' | 'pdf' | 'review-docx'): Promise<void> {
  const response = await http.get<Blob>(`/projects/${encodeURIComponent(project)}/workflow-runs/${encodeURIComponent(run)}/export/${kind}`, { responseType: 'blob', timeout: 60000 })
  const url = URL.createObjectURL(response.data)
  try {
    const link = document.createElement('a')
    link.href = url
    link.download = kind === 'review-docx' ? 'PaperAgent-审查报告.docx' : `PaperAgent-论文.${kind}`
    document.body.appendChild(link)
    link.click()
    link.remove()
  } finally { setTimeout(() => URL.revokeObjectURL(url), 1000) }
}

export async function startWorkflow(projectId: string, realPlanner = false): Promise<StartRun> {
  const response = await http.post<StartRun>(`/projects/${encodeURIComponent(projectId)}/${realPlanner ? 'planner-runs' : 'runs'}`)
  if (response.status !== 202) throw new HttpError('unknown', '启动结果无法确认，请先检查运行记录。')
  return response.data
}
export async function getRun(projectId: string, runId: string, signal?: AbortSignal): Promise<RunDetail> {
  const response = await http.get<RunDetail>(`/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}`, { signal })
  return response.data
}
export async function getDraft(projectId: string, runId: string, signal?: AbortSignal): Promise<WritingDraft> {
  const response = await http.get<WritingDraft>(`/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/draft`, { signal })
  return response.data
}
export async function retryWriting(projectId: string, runId: string): Promise<RunDetail> {
  const response = await http.post<RunDetail>(`/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/segmented-retry`)
  if (response.status !== 202) throw new HttpError('unknown', '重试结果无法确认，请先检查运行记录。')
  return response.data
}
const chapterPath = (project: string, run: string, chapter: string) => `/projects/${encodeURIComponent(project)}/workflow-runs/${encodeURIComponent(run)}/draft/chapters/${encodeURIComponent(chapter)}`
export async function getChapterVersions(project: string, run: string, chapter: string): Promise<ChapterVersion[]> {
  return (await http.get<ChapterVersion[]>(`${chapterPath(project, run, chapter)}/versions`)).data
}
export async function startChapterRevision(project: string, run: string, chapter: string, request: RevisionRequest): Promise<RevisionResult> {
  return (await http.post<RevisionResult>(`${chapterPath(project, run, chapter)}/revisions`, request)).data
}
export async function getChapterRevision(project: string, run: string, chapter: string, revision: string): Promise<RevisionResult> {
  return (await http.get<RevisionResult>(`${chapterPath(project, run, chapter)}/revisions/${encodeURIComponent(revision)}`)).data
}
export async function restoreChapterVersion(project: string, run: string, chapter: string, version: string, requestId: string): Promise<ChapterVersion> {
  return (await http.post<ChapterVersion>(`${chapterPath(project, run, chapter)}/versions/${encodeURIComponent(version)}/restore`, { request_id: requestId })).data
}
export function eventsUrl(projectId: string, runId: string): string {
  return `/api/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/events`
}

export async function getApproval(projectId: string, runId: string, signal?: AbortSignal): Promise<ApprovalDetail> {
  const response = await http.get<ApprovalDetail>(`/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/approval`, { signal })
  return response.data
}
export async function submitApproval(projectId: string, runId: string, decision: ApprovalDecision): Promise<ApprovalResult> {
  const response = await http.post<ApprovalResult>(`/projects/${encodeURIComponent(projectId)}/workflow-runs/${encodeURIComponent(runId)}/approval`, decision)
  if (response.status !== 202) throw new HttpError('unknown', '审核结果无法确认，请核对运行状态。')
  return response.data
}
