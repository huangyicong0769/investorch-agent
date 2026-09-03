import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type PropsWithChildren,
} from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useMatch } from 'react-router-dom'

import {
  getBootstrap,
  getSessionHistory,
  getSessionState,
  getSessions,
} from '../api/client'
import {
  queryKeys,
} from '../api/queries'
import { useWebConfig } from '../config/WebConfigContext'
import type {
  ApprovalRequest,
  ApprovalRequiredLiveEvent,
  BootstrapResponse,
  FollowUpEventKind,
  JournalRecord,
  LiveEvent,
  OutputEvent,
  RuntimeStateLiveEvent,
  SessionStateResponse,
  TodoItem,
} from '../api/types'
import {
  historyNewestSeq,
  mergeHistoryPages,
  type HistoryInfiniteData,
} from '../lib/timeline/history'
import { isCompleteRunEndedRecord } from '../lib/timeline/project'
import {
  createWebSocketConnection,
  type WebSocketConnectionStatus,
} from './connection'

export type { WebSocketConnectionStatus }

export interface LiveNotice {
  id: string
  text: string
  timestamp: string
}

interface LiveSessionValue {
  records: JournalRecord[]
  notices: LiveNotice[]
  resyncedRunId: string | null
}

interface LiveWebSocketContextValue {
  selectedSessionId: string | null
  status: WebSocketConnectionStatus
  records: JournalRecord[]
  notices: LiveNotice[]
  resyncedRunId: string | null
}

const EMPTY_RECORDS: JournalRecord[] = []
const EMPTY_NOTICES: LiveNotice[] = []
const LiveWebSocketContext = createContext<LiveWebSocketContextValue>({
  selectedSessionId: null,
  status: 'disconnected',
  records: EMPTY_RECORDS,
  notices: EMPTY_NOTICES,
  resyncedRunId: null,
})

type ParsedRunEndedEvent = {
  kind: 'run_ended'
  session_id: string
  run_id: string
  status: 'completed' | 'cancelled' | 'failed'
  started_at: string | null
  ended_at: string | null
  duration_ms: number | null
  auto_compaction_changed: boolean | null
  auto_compaction_failed: boolean | null
  auto_compaction_consistency_uncertain: boolean | null
}

type ParsedLiveEvent = Exclude<LiveEvent, { kind: 'run_ended' }> | ParsedRunEndedEvent

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function stringField(value: Record<string, unknown>, key: string): string | null {
  return typeof value[key] === 'string' ? value[key] : null
}

function nullableStringField(value: Record<string, unknown>, key: string): string | null | undefined {
  if (!(key in value)) {
    return undefined
  }
  return value[key] === null || typeof value[key] === 'string' ? value[key] : undefined
}

function positiveIntegerField(value: Record<string, unknown>, key: string): number | null {
  const candidate = value[key]
  return typeof candidate === 'number' && Number.isInteger(candidate) && candidate > 0 ? candidate : null
}

function nullablePositiveIntegerField(value: Record<string, unknown>, key: string): number | null | undefined {
  if (!(key in value)) {
    return undefined
  }
  if (value[key] === null) {
    return null
  }
  return positiveIntegerField(value, key)
}

const ISO_TIMESTAMP_PATTERN = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/

function isoTimestampField(value: Record<string, unknown>, key: string): string | null | undefined {
  if (!(key in value)) {
    return undefined
  }
  const candidate = value[key]
  return typeof candidate === 'string' && ISO_TIMESTAMP_PATTERN.test(candidate) && Number.isFinite(Date.parse(candidate))
    ? candidate
    : null
}

function nonNegativeNumberField(value: Record<string, unknown>, key: string): number | null | undefined {
  if (!(key in value)) {
    return undefined
  }
  const candidate = value[key]
  return typeof candidate === 'number' && Number.isFinite(candidate) && candidate >= 0 ? candidate : null
}

