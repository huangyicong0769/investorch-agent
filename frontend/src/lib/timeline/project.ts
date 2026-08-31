import type { JournalRecord } from '../../api/types'

export interface TimelineUserMessageViewModel {
  type: 'user'
  id: string
  seq: number
  timestamp: string
  text: string
}

export interface TimelineSteerViewModel {
  type: 'steer'
  id: string
  seq: number
  timestamp: string
  runId: string
  text: string
}

export interface TimelineSystemViewModel {
  type: 'system'
  id: string
  seq: number
  timestamp: string
  name: string
  text: string
}

export interface TimelineAssistantMessageViewModel {
  type: 'assistant_message'
  id: string
  seq: number
  timestamp: string
  text: string
}

export interface TimelineReasoningViewModel {
  type: 'reasoning'
  id: string
  seq: number
  timestamp: string
  text: string
}

export interface TimelineToolViewModel {
  type: 'tool'
  id: string
  seq: number
  timestamp: string
  name: string
  arguments: string | null
  label: string | null
  observation: string | null
  observationSeq: number | null
}

export interface TimelineUnmatchedToolOutputViewModel {
  type: 'unmatched_tool_output'
  id: string
  seq: number
  timestamp: string
  output: string
}

export interface TimelineApprovalViewModel {
  type: 'approval'
  id: string
  seq: number
  timestamp: string
  runId: string
  approvalId: string
  toolName: string
  arguments: string | null
  approved: boolean
  source: 'user' | 'permission'
  reviewDecision: 'approve' | 'ask' | 'reject' | null
  reviewReason: string | null
}

export type TimelineActivityViewModel =
  | TimelineReasoningViewModel
  | TimelineToolViewModel
  | TimelineUnmatchedToolOutputViewModel
  | TimelineApprovalViewModel

export interface TimelineActivityGroupViewModel {
  type: 'activity'
  id: string
  seq: number
  timestamp: string
  title: string
  collapsed: true
  items: TimelineActivityViewModel[]
}

export type TimelineAssistantContentViewModel =
  | TimelineActivityGroupViewModel
  | TimelineAssistantMessageViewModel

export interface TimelineAssistantTurnViewModel {
  type: 'assistant'
  id: string
  seq: number
  timestamp: string
  content: TimelineAssistantContentViewModel[]
}

export interface TimelineRunTimingViewModel {
  type: 'run_timing'
  id: string
  seq: number
  timestamp: string
  runId: string
  status: 'completed' | 'cancelled' | 'failed'
  startedAt: string
  endedAt: string
  durationMs: number
}

export type CompleteRunEndedRecord = Extract<JournalRecord, { type: 'run_ended' }> & {
  started_at: string
  ended_at: string
  duration_ms: number
}

export function isCompleteRunEndedRecord(record: JournalRecord): record is CompleteRunEndedRecord {
  return (
    record.type === 'run_ended' &&
    typeof record.started_at === 'string' &&
    Number.isFinite(Date.parse(record.started_at)) &&
    typeof record.ended_at === 'string' &&
    Number.isFinite(Date.parse(record.ended_at)) &&
    typeof record.duration_ms === 'number' &&
    Number.isFinite(record.duration_ms) &&
    record.duration_ms >= 0
  )
}

export type TimelineViewModel =
  | TimelineUserMessageViewModel
  | TimelineSteerViewModel
  | TimelineAssistantTurnViewModel
  | TimelineRunTimingViewModel
  | TimelineSystemViewModel

interface MutableAssistantTurn {
  view: TimelineAssistantTurnViewModel
  activity: TimelineActivityGroupViewModel | null
}

function canonicalRecords(records: readonly JournalRecord[]): JournalRecord[] {
  const bySequence = new Map<number, JournalRecord>()
  const runEndedById = new Map<string, Extract<JournalRecord, { type: 'run_ended' }>>()

  for (const record of records) {
    if (Number.isInteger(record.seq) && record.seq > 0) {
      if (record.type === 'run_ended') {
        const previous = runEndedById.get(record.run_id)
        if (previous) {
          if (isCompleteRunEndedRecord(previous) || !isCompleteRunEndedRecord(record)) {
            continue
          }
          bySequence.delete(previous.seq)
        }
        runEndedById.set(record.run_id, record)
      }
      bySequence.set(record.seq, record)
    }
  }

  return [...bySequence.values()].sort((left, right) => left.seq - right.seq)
}

