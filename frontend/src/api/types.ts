export type JsonPrimitive = boolean | number | string | null
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[]
export type JsonObject = { [key: string]: JsonValue }

export type ReasoningEffort = 'none' | 'minimal' | 'low' | 'medium' | 'high' | 'xhigh' | 'max'
export type PermissionMode = 'manual' | 'review'
export type FollowUpBehavior = 'steer' | 'queue'
export type RunPhase = 'running' | 'waiting_approval' | 'stopping'
export type TodoStatus = 'pending' | 'in_progress' | 'completed' | 'failed'

export interface SessionRecord {
  session_id: string
  title: string | null
  branch_from_session_id: string | null
  archived_at: string | null
  created_at: string
  updated_at: string
}

export interface TodoItem {
  content: string
  status: TodoStatus
}

export interface TokenUsage {
  requests: number
  input_tokens: number
  cached_input_tokens: number
  cache_write_input_tokens: number
  output_tokens: number
  reasoning_output_tokens: number
  total_tokens: number
  last_request_total_tokens: number
}

export interface RuntimeSnapshot {
  kind: 'runtime_state'
  session_id: string
  run_id: string | null
  run_started_at: string | null
  run_phase: RunPhase | null
  active_follow_up_behavior: FollowUpBehavior | null
  queued_count: number
  queue_paused: boolean
  pending_steer_count: number
  todos: TodoItem[]
}

export interface SessionPresentationState {
  usage: TokenUsage
  main_context_tokens: number | null
  last_todo_run_id: string | null
  last_todos: TodoItem[]
}

export interface QueueItem {
  queue_id: string
  session_id: string
  text: string
  created_at: string
}

export interface ApprovalRequest {
  kind: 'approval_required'
  approval_id: string
  session_id: string
  run_id: string
  tool_name: string
  arguments: string | null
  review_reason: string | null
}

export interface Defaults {
  reasoning_effort: ReasoningEffort
  permission_mode: PermissionMode
  follow_up_behavior: FollowUpBehavior
}

export interface BootstrapResponse {
  version: string
  initial_session_id: string
  agent_name: string
  context_window_tokens: number
  defaults: Defaults
  sessions: SessionRecord[]
  runtime: RuntimeSnapshot
  presentation: SessionPresentationState
  pending_approvals: ApprovalRequest[]
}

export interface SessionListResponse {
  sessions: SessionRecord[]
}

export interface SessionResponse {
  session: SessionRecord
}

export interface SessionStateResponse {
  session: SessionRecord
  runtime: RuntimeSnapshot
  presentation: SessionPresentationState
  queue: QueueItem[]
  pending_approvals: ApprovalRequest[]
}

export interface OutputEventAgentChanged {
  type: 'agent_changed'
  name: string
}

export interface OutputEventReasoning {
  type: 'reasoning'
  text: string
}

export interface OutputEventToolCalled {
  type: 'tool_called'
  name: string
  arguments: string | null
}

export interface OutputEventToolOutput {
  type: 'tool_output'
  output: string
}

export interface OutputEventAssistantMessage {
  type: 'assistant_message'
  text: string
}

export type OutputEvent =
  | OutputEventAgentChanged
  | OutputEventReasoning
  | OutputEventToolCalled
  | OutputEventToolOutput
  | OutputEventAssistantMessage

export interface OutputLiveEvent {
  kind: 'output'
  session_id: string
  run_id: string
  journal_seq: number | null
  event: OutputEvent
}

export type FollowUpEventKind = 'steer_submitted' | 'steer_fallback_promoted' | 'queue_submitted' | 'queue_promoted'

export interface FollowUpLiveEvent {
  kind: 'follow_up'
  event_kind: FollowUpEventKind
  session_id: string
  run_id: string
  source_run_id: string
  follow_up_id: string
  text: string
  journal_seq: number | null
}

export interface RuntimeStateLiveEvent extends RuntimeSnapshot {
  kind: 'runtime_state'
}

export interface RunEndedLiveEvent {
  kind: 'run_ended'
  session_id: string
  run_id: string
  status: 'completed' | 'cancelled' | 'failed'
  started_at?: string
  ended_at?: string
  duration_ms?: number
  discarded_steer_count: number
  main_usage: TokenUsage | null
  auxiliary_usage: TokenUsage | null
  main_context_tokens: number | null
  auto_compaction: CompactionResult | null
  auto_compaction_failed: boolean | null
  auto_compaction_consistency_uncertain: boolean | null
}