function outputEvent(value: unknown): OutputEvent | null {
  if (!isRecord(value) || typeof value.type !== 'string') {
    return null
  }

  switch (value.type) {
    case 'agent_changed': {
      const name = stringField(value, 'name')
      return name === null ? null : { type: 'agent_changed', name }
    }
    case 'reasoning': {
      const text = stringField(value, 'text')
      return text === null ? null : { type: 'reasoning', text }
    }
    case 'tool_called': {
      const name = stringField(value, 'name')
      const argumentsValue = nullableStringField(value, 'arguments')
      return name === null || argumentsValue === undefined
        ? null
        : { type: 'tool_called', name, arguments: argumentsValue }
    }
    case 'tool_output': {
      const output = stringField(value, 'output')
      return output === null ? null : { type: 'tool_output', output }
    }
    case 'assistant_message': {
      const text = stringField(value, 'text')
      return text === null ? null : { type: 'assistant_message', text }
    }
    default:
      return null
  }
}

function todoItems(value: unknown): TodoItem[] | null {
  if (!Array.isArray(value)) {
    return null
  }

  const result: TodoItem[] = []
  for (const item of value) {
    if (!isRecord(item) || typeof item.content !== 'string') {
      return null
    }
    if (item.status !== 'pending' && item.status !== 'in_progress' && item.status !== 'completed' && item.status !== 'failed') {
      return null
    }
    result.push({ content: item.content, status: item.status })
  }
  return result
}

function stringItems(value: unknown): string[] | null {
  return Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : null
}

function runtimeSnapshot(value: Record<string, unknown>): RuntimeStateLiveEvent | null {
  const sessionId = stringField(value, 'session_id')
  const runId = nullableStringField(value, 'run_id')
  const runStartedAt = nullableStringField(value, 'run_started_at')
  const runPhase = value.run_phase
  const activeFollowUpBehavior = value.active_follow_up_behavior
  const queuedCount = value.queued_count
  const queuePaused = value.queue_paused
  const pendingSteerCount = value.pending_steer_count
  const todos = todoItems(value.todos)

  if (
    sessionId === null ||
    runId === undefined ||
    runStartedAt === undefined ||
    (runPhase !== null && runPhase !== 'running' && runPhase !== 'waiting_approval' && runPhase !== 'stopping') ||
    (activeFollowUpBehavior !== null && activeFollowUpBehavior !== 'steer' && activeFollowUpBehavior !== 'queue') ||
    typeof queuedCount !== 'number' ||
    !Number.isInteger(queuedCount) ||
    queuedCount < 0 ||
    typeof queuePaused !== 'boolean' ||
    typeof pendingSteerCount !== 'number' ||
    !Number.isInteger(pendingSteerCount) ||
    pendingSteerCount < 0 ||
    todos === null
  ) {
    return null
  }

  return {
    kind: 'runtime_state',
    session_id: sessionId,
    run_id: runId,
    run_started_at: runStartedAt,
    run_phase: runPhase,
    active_follow_up_behavior: activeFollowUpBehavior,
    queued_count: queuedCount,
    queue_paused: queuePaused,
    pending_steer_count: pendingSteerCount,
    todos,
  }
}

