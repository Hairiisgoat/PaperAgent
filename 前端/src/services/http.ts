import axios, { AxiosError } from 'axios'

export type HttpErrorKind = 'cancelled' | 'timeout' | 'network' | 'http' | 'unknown'
export type ProjectValidationField = 'title' | 'topic' | 'paper_type' | 'citation_style'
export type SafeFieldErrors = Partial<Record<ProjectValidationField, string>>

/** Safe, presentation-ready error. Does not retain the request, response or driver error. */
export class HttpError extends Error {
  readonly kind: HttpErrorKind
  readonly status?: number
  readonly fieldErrors: SafeFieldErrors
  readonly code?: 'writing_storage_missing'

  constructor(kind: HttpErrorKind, message: string, status?: number, fieldErrors: SafeFieldErrors = {}, code?: 'writing_storage_missing') {
    super(message)
    this.name = 'HttpError'
    this.kind = kind
    this.status = status
    this.fieldErrors = fieldErrors
    this.code = code
  }
}

// Only known field names and fixed messages cross the transport boundary.
function safeProjectFieldErrors(data: unknown): SafeFieldErrors {
  const result: SafeFieldErrors = {}
  if (typeof data !== 'object' || data === null || !('detail' in data) || !Array.isArray(data.detail)) return result
  const fields: ReadonlySet<string> = new Set(['title', 'topic', 'paper_type', 'citation_style'])
  const messages: Readonly<Record<string, string>> = {
    missing: '请填写此项', string_too_short: '请填写此项', string_too_long: '输入内容过长',
    string_type: '请输入文本', literal_error: '请选择有效的论文类型', value_error: '请检查此项内容',
  }
  for (const item of data.detail) {
    if (typeof item !== 'object' || item === null || !('loc' in item) || !Array.isArray(item.loc) || !('type' in item) || typeof item.type !== 'string') continue
    if (item.loc.length !== 2 || item.loc[0] !== 'body' || typeof item.loc[1] !== 'string') continue
    const field = item.loc[1]
    if (!fields.has(field) || !Object.hasOwn(messages, item.type)) continue
    result[field as ProjectValidationField] = messages[item.type]
  }
  return result
}
export function normalizeHttpError(error: unknown): HttpError {
  if (axios.isCancel(error)) return new HttpError('cancelled', '请求已取消')
  if (axios.isAxiosError(error)) {
    if (error.code === AxiosError.ECONNABORTED || error.code === AxiosError.ETIMEDOUT) {
      return new HttpError('timeout', '请求超时，请先确认操作结果')
    }
    const status = error.response?.status
    if (status !== undefined) {
      const data: unknown = error.response?.data
      if (status === 503 && typeof data === 'object' && data !== null && 'code' in data && data.code === 'writing_storage_missing') {
        return new HttpError('http', '写作数据库尚未准备好，请先完成部署。', status, {}, 'writing_storage_missing')
      }
      const messages: Record<number, string> = {
        400: '请求无效，请检查输入',
        401: '身份验证失败',
        403: '没有权限执行此操作',
        404: '请求的内容不存在或已删除',
        409: '当前状态已变化，请刷新后重试',
        422: '输入信息不符合要求，请检查后重试',
        500: '服务发生错误，请先确认操作结果',
        503: '服务暂不可用，请先确认操作结果',
      }
      return new HttpError('http', messages[status] ?? '请求未成功，请先确认操作结果', status, status === 422 ? safeProjectFieldErrors(error.response?.data) : {})
    }
    return new HttpError('network', '无法连接服务，请检查网络或后端服务')
  }
  return new HttpError('unknown', '请求未成功，请先确认操作结果')
}

export const http = axios.create({ baseURL: '/api', timeout: 15000 })

// Callers can pass an AbortSignal using Axios's `signal` option. No automatic retries.
http.interceptors.response.use(
  response => response,
  (error: unknown) => Promise.reject(normalizeHttpError(error)),
)