function activityTitle(group: TimelineActivityGroupViewModel): string {
  const latestTool = [...group.items].reverse().find((item): item is TimelineToolViewModel => item.type === 'tool')
  if (latestTool?.label) {
    return latestTool.label
  }
  if (latestTool) {
    return `Calling ${latestTool.name}…`
  }

  const latestUnmatched = [...group.items]
    .reverse()
    .find((item): item is TimelineUnmatchedToolOutputViewModel => item.type === 'unmatched_tool_output')
  if (latestUnmatched) {
    return 'Unmatched tool output'
  }

  const latestApproval = [...group.items].reverse().find((item): item is TimelineApprovalViewModel => item.type === 'approval')
  if (latestApproval) {
    const automatic = latestApproval.source === 'permission'
    const outcome = latestApproval.approved
      ? automatic
        ? '✓ Auto-approved'
        : '✓ Approved'
      : automatic
        ? '⊘ Auto-rejected'
        : '⊘ Rejected'
    return `${outcome} · ${latestApproval.toolName}`
  }

  return 'Thinking…'
}

function refreshActivityTitle(group: TimelineActivityGroupViewModel): void {
  group.title = activityTitle(group)
}

function ensureAssistantTurn(
  activeTurn: MutableAssistantTurn | null,
  record: JournalRecord,
): MutableAssistantTurn {
  if (activeTurn) {
    return activeTurn
  }

  const view: TimelineAssistantTurnViewModel = {
    type: 'assistant',
    id: `assistant-${record.seq}`,
    seq: record.seq,
    timestamp: record.timestamp,
    content: [],
  }
  return { view, activity: null }
}

function ensureActivityGroup(
  activeTurn: MutableAssistantTurn,
  record: JournalRecord,
): TimelineActivityGroupViewModel {
  if (activeTurn.activity) {
    return activeTurn.activity
  }

  const items: TimelineActivityViewModel[] = []
  const group: TimelineActivityGroupViewModel = {
    type: 'activity',
    id: `activity-${record.seq}`,
    seq: record.seq,
    timestamp: record.timestamp,
    title: 'Thinking…',
    collapsed: true,
    items,
  }
  activeTurn.activity = group
  activeTurn.view.content.push(group)
  return group
}

function appendReasoning(activeTurn: MutableAssistantTurn, record: Extract<JournalRecord, { type: 'reasoning' }>): void {
  const group = ensureActivityGroup(activeTurn, record)
  group.items.push({
    type: 'reasoning',
    id: `reasoning-${record.seq}`,
    seq: record.seq,
    timestamp: record.timestamp,
    text: record.text,
  })
  refreshActivityTitle(group)
}

function appendTool(
  activeTurn: MutableAssistantTurn,
  record: Extract<JournalRecord, { type: 'tool_called' }>,
  labels: ReadonlyMap<number, string>,
): TimelineToolViewModel {
  const group = ensureActivityGroup(activeTurn, record)
  const step: TimelineToolViewModel = {
    type: 'tool',
    id: `tool-${record.seq}`,
    seq: record.seq,
    timestamp: record.timestamp,
    name: record.name,
    arguments: record.arguments,
    label: labels.get(record.seq) ?? null,
    observation: null,
    observationSeq: null,
  }
  group.items.push(step)
  refreshActivityTitle(group)
  return step
}

function appendUnmatchedOutput(
  activeTurn: MutableAssistantTurn,
  record: Extract<JournalRecord, { type: 'tool_output' }>,
): void {
  const group = ensureActivityGroup(activeTurn, record)
  group.items.push({
    type: 'unmatched_tool_output',
    id: `unmatched-tool-output-${record.seq}`,
    seq: record.seq,
    timestamp: record.timestamp,
    output: record.output,
  })
  refreshActivityTitle(group)
}

function appendApproval(activeTurn: MutableAssistantTurn, record: Extract<JournalRecord, { type: 'approval' }>): void {
  const group = ensureActivityGroup(activeTurn, record)
  group.items.push({
    type: 'approval',
    id: `approval-${record.seq}`,
    seq: record.seq,
    timestamp: record.timestamp,
    runId: record.run_id,
    approvalId: record.approval_id,
    toolName: record.tool_name,
    arguments: record.arguments,
    approved: record.approved,
    source: record.source,
    reviewDecision: record.review_decision ?? null,
    reviewReason: record.review_reason ?? null,
  })
  refreshActivityTitle(group)
}

