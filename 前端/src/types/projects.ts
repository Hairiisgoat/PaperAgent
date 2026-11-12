/** Matches the backend ProjectResponse and ProjectPage OpenAPI schemas. */
export type PaperType = 'course_paper' | 'thesis' | 'literature_review' | 'research_paper'
export type LifecycleStatus = 'active' | 'archived' | 'deleted'
export type ProgressStatus = 'not_started' | 'in_progress' | 'completed'
export type ProjectFilter = 'active' | 'archived' | undefined

export interface ProjectResponse {
  id: string
  user_id: string | null
  title: string
  paper_type: PaperType
  topic: string
  requirements: Record<string, unknown> | null
  citation_style: string
  lifecycle_status: LifecycleStatus
  progress_status: ProgressStatus
  created_at: string
  updated_at: string
  archived_at: string | null
  deleted_at: string | null
}

export interface ProjectPage {
  items: ProjectResponse[]
  total: number
  page: number
  page_size: number
}

export interface ProjectListQuery {
  page: number
  page_size: number
  lifecycle_status?: Exclude<ProjectFilter, undefined>
}

/** ProjectCreate fields used by the course-paper creation screen. */
export interface ProjectCreate {
  title: string
  topic: string
  paper_type?: PaperType
  citation_style: string
  requirements?: Record<string, unknown> | null
}

/** This screen deliberately edits only the three permitted fields. */
export type ProjectEdit = Partial<Pick<ProjectResponse, 'title' | 'topic' | 'citation_style'>>