function parseLiveEvent(value: unknown): ParsedLiveEvent | null {
  if (!isRecord(value) || typeof value.kind !== 'string') {
    return null
  }

  switch (value.kind) {
    case 'output': {
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      const journalSeq = nullablePositiveIntegerField(value, 'journal_seq')
      const event = outputEvent(value.event)
      return sessionId === null || runId === null || journalSeq === undefined || event === null
        ? null
        : { kind: 'output', session_id: sessionId, run_id: runId, journal_seq: journalSeq, event }
    }
    case 'activity_label': {
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      const targetSeq = positiveIntegerField(value, 'target_seq')
      const journalSeq = positiveIntegerField(value, 'journal_seq')
      const text = stringField(value, 'text')
      return sessionId === null || runId === null || targetSeq === null || journalSeq === null || text === null
        ? null
        : {
            kind: 'activity_label',
            session_id: sessionId,
            run_id: runId,
            target_seq: targetSeq,
            journal_seq: journalSeq,
            text,
          }
    }
    case 'portfolio_tool_succeeded': {
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      const portfolioIds = stringItems(value.portfolio_ids)
      return sessionId === null || runId === null || portfolioIds === null || typeof value.mutated !== 'boolean'
        ? null
        : {
            kind: 'portfolio_tool_succeeded',
            session_id: sessionId,
            run_id: runId,
            portfolio_ids: portfolioIds,
            mutated: value.mutated,
          }
    }
    case 'follow_up': {
      const eventKind = value.event_kind
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      const sourceRunId = stringField(value, 'source_run_id')
      const followUpId = stringField(value, 'follow_up_id')
      const text = stringField(value, 'text')
      const journalSeq = nullablePositiveIntegerField(value, 'journal_seq')
      const knownEventKind: FollowUpEventKind[] = [
        'steer_submitted',
        'steer_fallback_promoted',
        'queue_submitted',
        'queue_promoted',
      ]
      return typeof eventKind !== 'string' || !knownEventKind.includes(eventKind as FollowUpEventKind) || sessionId === null || runId === null || sourceRunId === null || followUpId === null || text === null || journalSeq === undefined
        ? null
        : {
            kind: 'follow_up',
            event_kind: eventKind as FollowUpEventKind,
            session_id: sessionId,
            run_id: runId,
            source_run_id: sourceRunId,
            follow_up_id: followUpId,
            text,
            journal_seq: journalSeq,
          }
    }
    case 'runtime_state':
      return runtimeSnapshot(value)
    case 'run_ended': {
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      const status = value.status
      const autoCompaction = value.auto_compaction
      const startedAt = isoTimestampField(value, 'started_at')
      const endedAt = isoTimestampField(value, 'ended_at')
      const durationMs = nonNegativeNumberField(value, 'duration_ms')
      const timingPresent = startedAt !== undefined || endedAt !== undefined || durationMs !== undefined
      const timing =
        startedAt !== undefined && startedAt !== null && endedAt !== undefined && endedAt !== null && durationMs !== undefined && durationMs !== null
          ? { started_at: startedAt, ended_at: endedAt, duration_ms: durationMs }
          : null
      const autoCompactionFailed = value.auto_compaction_failed
      const autoCompactionConsistencyUncertain = value.auto_compaction_consistency_uncertain
      let autoCompactionChanged: boolean | null = null
      if (autoCompaction !== null && autoCompaction !== undefined) {
        if (!isRecord(autoCompaction) || typeof autoCompaction.changed !== 'boolean') {
          return null
        }
        autoCompactionChanged = autoCompaction.changed
      }
      return sessionId === null || runId === null || (status !== 'completed' && status !== 'cancelled' && status !== 'failed') || (timingPresent && timing === null) || (autoCompactionFailed !== null && autoCompactionFailed !== undefined && typeof autoCompactionFailed !== 'boolean') || (autoCompactionConsistencyUncertain !== null && autoCompactionConsistencyUncertain !== undefined && typeof autoCompactionConsistencyUncertain !== 'boolean')
        ? null
        : {
            kind: 'run_ended',
            session_id: sessionId,
            run_id: runId,
            status,
            started_at: timing?.started_at ?? null,
            ended_at: timing?.ended_at ?? null,
            duration_ms: timing?.duration_ms ?? null,
            auto_compaction_changed: autoCompactionChanged,
            auto_compaction_failed: autoCompactionFailed ?? null,
            auto_compaction_consistency_uncertain: autoCompactionConsistencyUncertain ?? null,
          }
    }
    case 'approval_required': {
      const approvalId = stringField(value, 'approval_id')
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      const toolName = stringField(value, 'tool_name')
      const argumentsValue = nullableStringField(value, 'arguments')
      const reviewReason = nullableStringField(value, 'review_reason')
      return approvalId === null || sessionId === null || runId === null || toolName === null || argumentsValue === undefined || reviewReason === undefined
        ? null
        : {
            kind: 'approval_required',
            approval_id: approvalId,
            session_id: sessionId,
            run_id: runId,
            tool_name: toolName,
            arguments: argumentsValue,
            review_reason: reviewReason,
          }
    }
    case 'approval_resolved': {
      const approvalId = stringField(value, 'approval_id')
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      const approved = value.approved
      const source = value.source
      const reviewDecision = nullableStringField(value, 'review_decision')
      const reviewReason = nullableStringField(value, 'review_reason')
      const journalSeq = nullablePositiveIntegerField(value, 'journal_seq')
      return approvalId === null || sessionId === null || runId === null || typeof approved !== 'boolean' || (source !== 'user' && source !== 'permission') || (reviewDecision !== null && reviewDecision !== undefined && reviewDecision !== 'approve' && reviewDecision !== 'ask' && reviewDecision !== 'reject') || reviewReason === undefined || journalSeq === undefined
        ? null
        : {
            kind: 'approval_resolved',
            approval_id: approvalId,
            session_id: sessionId,
            run_id: runId,
            approved,
            source,
            review_decision: reviewDecision as 'approve' | 'ask' | 'reject' | null,
            review_reason: reviewReason,
            journal_seq: journalSeq,
          }
    }
    case 'approval_cancelled': {
      const approvalId = stringField(value, 'approval_id')
      const sessionId = stringField(value, 'session_id')
      const runId = stringField(value, 'run_id')
      return approvalId === null || sessionId === null || runId === null
        ? null
        : { kind: 'approval_cancelled', approval_id: approvalId, session_id: sessionId, run_id: runId }
    }
    default:
      return null
  }
}

