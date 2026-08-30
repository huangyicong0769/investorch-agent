import type { SessionStateResponse } from '../api/types'

export type SessionStatus = 'Approval' | 'Stopping' | 'Running' | 'Queue paused' | 'Queued' | 'Ready'

/**
 * Derive the compact status shown beside a session in the sidebar.
 *
 * Keep the order explicit: the same state can have a pending approval and a
 * running queue, but the approval is the most useful signal to show first.
 */
export function getSessionStatus(state: SessionStateResponse): SessionStatus {
  if (state.pending_approvals.length > 0) {
    return 'Approval'
  }

  if (state.runtime.run_phase === 'stopping') {
    return 'Stopping'
  }

  if (state.runtime.run_phase === 'running' || state.runtime.run_phase === 'waiting_approval') {
    return 'Running'
  }

  if (state.runtime.queue_paused && state.runtime.queued_count > 0) {
    return 'Queue paused'
  }

  if (state.runtime.queued_count > 0) {
    return 'Queued'
  }

  return 'Ready'
}
