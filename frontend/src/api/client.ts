import type {
  ApiErrorDetails,
  ApiErrorEnvelope,
  BackgroundJob,
  BootstrapResponse,
  ClearQueueResponse,
  ClearSessionResponse,
  CompactionResult,
  ConfirmBody,
  Defaults,
  DeleteSessionResponse,
  DiscardUnusedResponse,
  HealthResponse,
  HistoryResponse,
  JsonValue,
  PortfolioDetailResponse,
  PortfolioLedgerResponse,
  PortfolioListResponse,
  ProcessListResponse,
  QueueMutationResponse,
  RemovedQueueItemResponse,
  RenameSessionBody,
  ResolveApprovalBody,
  ResolveApprovalResponse,
  SendMessageBody,
  SessionListResponse,
  SessionResponse,
  SessionStateResponse,
  StopResponse,
  UpdateDefaultsBody,
  UserInputSubmission,
} from './types'

export type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: JsonValue | object
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: ApiErrorDetails

  constructor(status: number, code: string, message: string, details: ApiErrorDetails = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

interface ParsedResponse {
  malformed: boolean
  value: unknown
}

async function parseResponse(response: Response): Promise<ParsedResponse> {
  const text = await response.text()
  if (!text.trim()) {
    return { malformed: false, value: null }
  }

  try {
    return { malformed: false, value: JSON.parse(text) as unknown }
  } catch {
    return { malformed: true, value: null }
  }
}

function isApiErrorEnvelope(value: unknown): value is ApiErrorEnvelope {
  if (typeof value !== 'object' || value === null || !('error' in value)) {
    return false
  }

  const error = value.error
  return (
    typeof error === 'object' &&
    error !== null &&
    'code' in error &&
    typeof error.code === 'string' &&
    'message' in error &&
    typeof error.message === 'string' &&
    'details' in error &&
    typeof error.details === 'object' &&
    error.details !== null
  )
}

function detailsFromUnknown(value: unknown): ApiErrorDetails {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return {}
  }

  return value as ApiErrorDetails
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, headers: initHeaders, ...init } = options
  const headers = new Headers(initHeaders)

  if (body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    ...init,
    body: body === undefined ? undefined : JSON.stringify(body),
    headers,
  })
  const parsed = await parseResponse(response)

  if (!response.ok) {
    if (isApiErrorEnvelope(parsed.value)) {
      throw new ApiError(
        response.status,
        parsed.value.error.code,
        parsed.value.error.message,
        detailsFromUnknown(parsed.value.error.details),
      )
    }

    throw new ApiError(response.status, 'http_error', `Request failed with status ${response.status}.`)
  }

  if (parsed.malformed) {
    throw new ApiError(response.status, 'invalid_response', 'The server returned malformed JSON.')
  }

  return parsed.value as T
}

type RequestOverrides = Pick<RequestOptions, 'headers' | 'signal'>

function encodePathPart(value: string): string {
  return encodeURIComponent(value)
}

export function getHealth(options: RequestOverrides = {}): Promise<HealthResponse> {
  return request<HealthResponse>('/api/health', { ...options, method: 'GET' })
}

export function getBootstrap(options: RequestOverrides = {}): Promise<BootstrapResponse> {
  return request<BootstrapResponse>('/api/bootstrap', { ...options, method: 'GET' })
}

export function getSessions(options: RequestOverrides = {}): Promise<SessionListResponse> {
  return request<SessionListResponse>('/api/sessions', { ...options, method: 'GET' })
}

export function getArchivedSessions(options: RequestOverrides = {}): Promise<SessionListResponse> {
  return request<SessionListResponse>('/api/sessions/archived', { ...options, method: 'GET' })
}

export function getPortfolios(options: RequestOverrides = {}): Promise<PortfolioListResponse> {
  return request<PortfolioListResponse>('/api/portfolios', { ...options, method: 'GET' })
}

export function getPortfolio(
  portfolioId: string,
  options: RequestOverrides = {},
): Promise<PortfolioDetailResponse> {
  return request<PortfolioDetailResponse>(`/api/portfolios/${encodePathPart(portfolioId)}`, {
    ...options,
    method: 'GET',
  })
}

export interface PortfolioLedgerOptions extends RequestOverrides {
  limit?: number
}

export function getPortfolioLedger(
  portfolioId: string,
  options: PortfolioLedgerOptions = {},
): Promise<PortfolioLedgerResponse> {
  const { limit, ...requestOptions } = options
  const suffix = limit === undefined ? '' : `?limit=${encodeURIComponent(String(limit))}`
  return request<PortfolioLedgerResponse>(`/api/portfolios/${encodePathPart(portfolioId)}/ledger${suffix}`, {
    ...requestOptions,
    method: 'GET',
  })
}

export function getSessionRelatedPortfolios(
  sessionId: string,
  options: RequestOverrides = {},
): Promise<PortfolioListResponse> {
  return request<PortfolioListResponse>(`/api/sessions/${encodePathPart(sessionId)}/related-portfolios`, {
    ...options,
    method: 'GET',
  })
}

export function createSession(options: RequestOverrides = {}): Promise<SessionResponse> {
  return request<SessionResponse>('/api/sessions', { ...options, method: 'POST' })
}