function receivedAt(): string {
  return new Date().toISOString()
}

function overlayRecord(event: ParsedLiveEvent, runEndedSequence: number | null = null): JournalRecord | null {
  if (event.kind === 'run_ended') {
    if (runEndedSequence === null || event.started_at === null || event.ended_at === null || event.duration_ms === null) {
      return null
    }
    return {
      seq: runEndedSequence,
      timestamp: event.ended_at,
      type: 'run_ended',
      run_id: event.run_id,
      status: event.status,
      started_at: event.started_at,
      ended_at: event.ended_at,
      duration_ms: event.duration_ms,
    }
  }

  const timestamp = receivedAt()

  if (event.kind === 'output' && event.journal_seq !== null) {
    return { seq: event.journal_seq, timestamp, ...event.event }
  }

  if (event.kind === 'activity_label') {
    return {
      seq: event.journal_seq,
      timestamp,
      type: 'activity_label',
      target_seq: event.target_seq,
      text: event.text,
    }
  }

  if (event.kind === 'follow_up' && event.journal_seq !== null) {
    if (event.event_kind === 'steer_submitted') {
      return {
        seq: event.journal_seq,
        timestamp,
        type: 'user_steer',
        run_id: event.run_id,
        text: event.text,
      }
    }
    if (event.event_kind === 'queue_promoted') {
      return { seq: event.journal_seq, timestamp, type: 'user_message', text: event.text }
    }
  }

  return null
}

function approvalFromEvent(event: ApprovalRequiredLiveEvent): ApprovalRequest {
  return {
    kind: 'approval_required',
    approval_id: event.approval_id,
    session_id: event.session_id,
    run_id: event.run_id,
    tool_name: event.tool_name,
    arguments: event.arguments,
    review_reason: event.review_reason,
  }
}

function updateSessionApproval(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string,
  update: (current: ApprovalRequest[]) => ApprovalRequest[],
): void {
  queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(sessionId), (current) =>
    current ? { ...current, pending_approvals: update(current.pending_approvals) } : current,
  )
  queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
    current
      ? {
          ...current,
          pending_approvals: update(current.pending_approvals),
        }
      : current,
  )
}

function addPendingApproval(current: ApprovalRequest[], request: ApprovalRequest): ApprovalRequest[] {
  if (current.some((approval) => approval.approval_id === request.approval_id)) {
    return current.map((approval) => (approval.approval_id === request.approval_id ? request : approval))
  }
  return [...current, request]
}

function removePendingApproval(current: ApprovalRequest[], approvalId: string): ApprovalRequest[] {
  return current.filter((approval) => approval.approval_id !== approvalId)
}

