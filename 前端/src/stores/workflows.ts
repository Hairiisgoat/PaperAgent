import { defineStore } from 'pinia'
import { ref } from 'vue'
import { HttpError } from '../services/http'
import { getApproval, getRun, startWorkflow, submitApproval } from '../services/workflows'
import { monitorStream, parseWorkflowFrame } from '../services/workflowStream'
import type { ApprovalDecision, ApprovalDetail, ApprovalResult, ConnectionState, RunDetail, StartRun, WorkflowEvent } from '../types/workflows'

const finished = new Set(['waiting_human', 'paused', 'failed', 'completed', 'cancelled'])
const key = (project: string, run: string) => `paperagent.events.${project}.${run}`
const latestKey = (project: string) => `paperagent.latest-run.${project}`
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function lastKnownRun(projectId: string): string | null {
  try { const value = sessionStorage.getItem(latestKey(projectId)); return value && uuid.test(value) ? value : null } catch { return null }
}

export const useWorkflowStore = defineStore('workflow', () => {
  const starting = ref(false)
  const startError = ref<HttpError | null>(null)
  const detail = ref<RunDetail | null>(null)
  const loading = ref(false)
  const error = ref<HttpError | null>(null)
  const connection = ref<ConnectionState>('idle')
  const connectionMessage = ref('')
  const timeline = ref<WorkflowEvent[]>([])
  const lastSequence = ref(0)
  const lastHeartbeat = ref<string | null>(null)
  const summary = ref<RunDetail | null>(null)
  const summaryRunId = ref<string | null>(null)
  const summaryError = ref<HttpError | null>(null)
  let summaryController: AbortController | undefined
  let summaryVersion = 0
  let monitor: AbortController | undefined
  let fetchController: AbortController | undefined
  let generation = 0
  let requestVersion = 0
  let activeProject = ''
  let activeRun = ''

  function cancelSummary(): void { summaryVersion++; summaryController?.abort(); summaryController = undefined }
  async function loadLatest(projectId: string): Promise<void> {
    cancelSummary()
    const current = summaryVersion
    summary.value = null; summaryError.value = null
    summaryRunId.value = lastKnownRun(projectId)
    if (!summaryRunId.value) return
    const runId = summaryRunId.value
    const controller = new AbortController()
    summaryController = controller
    try {
      const value = await getRun(projectId, runId, controller.signal)
      if (current === summaryVersion && value.project_id === projectId && value.run_id === runId) summary.value = value
    } catch (cause: unknown) {
      if (current === summaryVersion && !(cause instanceof HttpError && cause.kind === 'cancelled')) summaryError.value = cause instanceof HttpError ? cause : new HttpError('unknown', '运行数据不可用')
    }
  }

  async function start(projectId: string, realPlanner = false): Promise<StartRun | null> {
    if (starting.value) return null
    starting.value = true; startError.value = null
    try {
      const result = await startWorkflow(projectId, realPlanner)
      if (result.project_id !== projectId || !uuid.test(result.run_id) || !uuid.test(result.thread_id) || result.status !== 'queued') {
        throw new HttpError('unknown', '启动结果无法确认，请先检查运行记录。')
      }
      try { sessionStorage.setItem(latestKey(projectId), result.run_id) } catch { /* storage can be disabled */ }
      return result
    } catch (cause: unknown) {
      startError.value = cause instanceof HttpError ? cause : new HttpError('unknown', '启动结果无法确认，请先检查运行记录。')
      return null
    } finally { starting.value = false }
  }

  async function readApproval(projectId: string, runId: string, signal: AbortSignal): Promise<ApprovalDetail> {
    return getApproval(projectId, runId, signal)
  }
  async function sendApproval(projectId: string, runId: string, threadId: string, decision: ApprovalDecision): Promise<ApprovalResult> {
    const result = await submitApproval(projectId, runId, decision)
    if (result.project_id !== projectId || result.previous_run_id !== runId || !uuid.test(result.run_id) || !uuid.test(result.thread_id) || result.thread_id !== threadId || result.run_id === runId || result.status !== 'queued' || !Number.isInteger(result.attempt_no) || result.attempt_no < 2)
      throw new HttpError('unknown', '审核结果无法确认，请先核对运行状态。')
    try { sessionStorage.setItem(latestKey(projectId), result.run_id) } catch { /* storage unavailable */ }
    return result
  }

  function stop(): void {
    generation++; requestVersion++
    monitor?.abort(); monitor = undefined
    fetchController?.abort(); fetchController = undefined
    loading.value = false
    connection.value = 'closed'
  }

  function restore(projectId: string, runId: string): void {
    timeline.value = []; lastSequence.value = 0
    try {
      const raw = sessionStorage.getItem(key(projectId, runId))
      if (!raw) return
      const stored: unknown = JSON.parse(raw)
      if (!Array.isArray(stored) || stored.length > 100) return
      const items: WorkflowEvent[] = []
      let previous = 0
      for (const entry of stored) {
        if (typeof entry !== 'object' || entry === null || !('event_type' in entry) || !('sequence' in entry) || typeof entry.event_type !== 'string' || typeof entry.sequence !== 'number') throw new Error('invalid cache')
        const event = parseWorkflowFrame({ event: entry.event_type, id: String(entry.sequence), data: JSON.stringify(entry) }, projectId, runId)
        if (event.sequence === null || event.sequence <= previous) throw new Error('invalid cache')
        previous = event.sequence
        items.push(event)
      }
      timeline.value = items; lastSequence.value = previous
    } catch { try { sessionStorage.removeItem(key(projectId, runId)) } catch { /* unavailable */ } }
  }
  function save(): void {
    try { sessionStorage.setItem(key(activeProject, activeRun), JSON.stringify(timeline.value.filter(e => e.sequence !== null).slice(-100))) } catch { /* cursor will replay from zero after refresh */ }
  }
  function replay(): void {
    try { sessionStorage.removeItem(key(activeProject, activeRun)) } catch { /* storage unavailable */ }
    void open(activeProject, activeRun)
  }
  async function refresh(expected: number): Promise<boolean> {
    fetchController?.abort()
    const controller = new AbortController()
    fetchController = controller
    const current = ++requestVersion
    try {
      const value = await getRun(activeProject, activeRun, controller.signal)
      if (generation !== expected || requestVersion !== current) return false
      if (value.project_id !== activeProject || value.run_id !== activeRun) throw new HttpError('unknown', '运行数据不可用')
      detail.value = value; error.value = null
      return true
    } catch (cause: unknown) {
      if (generation !== expected || requestVersion !== current || (cause instanceof HttpError && cause.kind === 'cancelled')) return false
      error.value = cause instanceof HttpError ? cause : new HttpError('unknown', '运行数据不可用')
      return false
    } finally {
      if (generation === expected && requestVersion === current) { loading.value = false; fetchController = undefined }
    }
  }

  async function open(projectId: string, runId: string): Promise<void> {
    stop()
    activeProject = projectId; activeRun = runId
    const expected = generation
    detail.value = null; error.value = null; loading.value = true
    connection.value = 'idle'; connectionMessage.value = ''; lastHeartbeat.value = null
    restore(projectId, runId)
    if (!await refresh(expected) || generation !== expected) return
    const controller = new AbortController()
    monitor = controller
    void monitorStream({
      projectId, runId, signal: controller.signal, cursor: () => lastSequence.value,
      onState: (state, message) => { if (generation === expected) { connection.value = state; connectionMessage.value = message || '' } },
      onEvent: event => {
        if (generation !== expected) return
        if (event.event_type === 'heartbeat') {
          lastHeartbeat.value = event.timestamp
          if (detail.value && finished.has(detail.value.status)) { controller.abort(); connection.value = 'closed' }
          return
        }
        if (event.sequence !== null) {
          if (event.sequence <= lastSequence.value) return
          if (event.sequence !== lastSequence.value + 1) throw new Error('sequence gap')
          lastSequence.value = event.sequence
        }
        timeline.value = [...timeline.value, event].slice(-200)
        if (event.sequence !== null) save()
        if (event.sequence !== null) {
          const terminalEvent = ['approval_required', 'completed', 'error'].includes(event.event_type) || (event.event_type === 'progress' && event.status === 'cancelled')
          void refresh(expected).then(ok => {
            if (ok && terminalEvent && detail.value && finished.has(detail.value.status) && generation === expected) {
              controller.abort(); connection.value = 'closed'
            }
          })
        }
      },
    })
  }
  return { readApproval, sendApproval, starting, startError, start, summary, summaryRunId, summaryError, loadLatest, cancelSummary, detail, loading, error, connection, connectionMessage, timeline, lastSequence, lastHeartbeat, open, stop, replay }
})