export function getSession(sessionId: string, options: RequestOverrides = {}): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/sessions/${encodePathPart(sessionId)}`, { ...options, method: 'GET' })
}

export function getSessionState(sessionId: string, options: RequestOverrides = {}): Promise<SessionStateResponse> {
  return request<SessionStateResponse>(`/api/sessions/${encodePathPart(sessionId)}/state`, { ...options, method: 'GET' })
}

export interface HistoryOptions extends RequestOverrides {
  beforeSeq?: number
  limit?: number
}

export function getSessionHistory(sessionId: string, options: HistoryOptions = {}): Promise<HistoryResponse> {
  const { beforeSeq, limit, ...requestOptions } = options
  const query = new URLSearchParams()
  if (beforeSeq !== undefined) {
    query.set('before_seq', String(beforeSeq))
  }
  if (limit !== undefined) {
    query.set('limit', String(limit))
  }
  const suffix = query.size > 0 ? `?${query.toString()}` : ''
  return request<HistoryResponse>(`/api/sessions/${encodePathPart(sessionId)}/history${suffix}`, {
    ...requestOptions,
    method: 'GET',
  })
}

export function renameSession(
  sessionId: string,
  body: RenameSessionBody,
  options: RequestOverrides = {},
): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/sessions/${encodePathPart(sessionId)}`, {
    ...options,
    body,
    method: 'PATCH',
  })
}

export function sendMessage(
  sessionId: string,
  body: SendMessageBody,
  options: RequestOverrides = {},
): Promise<UserInputSubmission> {
  return request<UserInputSubmission>(`/api/sessions/${encodePathPart(sessionId)}/messages`, {
    ...options,
    body,
    method: 'POST',
  })
}

export function stopSession(sessionId: string, options: RequestOverrides = {}): Promise<StopResponse> {
  return request<StopResponse>(`/api/sessions/${encodePathPart(sessionId)}/stop`, { ...options, method: 'POST' })
}

export function forkSession(sessionId: string, options: RequestOverrides = {}): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/sessions/${encodePathPart(sessionId)}/fork`, { ...options, method: 'POST' })
}

export function archiveSession(sessionId: string, options: RequestOverrides = {}): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/sessions/${encodePathPart(sessionId)}/archive`, { ...options, method: 'POST' })
}

export function unarchiveSession(sessionId: string, options: RequestOverrides = {}): Promise<SessionResponse> {
  return request<SessionResponse>(`/api/sessions/${encodePathPart(sessionId)}/unarchive`, { ...options, method: 'POST' })
}

export function clearSession(
  sessionId: string,
  body: ConfirmBody,
  options: RequestOverrides = {},
): Promise<ClearSessionResponse> {
  return request<ClearSessionResponse>(`/api/sessions/${encodePathPart(sessionId)}/clear`, {
    ...options,
    body,
    method: 'POST',
  })
}

export function deleteSession(
  sessionId: string,
  body: ConfirmBody,
  options: RequestOverrides = {},
): Promise<DeleteSessionResponse> {
  return request<DeleteSessionResponse>(`/api/sessions/${encodePathPart(sessionId)}`, {
    ...options,
    body,
    method: 'DELETE',
  })
}

export function compactSession(sessionId: string, options: RequestOverrides = {}): Promise<CompactionResult> {
  return request<CompactionResult>(`/api/sessions/${encodePathPart(sessionId)}/compact`, { ...options, method: 'POST' })
}

export function discardUnusedSession(
  sessionId: string,
  options: RequestOverrides = {},
): Promise<DiscardUnusedResponse> {
  return request<DiscardUnusedResponse>(`/api/sessions/${encodePathPart(sessionId)}/discard-unused`, {
    ...options,
    method: 'POST',
  })
}

export function removeQueuedInput(
  sessionId: string,
  queueId: string,
  options: RequestOverrides = {},
): Promise<RemovedQueueItemResponse> {
  return request<RemovedQueueItemResponse>(
    `/api/sessions/${encodePathPart(sessionId)}/queue/${encodePathPart(queueId)}`,
    { ...options, method: 'DELETE' },
  )
}

export function clearSessionQueue(
  sessionId: string,
  body: ConfirmBody,
  options: RequestOverrides = {},
): Promise<ClearQueueResponse> {
  return request<ClearQueueResponse>(`/api/sessions/${encodePathPart(sessionId)}/queue`, {
    ...options,
    body,
    method: 'DELETE',
  })
}

export function resumeSessionQueue(sessionId: string, options: RequestOverrides = {}): Promise<QueueMutationResponse> {
  return request<QueueMutationResponse>(`/api/sessions/${encodePathPart(sessionId)}/queue/resume`, {
    ...options,
    method: 'POST',
  })
}

export function resolveApproval(
  approvalId: string,
  body: ResolveApprovalBody,
  options: RequestOverrides = {},
): Promise<ResolveApprovalResponse> {
  return request<ResolveApprovalResponse>(`/api/approvals/${encodePathPart(approvalId)}`, {
    ...options,
    body,
    method: 'POST',
  })
}

export function getDefaults(options: RequestOverrides = {}): Promise<Defaults> {
  return request<Defaults>('/api/defaults', { ...options, method: 'GET' })
}

export function updateDefaults(body: UpdateDefaultsBody, options: RequestOverrides = {}): Promise<Defaults> {
  return request<Defaults>('/api/defaults', { ...options, body, method: 'PATCH' })
}

export function getProcesses(options: RequestOverrides = {}): Promise<ProcessListResponse> {
  return request<ProcessListResponse>('/api/processes', { ...options, method: 'GET' })
}

export const api = {
  archiveSession,
  clearSession,
  clearSessionQueue,
  compactSession,
  createSession,
  deleteSession,
  discardUnusedSession,
  forkSession,
  getArchivedSessions,
  getBootstrap,
  getDefaults,
  getHealth,
  getProcesses,
  getPortfolio,
  getPortfolioLedger,
  getPortfolios,
  getSession,
  getSessionHistory,
  getSessionRelatedPortfolios,
  getSessionState,
  getSessions,
  removeQueuedInput,
  renameSession,
  request,
  resolveApproval,
  resumeSessionQueue,
  sendMessage,
  stopSession,
  unarchiveSession,
  updateDefaults,
}

export type { BackgroundJob }

export default api