function latestCanonicalSequence(queryClient: ReturnType<typeof useQueryClient>, sessionId: string): number | null {
  return historyNewestSeq(queryClient.getQueryData<HistoryInfiniteData>(queryKeys.sessionHistoryPages(sessionId)))
}

function canonicalRunEndedIds(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string,
): Set<string> {
  const data = queryClient.getQueryData<HistoryInfiniteData>(queryKeys.sessionHistoryPages(sessionId))
  const runIds = new Set<string>()
  for (const page of data?.pages ?? []) {
    for (const record of page.records) {
      if (isCompleteRunEndedRecord(record)) {
        runIds.add(record.run_id)
      }
    }
  }
  return runIds
}

export function LiveWebSocketProvider({ children }: PropsWithChildren) {
  const webConfig = useWebConfig()
  const queryClient = useQueryClient()
  const selectedMatch = useMatch('/c/:sessionId')
  const selectedSessionId = selectedMatch?.params.sessionId ?? null
  const selectedSessionRef = useRef<string | null>(selectedSessionId)
  selectedSessionRef.current = selectedSessionId

  const [status, setStatus] = useState<WebSocketConnectionStatus>('reconnecting')
  const [overlayState, setOverlayState] = useState<Map<number, JournalRecord>>(() => new Map())
  const [noticeState, setNoticeState] = useState<LiveNotice[]>([])
  const [resyncedRun, setResyncedRun] = useState<{ sessionId: string; runId: string } | null>(null)
  const overlayRef = useRef(overlayState)
  const liveSessionRef = useRef<string | null>(selectedSessionId)
  const mountedRef = useRef(false)
  const connectionStatusRef = useRef<WebSocketConnectionStatus>('disconnected')
  const resyncTokenRef = useRef(0)
  const resyncAbortRef = useRef<AbortController | null>(null)
  const resyncSessionRef = useRef<string | null>(null)
  const bufferedEventsRef = useRef<ParsedLiveEvent[]>([])
  const drainingRef = useRef(false)
  const deferredResyncRef = useRef<string | null>(null)
  const endedRunBySessionRef = useRef<Map<string, string>>(new Map())
  const processEventRef = useRef<(event: ParsedLiveEvent) => void>(() => undefined)
  const startResyncRef = useRef<(sessionId: string) => void>(() => undefined)

  const replaceOverlay = (next: Map<number, JournalRecord>) => {
    overlayRef.current = next
    setOverlayState(next)
  }

  const clearSelectedState = (sessionId: string | null) => {
    liveSessionRef.current = sessionId
    replaceOverlay(new Map())
    setNoticeState([])
    setResyncedRun(null)
  }

  const addNotice = (sessionId: string, key: string, text: string) => {
    if (selectedSessionRef.current !== sessionId) {
      return
    }

    if (liveSessionRef.current !== sessionId) {
      liveSessionRef.current = sessionId
      replaceOverlay(new Map())
      setNoticeState([])
    }

    const id = `${sessionId}:${key}`
    const notice: LiveNotice = {
      id,
      text,
      timestamp: receivedAt(),
    }
    setNoticeState((current) =>
      current.some((item) => item.id === id)
        ? current
        : [...current, notice].slice(-webConfig.max_notices),
    )
  }

  const invalidateSessionQueries = (sessionId: string) => {
    void queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
    void queryClient.invalidateQueries({ exact: true, queryKey: queryKeys.session(sessionId) })
    void queryClient.invalidateQueries({ exact: true, queryKey: queryKeys.sessionState(sessionId) })
    void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios() })
    void queryClient.invalidateQueries({ queryKey: queryKeys.portfolioRecords() })
    void queryClient.invalidateQueries({ exact: true, queryKey: queryKeys.sessionRelatedPortfolios(sessionId) })
    void queryClient.invalidateQueries({ queryKey: queryKeys.bootstrap() })
  }

  const nextLiveRunEndedSequence = (sessionId: string, runId: string): number | null => {
    if (canonicalRunEndedIds(queryClient, sessionId).has(runId)) {
      return null
    }

    for (const record of overlayRef.current.values()) {
      if (isCompleteRunEndedRecord(record) && record.run_id === runId) {
        return record.seq
      }
    }

    let newestSequence = latestCanonicalSequence(queryClient, sessionId) ?? 0
    for (const record of overlayRef.current.values()) {
      newestSequence = Math.max(newestSequence, record.seq)
    }
    return newestSequence + 1
  }

  const removeCanonicalRunEndedOverlay = (sessionId: string) => {
    const runIds = canonicalRunEndedIds(queryClient, sessionId)
    if (runIds.size === 0) {
      return
    }

    const next = new Map(overlayRef.current)
    for (const [sequence, record] of next) {
      if (isCompleteRunEndedRecord(record) && runIds.has(record.run_id)) {
        next.delete(sequence)
      }
    }
    if (next.size !== overlayRef.current.size) {
      replaceOverlay(next)
    }
  }

  const requestSelectedResync = (sessionId: string) => {
    if (selectedSessionRef.current !== sessionId) {
      return
    }
    if (drainingRef.current) {
      deferredResyncRef.current = sessionId
      return
    }
    if (resyncSessionRef.current === sessionId) {
      return
    }
    startResyncRef.current(sessionId)
  }

  const processEvent = (event: ParsedLiveEvent) => {
    if (event.kind === 'approval_required') {
      const request = approvalFromEvent(event)
      updateSessionApproval(queryClient, event.session_id, (current) => addPendingApproval(current, request))
    } else if (event.kind === 'approval_resolved') {
      updateSessionApproval(queryClient, event.session_id, (current) => removePendingApproval(current, event.approval_id))
      if (selectedSessionRef.current === event.session_id) {
        requestSelectedResync(event.session_id)
      }
    } else if (event.kind === 'approval_cancelled') {
      updateSessionApproval(queryClient, event.session_id, (current) => removePendingApproval(current, event.approval_id))
    } else if (event.kind === 'runtime_state') {
      queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(event.session_id), (current) =>
        current ? { ...current, runtime: event } : current,
      )
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
        current?.initial_session_id === event.session_id ? { ...current, runtime: event } : current,
      )
    } else if (event.kind === 'run_ended') {
      endedRunBySessionRef.current.set(event.session_id, event.run_id)
      invalidateSessionQueries(event.session_id)
      if (event.status === 'cancelled') {
        addNotice(event.session_id, `${event.run_id}:cancelled`, 'Run stopped.')
      } else if (event.status === 'failed') {
        addNotice(event.session_id, `${event.run_id}:failed`, 'Run failed.')
      }
      if (event.auto_compaction_changed === true) {
        addNotice(event.session_id, `${event.run_id}:compacted`, 'Context compacted automatically.')
      }
      if (event.auto_compaction_failed === true) {
        addNotice(event.session_id, `${event.run_id}:compaction-failed`, 'Conversation compaction failed.')
      }
      if (event.auto_compaction_consistency_uncertain === true) {
        addNotice(event.session_id, `${event.run_id}:consistency-uncertain`, 'Conversation state may be inconsistent.')
      }
      if (selectedSessionRef.current === event.session_id) {
        requestSelectedResync(event.session_id)
      }
    } else if (event.kind === 'portfolio_tool_succeeded') {
      void queryClient.invalidateQueries({ exact: true, queryKey: queryKeys.sessionRelatedPortfolios(event.session_id) })
      if (event.mutated) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.portfolios() })
        for (const portfolioId of event.portfolio_ids) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.portfolio(portfolioId) })
        }
      }
    } else if (event.kind === 'follow_up') {
      if (event.event_kind === 'steer_fallback_promoted') {
        addNotice(event.session_id, `${event.follow_up_id}:steer-fallback`, 'Steer was promoted to a new run.')
      }
      if (event.event_kind === 'queue_submitted' || event.event_kind === 'queue_promoted') {
        requestSelectedResync(event.session_id)
      }
    }

    const runEndedSequence = event.kind === 'run_ended' ? nextLiveRunEndedSequence(event.session_id, event.run_id) : null
    const record = overlayRecord(event, runEndedSequence)
    if (record !== null && event.session_id === selectedSessionRef.current) {
      const resetOverlay = liveSessionRef.current !== event.session_id
      if (liveSessionRef.current !== event.session_id) {
        liveSessionRef.current = event.session_id
        overlayRef.current = new Map()
        setNoticeState([])
      }
      setOverlayState((current) => {
        const next = new Map(resetOverlay ? [] : current)
        next.set(record.seq, record)
        overlayRef.current = next
        return next
      })
    }
  }

  processEventRef.current = processEvent

  const startResync = async (sessionId: string) => {
    const token = resyncTokenRef.current + 1
    resyncTokenRef.current = token
    const previousSessionId = resyncSessionRef.current
    const pendingEvents = previousSessionId === sessionId ? bufferedEventsRef.current : []
    resyncAbortRef.current?.abort()
    const controller = new AbortController()
    resyncAbortRef.current = controller
    resyncSessionRef.current = sessionId
    bufferedEventsRef.current = pendingEvents

    const historyKey = queryKeys.sessionHistoryPages(sessionId)
    await queryClient.cancelQueries({ exact: true, queryKey: historyKey })

    if (!mountedRef.current || token !== resyncTokenRef.current || selectedSessionRef.current !== sessionId) {
      return
    }

    const requests = await Promise.allSettled([
      getBootstrap({ signal: controller.signal }),
      getSessions({ signal: controller.signal }),
      getSessionState(sessionId, { signal: controller.signal }),
      getSessionHistory(sessionId, { limit: webConfig.history_page_size, signal: controller.signal }),
    ])

    if (!mountedRef.current || token !== resyncTokenRef.current || selectedSessionRef.current !== sessionId) {
      return
    }

    const bootstrap = requests[0].status === 'fulfilled' ? requests[0].value : null
    const sessions = requests[1].status === 'fulfilled' ? requests[1].value : null
    const state = requests[2].status === 'fulfilled' ? requests[2].value : null
    const latest = requests[3].status === 'fulfilled' ? requests[3].value : null

    if (bootstrap) {
      queryClient.setQueryData(queryKeys.bootstrap(), bootstrap)
      queryClient.setQueryData(queryKeys.sessions(), { sessions: bootstrap.sessions })
    }
    if (sessions) {
      queryClient.setQueryData(queryKeys.sessions(), sessions)
    }
    if (state) {
      queryClient.setQueryData(queryKeys.sessionState(sessionId), state)
      queryClient.setQueryData(queryKeys.session(sessionId), { session: state.session })
    }
    if (latest) {
      queryClient.setQueryData<HistoryInfiniteData>(historyKey, (current) =>
        mergeHistoryPages(current, latest, webConfig.history_page_size),
      )
      const endedRunId = endedRunBySessionRef.current.get(sessionId)
      if (endedRunId) {
        endedRunBySessionRef.current.delete(sessionId)
        setResyncedRun({ sessionId, runId: endedRunId })
      }
    } else {
      await queryClient.invalidateQueries({
        exact: true,
        queryKey: historyKey,
        refetchType: 'active',
      })

      if (!mountedRef.current || token !== resyncTokenRef.current || selectedSessionRef.current !== sessionId) {
        return
      }
    }

    const canonicalRunIds = canonicalRunEndedIds(queryClient, sessionId)
    removeCanonicalRunEndedOverlay(sessionId)
    const newestSeq = latest?.newest_seq ?? latestCanonicalSequence(queryClient, sessionId)
    if (newestSeq !== null) {
      const next = new Map(overlayRef.current)
      for (const [sequence, record] of next) {
        const isUnreconciledRunEnded = isCompleteRunEndedRecord(record) && !canonicalRunIds.has(record.run_id)
        if (sequence <= newestSeq && !isUnreconciledRunEnded) {
          next.delete(sequence)
        }
      }
      replaceOverlay(next)
    }

    resyncSessionRef.current = null
    resyncAbortRef.current = null
    const buffered = bufferedEventsRef.current
    bufferedEventsRef.current = []
    drainingRef.current = true
    for (const event of buffered) {
      if (event.session_id === sessionId && selectedSessionRef.current === sessionId) {
        processEventRef.current(event)
      }
    }
    drainingRef.current = false
    const deferredSessionId = deferredResyncRef.current
    deferredResyncRef.current = null
    if (deferredSessionId && selectedSessionRef.current === deferredSessionId) {
      startResyncRef.current(deferredSessionId)
    } else if (connectionStatusRef.current === 'connected') {
      setStatus('connected')
    }
  }

  startResyncRef.current = (sessionId: string) => {
    void startResync(sessionId)
  }

  useEffect(() => {
    resyncTokenRef.current += 1
    resyncAbortRef.current?.abort()
    resyncAbortRef.current = null
    resyncSessionRef.current = null
    bufferedEventsRef.current = []
    deferredResyncRef.current = null
    clearSelectedState(selectedSessionId)
    if (selectedSessionId && connectionStatusRef.current === 'connected') {
      setStatus('reconnecting')
      startResyncRef.current(selectedSessionId)
    }
  }, [selectedSessionId])

  useEffect(() => {
    mountedRef.current = true
    const connection = createWebSocketConnection({
      baseDelayMs: webConfig.websocket_reconnect_base_delay_ms,
      maxDelayMs: webConfig.websocket_reconnect_max_delay_ms,
      onMessage: (payload) => {
        const event = parseLiveEvent(payload)
        if (event === null) {
          return
        }
        const resyncSessionId = resyncSessionRef.current
        if (resyncSessionId !== null && event.session_id === resyncSessionId) {
          bufferedEventsRef.current.push(event)
          return
        }
        processEventRef.current(event)
      },
      onStatusChange: (nextStatus) => {
        connectionStatusRef.current = nextStatus
        if (nextStatus === 'connected') {
          const currentSessionId = selectedSessionRef.current
          if (currentSessionId) {
            setStatus('reconnecting')
            requestSelectedResync(currentSessionId)
          } else {
            setStatus('connected')
          }
        } else {
          setStatus(nextStatus)
        }
      },
    })

    return () => {
      mountedRef.current = false
      resyncTokenRef.current += 1
      resyncAbortRef.current?.abort()
      resyncAbortRef.current = null
      resyncSessionRef.current = null
      bufferedEventsRef.current = []
      endedRunBySessionRef.current.clear()
      connection.close()
    }
  }, [
    webConfig.websocket_reconnect_base_delay_ms,
    webConfig.websocket_reconnect_max_delay_ms,
  ])

  const records = useMemo(
    () => (liveSessionRef.current === selectedSessionId ? [...overlayState.values()] : EMPTY_RECORDS),
    [overlayState, selectedSessionId],
  )
  const notices = liveSessionRef.current === selectedSessionId ? noticeState : EMPTY_NOTICES
  const resyncedRunId = resyncedRun?.sessionId === selectedSessionId ? resyncedRun.runId : null
  const value = useMemo<LiveWebSocketContextValue>(
    () => ({ selectedSessionId, status, records, notices, resyncedRunId }),
    [notices, records, resyncedRunId, selectedSessionId, status],
  )

  return <LiveWebSocketContext.Provider value={value}>{children}</LiveWebSocketContext.Provider>
}

export function useWebSocketStatus(): WebSocketConnectionStatus {
  return useContext(LiveWebSocketContext).status
}

export function useLiveSession(sessionId: string): LiveSessionValue {
  const context = useContext(LiveWebSocketContext)
  return useMemo(
    () =>
      context.selectedSessionId === sessionId
        ? { records: context.records, notices: context.notices, resyncedRunId: context.resyncedRunId }
        : { records: EMPTY_RECORDS, notices: EMPTY_NOTICES, resyncedRunId: null },
    [context.notices, context.records, context.resyncedRunId, context.selectedSessionId, sessionId],
  )
}
