import type { SessionRecord, SessionStateResponse } from '../../api/types'
import { sessionTitle, shortSessionId } from '../../lib/session'
import { getSessionStatus } from '../../lib/session-status'

interface SessionItemProps {
  active: boolean
  archived?: boolean
  onSelect: (session: SessionRecord) => void
  session: SessionRecord
  state?: SessionStateResponse
}

export function SessionItem({ active, archived = false, onSelect, session, state }: SessionItemProps) {
  const status = state ? getSessionStatus(state) : 'Ready'
  const queuedCount = state?.runtime.queued_count ?? 0

  return (
    <li className="list-none">
      <button
        aria-current={active ? 'page' : undefined}
        className={`w-full min-w-0 rounded-lg border px-3 py-2 text-left transition-colors ${
          active ? 'border-border bg-muted' : 'border-transparent hover:border-border hover:bg-muted/60'
        }`}
        onClick={() => onSelect(session)}
        title={sessionTitle(session)}
        type="button"
      >
        <span className="block truncate text-sm font-medium">{sessionTitle(session)}</span>
        <span className="mt-1 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          <span className="truncate">{shortSessionId(session.session_id)}</span>
          <span aria-label={`Status: ${status}`} className="truncate">
            {status === 'Approval' ? `! ${status}` : status}
          </span>
          {queuedCount > 0 ? <span className="shrink-0">{queuedCount} queued</span> : null}
          {archived ? <span className="shrink-0">Archived</span> : null}
        </span>
      </button>
    </li>
  )
}