/**
 * Project journal history into stable, render-ready conversation rows.
 *
 * The input may contain overlapping pages or arrive out of order. Projection
 * deliberately starts from one canonical sequence-sorted set, so tool pairing
 * and labels are recomputed whenever callers provide a different page set.
 */
export function projectTimeline(records: readonly JournalRecord[]): TimelineViewModel[] {
  const orderedRecords = canonicalRecords(records)
  const labels = new Map<number, string>()

  for (const record of orderedRecords) {
    if (record.type === 'activity_label' && record.text.trim()) {
      labels.set(record.target_seq, record.text.trim())
    }
  }

  const timeline: TimelineViewModel[] = []
  const pendingTools: TimelineToolViewModel[] = []
  let activeTurn: MutableAssistantTurn | null = null
  let lastAgentChangedName: string | null = null

  const finishAssistantTurn = () => {
    if (activeTurn) {
      timeline.push(activeTurn.view)
      activeTurn = null
    }
  }

  for (const record of orderedRecords) {
    if (record.type === 'activity_label') {
      continue
    }

    if (record.type === 'user_message') {
      finishAssistantTurn()
      pendingTools.length = 0
      timeline.push({
        type: 'user',
        id: `user-${record.seq}`,
        seq: record.seq,
        timestamp: record.timestamp,
        text: record.text,
      })
      lastAgentChangedName = null
      continue
    }

    if (record.type === 'user_steer') {
      finishAssistantTurn()
      timeline.push({
        type: 'steer',
        id: `steer-${record.seq}`,
        seq: record.seq,
        timestamp: record.timestamp,
        runId: record.run_id,
        text: record.text,
      })
      lastAgentChangedName = null
      continue
    }

    if (record.type === 'run_ended') {
      if (!isCompleteRunEndedRecord(record)) {
        continue
      }
      finishAssistantTurn()
      pendingTools.length = 0
      timeline.push({
        type: 'run_timing',
        id: `run-timing-${record.seq}`,
        seq: record.seq,
        timestamp: record.timestamp,
        runId: record.run_id,
        status: record.status,
        startedAt: record.started_at,
        endedAt: record.ended_at,
        durationMs: record.duration_ms,
      })
      lastAgentChangedName = null
      continue
    }

    if (record.type === 'agent_changed') {
      finishAssistantTurn()
      const name = record.name.trim() || record.name
      if (lastAgentChangedName !== name) {
        timeline.push({
          type: 'system',
          id: `system-${record.seq}`,
          seq: record.seq,
          timestamp: record.timestamp,
          name,
          text: `Agent → ${name}`,
        })
      }
      lastAgentChangedName = name
      continue
    }

    lastAgentChangedName = null

    if (record.type === 'assistant_message') {
      activeTurn = ensureAssistantTurn(activeTurn, record)
      const message: TimelineAssistantMessageViewModel = {
        type: 'assistant_message',
        id: `assistant-message-${record.seq}`,
        seq: record.seq,
        timestamp: record.timestamp,
        text: record.text,
      }
      activeTurn.view.content.push(message)
      finishAssistantTurn()
      pendingTools.length = 0
      continue
    }

    if (record.type === 'reasoning') {
      activeTurn = ensureAssistantTurn(activeTurn, record)
      appendReasoning(activeTurn, record)
      continue
    }

    if (record.type === 'tool_called') {
      activeTurn = ensureAssistantTurn(activeTurn, record)
      const step = appendTool(activeTurn, record, labels)
      pendingTools.push(step)
      continue
    }

    if (record.type === 'tool_output') {
      const pending = pendingTools.shift()
      if (pending) {
        pending.observation = record.output
        pending.observationSeq = record.seq
      } else {
        activeTurn = ensureAssistantTurn(activeTurn, record)
        appendUnmatchedOutput(activeTurn, record)
      }
      continue
    }

    if (record.type === 'approval') {
      activeTurn = ensureAssistantTurn(activeTurn, record)
      appendApproval(activeTurn, record)
    }
  }

  finishAssistantTurn()
  return timeline
}

export function formatJsonValue(value: string | null): string {
  if (!value) {
    return ''
  }

  try {
    return JSON.stringify(JSON.parse(value), null, 2)
  } catch {
    return value
  }
}
