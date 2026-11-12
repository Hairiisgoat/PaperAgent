export type RunStatus = 'queued' | 'running' | 'waiting_human' | 'paused' | 'failed' | 'completed' | 'cancelled'
export type WorkflowStage = 'planning' | 'research' | 'outline' | 'writing' | 'review' | 'export'
export type AgentName = 'Planner' | 'Research' | 'Writing' | 'Review'
export type ErrorCode = 'timeout' | 'provider_unavailable' | 'invalid_output' | 'database_unavailable' | 'budget_exceeded' | 'internal_error' | 'other'
export interface StartRun { project_id: string; thread_id: string; run_id: string; status: 'queued' }
export interface RunDetail {
  run_id: string; thread_id: string; project_id: string; attempt_no: number; status: RunStatus
  current_stage: WorkflowStage | null; started_at: string | null; finished_at: string | null
  created_at: string; updated_at: string; error_code: ErrorCode | null; retryable: boolean | null
}
export interface EventBase {
  event_id: string; sequence: number | null; run_id: string; project_id: string
  stage: WorkflowStage | null; agent_name: AgentName | null; status: RunStatus; timestamp: string
}
export type WorkflowEvent =
  | (EventBase & { event_type: 'progress'; payload: { stage: WorkflowStage; agent_name: AgentName | null; status: RunStatus; message: string } })
  | (EventBase & { event_type: 'approval_required'; payload: { checkpoint_id: string; approval_type: 'task_approval' | 'source_approval'; payload_ref: string } })
  | (EventBase & { event_type: 'text_delta'; payload: { chapter_id: string; delta: string; delta_sequence: number } })
  | (EventBase & { event_type: 'completed'; payload: { result_ref: string; finished_at: string } })
  | (EventBase & { event_type: 'error'; payload: { error_code: ErrorCode; safe_message: string; retryable: boolean } })
  | (EventBase & { event_type: 'heartbeat'; payload: { timestamp: string } })
export type ConnectionState = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'closed' | 'error'

export interface ApprovalTask { task_id: string; title: string; description: string; expected_output: string }
export interface ApprovalSource { source_id: string; title: string; authors: string[]; year?: number; journal?: string; doi: string; url: string; abstract?: string; task_ids: string[]; tasks: { task_id: string; title: string }[]; relevance_hint?: 'more' | 'some' | 'unknown'; same_title_count?: number }
export interface ApprovalDetail { project_id: string; run_id: string; thread_id: string; approval_type: 'task_approval' | 'source_approval'; checkpoint_id: string; current_stage: WorkflowStage; item_ids: string[]; items?: ApprovalTask[]; sources?: ApprovalSource[] }
export type ApprovalDecision = { decision: 'approve'; approved_ids: string[]; continue_research?: boolean; continue_writing?: boolean; segmented_writing?: boolean } | { decision: 'reject' }
export interface DraftCitation { source_id: string; title: string; authors: string[]; year: number | null; doi: string; url: string; evidence: string }
export interface DraftParagraph { text: string; citations: DraftCitation[] }
export interface DraftChapter { chapter_id: string; position: number; title: string; objective: string; status: 'queued' | 'writing' | 'completed' | 'failed' | 'needs_review'; paragraphs: DraftParagraph[] }
export interface WritingDraft { project_id: string; run_id: string; draft_id: string; status: 'queued' | 'writing' | 'completed' | 'failed' | 'needs_review'; chapters: DraftChapter[]; supports_versions?: boolean }
export interface ChapterVersion { version_id: string; chapter_id: string; version_no: number; kind: 'generated' | 'revised' | 'restored'; parent_version_id: string | null; created_at: string; paragraphs: DraftParagraph[] }
export type RevisionMode = 'expand' | 'condense' | 'rewrite' | 'restructure'
export interface RevisionRequest { revision_id: string; base_version_id: string; mode: RevisionMode; instruction: string }
export interface RevisionResult { revision_id: string; chapter_id: string; base_version_id: string; result_version_id: string | null; mode: RevisionMode; status: 'queued' | 'running' | 'completed' | 'failed' | 'needs_review'; error_code: string | null; created_at: string }
export interface ApprovalResult { project_id: string; previous_run_id: string; run_id: string; thread_id: string; attempt_no: number; status: 'queued' }
