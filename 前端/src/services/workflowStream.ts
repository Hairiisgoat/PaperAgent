import type { ConnectionState, WorkflowEvent } from '../types/workflows'
import { SSEParser, type SSEFrame } from './sseParser'
import { eventsUrl } from './workflows'

const durable = new Set(['progress', 'approval_required', 'completed', 'error'])
const statuses = new Set(['queued', 'running', 'waiting_human', 'paused', 'failed', 'completed', 'cancelled'])
const stages = new Set(['planning', 'research', 'outline', 'writing', 'review', 'export'])
const agents = new Set(['Planner', 'Research', 'Writing', 'Review'])
const errorCodes = new Set(['timeout', 'provider_unavailable', 'invalid_output', 'database_unavailable', 'budget_exceeded', 'internal_error', 'other'])
const progressMessages = new Set(['Workflow queued', 'Workflow running', 'Planner completed', 'Waiting for approval', 'Workflow completed', 'Approval rejected', 'Planner approval complete; workflow paused', 'Source approval complete; workflow paused'])
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const obj = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)
const isUuid = (v: unknown): v is string => typeof v === 'string' && uuid.test(v)
const isTime = (v: unknown): v is string => typeof v === 'string' && !Number.isNaN(Date.parse(v))
const isStage = (v: unknown): boolean => v === null || (typeof v === 'string' && stages.has(v))
const isAgent = (v: unknown): boolean => v === null || (typeof v === 'string' && agents.has(v))

/** Reject malformed, unscoped or unrecognised wire events before using their data. */
export function parseWorkflowFrame(frame: SSEFrame, projectId: string, runId: string): WorkflowEvent {
  let value: unknown
  try { value = JSON.parse(frame.data) } catch { throw new Error('invalid event') }
  if (!obj(value) || !obj(value.payload) || value.event_type !== frame.event || !isUuid(value.event_id) ||
      value.project_id !== projectId || value.run_id !== runId || !isStage(value.stage) || !isAgent(value.agent_name) ||
      typeof value.status !== 'string' || !statuses.has(value.status) || !isTime(value.timestamp)) throw new Error('invalid event')
  const kind = value.event_type
  if (typeof kind !== 'string') throw new Error('invalid event')
  if (durable.has(kind)) {
    if (!Number.isSafeInteger(value.sequence) || (value.sequence as number) < 1 || frame.id !== String(value.sequence)) throw new Error('invalid event')
  } else if (!['heartbeat', 'text_delta'].includes(kind) || value.sequence !== null || frame.id !== undefined) throw new Error('invalid event')
  const p = value.payload
  if (kind === 'progress' && !(p.stage === value.stage && p.agent_name === value.agent_name && p.status === value.status && typeof p.message === 'string' && progressMessages.has(p.message))) throw new Error('invalid event')
  if (kind === 'approval_required' && !(value.status === 'waiting_human' && isUuid(p.checkpoint_id) && isUuid(p.payload_ref) && (p.approval_type === 'task_approval' || p.approval_type === 'source_approval'))) throw new Error('invalid event')
  if (kind === 'text_delta' && !(isUuid(p.chapter_id) && typeof p.delta === 'string' && p.delta.length <= 4096 && p.delta.length > 0 && Number.isSafeInteger(p.delta_sequence) && (p.delta_sequence as number) > 0)) throw new Error('invalid event')
  if (kind === 'completed' && !(value.status === 'completed' && isUuid(p.result_ref) && isTime(p.finished_at))) throw new Error('invalid event')
  if (kind === 'error' && !(value.status === 'failed' && typeof p.error_code === 'string' && errorCodes.has(p.error_code) && (p.safe_message === 'Workflow execution failed' || p.safe_message === 'Workflow persistence failed') && typeof p.retryable === 'boolean')) throw new Error('invalid event')
  if (kind === 'heartbeat' && !isTime(p.timestamp)) throw new Error('invalid event')
  return value as unknown as WorkflowEvent
}

class StreamError extends Error { constructor(readonly status?: number) { super('stream failed') } }
type StreamOptions = {
  projectId: string; runId: string; signal: AbortSignal; cursor: () => number
  onEvent: (event: WorkflowEvent) => void; onState: (state: ConnectionState, message?: string) => void
}
const wait = (ms: number, signal: AbortSignal): Promise<void> => new Promise(resolve => {
  if (signal.aborted) { resolve(); return }
  const finish = () => { clearTimeout(timer); signal.removeEventListener('abort', finish); resolve() }
  const timer = setTimeout(finish, ms)
  signal.addEventListener('abort', finish, { once: true })
})

export async function monitorStream(options: StreamOptions): Promise<void> {
  let failures = 0
  while (!options.signal.aborted && failures < 5) {
    options.onState(failures ? 'reconnecting' : 'connecting')
    const controller = new AbortController()
    const abort = () => controller.abort()
    options.signal.addEventListener('abort', abort, { once: true })
    let timer: ReturnType<typeof setTimeout> | undefined
    const resetTimeout = () => { clearTimeout(timer); timer = setTimeout(() => controller.abort(), 15000) }
    const started = Date.now()
    try {
      resetTimeout()
      const headers: Record<string, string> = { Accept: 'text/event-stream' }
      if (options.cursor() > 0) headers['Last-Event-ID'] = String(options.cursor())
      const response = await fetch(eventsUrl(options.projectId, options.runId), { headers, signal: controller.signal, cache: 'no-store' })
      if (!response.ok) throw new StreamError(response.status)
      if (!response.body || !response.headers.get('content-type')?.toLowerCase().startsWith('text/event-stream')) throw new StreamError()
      options.onState('connected')
      const parser = new SSEParser(frame => options.onEvent(parseWorkflowFrame(frame, options.projectId, options.runId)))
      const reader = response.body.getReader()
      try {
        while (!options.signal.aborted) {
          const { value, done } = await reader.read()
          if (done) { parser.finish(); break }
          resetTimeout()
          parser.push(value)
        }
      } finally { reader.releaseLock() }
      if (options.signal.aborted) return
      throw new StreamError()
    } catch (error: unknown) {
      if (options.signal.aborted) return
      if (error instanceof StreamError && error.status !== undefined && error.status >= 400 && error.status < 500) {
        options.onState('error', error.status === 422 ? '事件游标无效，可以从头加载事件。' : '订阅被拒绝，请检查项目和运行是否存在。')
        return
      }
      if (!(error instanceof StreamError) && !(error instanceof TypeError) && !(error instanceof DOMException)) {
        options.onState('error', '事件格式异常，已停止订阅。')
        return
      }
      if (Date.now() - started >= 10000) failures = 0
      failures++
      if (failures >= 5) break
      options.onState('reconnecting', '连接中断，正在重新连接。')
      await wait(Math.min(500 * 2 ** (failures - 1), 8000), options.signal)
    } finally {
      clearTimeout(timer)
      options.signal.removeEventListener('abort', abort)
      controller.abort()
    }
  }
  if (!options.signal.aborted) options.onState('error', '连接多次中断，请手动重试。')
}