export interface ActivityLabelLiveEvent {
  kind: 'activity_label'
  session_id: string
  run_id: string
  target_seq: number
  journal_seq: number
  text: string
}

export interface ApprovalRequiredLiveEvent extends ApprovalRequest {
  kind: 'approval_required'
}

export interface ApprovalResolvedLiveEvent {
  kind: 'approval_resolved'
  approval_id: string
  session_id: string
  run_id: string
  approved: boolean
  source: 'user' | 'permission'
  review_decision: 'approve' | 'ask' | 'reject' | null
  review_reason: string | null
  journal_seq: number | null
}

export interface ApprovalCancelledLiveEvent {
  kind: 'approval_cancelled'
  approval_id: string
  session_id: string
  run_id: string
}

export type LiveEvent =
  | OutputLiveEvent
  | RuntimeStateLiveEvent
  | FollowUpLiveEvent
  | RunEndedLiveEvent
  | ActivityLabelLiveEvent
  | ApprovalRequiredLiveEvent
  | ApprovalResolvedLiveEvent
  | ApprovalCancelledLiveEvent

export interface JournalRecordBase {
  seq: number
  timestamp: string
}

export interface UserMessageRecord extends JournalRecordBase {
  type: 'user_message'
  text: string
}

export interface UserSteerRecord extends JournalRecordBase {
  type: 'user_steer'
  run_id: string
  text: string
}

export interface ActivityLabelRecord extends JournalRecordBase {
  type: 'activity_label'
  target_seq: number
  text: string
}

export interface ApprovalRecord extends JournalRecordBase {
  type: 'approval'
  run_id: string
  approval_id: string
  tool_name: string
  arguments: string | null
  approved: boolean
  source: 'user' | 'permission'
  review_decision?: 'approve' | 'ask' | 'reject'
  review_reason?: string
}

export interface RunEndedRecord extends JournalRecordBase {
  type: 'run_ended'
  run_id: string
  status: 'completed' | 'cancelled' | 'failed'
  started_at?: string
  ended_at?: string
  duration_ms?: number
}

export type JournalOutputRecord = JournalRecordBase & OutputEvent

export type JournalRecord =
  | UserMessageRecord
  | UserSteerRecord
  | ActivityLabelRecord
  | ApprovalRecord
  | RunEndedRecord
  | JournalOutputRecord

export interface HistoryResponse {
  records: JournalRecord[]
  has_older: boolean
  oldest_seq: number | null
  newest_seq: number | null
}

export interface HealthResponse {
  status: string
  version: string
}

export interface UserInputSubmission {
  session_id: string
  disposition: 'run_started' | 'steer_submitted' | 'queue_submitted'
  run_id: string
  follow_up_id: string | null
}

export interface StopResponse {
  session_id: string
  run_id: string
  status: 'stopping'
}

export interface ClearSessionResponse {
  replacement_session_id: string
}

export interface DeleteSessionResponse {
  deleted: true
  session_id: string
  replacement_session_id: string | null
}

export interface DiscardUnusedResponse {
  discarded: boolean
}

export interface QueueMutationResponse {
  runtime: RuntimeSnapshot
  queue: QueueItem[]
}

export interface RemovedQueueItemResponse extends QueueMutationResponse {
  removed: QueueItem
}

export interface ClearQueueResponse extends QueueMutationResponse {
  cleared_count: number
}

export interface ResolveApprovalResponse {
  approval_id: string
  approved: boolean
}

export interface CompactionResult {
  changed: boolean
  usage: TokenUsage
  source_items: number
  summary_chars: number
}

export interface BackgroundJob {
  job_id: string
  pid: number
  process_id: number | null
  command: string
  status: 'running' | 'exited' | 'lost'
  owner_session_id: string
  owner_run_id: string
  started_at: string
  finished_at: string | null
  exit_code: number | null
  stdout_log: string
  stderr_log: string
}

export interface ProcessListResponse {
  processes: BackgroundJob[]
}

export interface ApiErrorDetails {
  [key: string]: JsonValue | undefined
  errors?: JsonValue[]
}

export interface ApiErrorEnvelope {
  error: {
    code: string
    message: string
    details: ApiErrorDetails
  }
}

export interface SendMessageBody {
  text: string
}

export interface RenameSessionBody {
  title: string
}

export interface ConfirmBody {
  confirm: boolean
}

export interface ResolveApprovalBody {
  approved: boolean
}

export interface UpdateDefaultsBody {
  reasoning_effort?: ReasoningEffort
  permission_mode?: PermissionMode
  follow_up_behavior?: FollowUpBehavior
}
