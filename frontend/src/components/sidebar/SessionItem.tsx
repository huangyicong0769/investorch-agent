import { Archive, Trash2 } from 'lucide-react'

import type { SessionRecord, SessionStateResponse } from '../../api/types'
import { sessionTitle, shortSessionId } from '../../lib/session'
import { getSessionStatus } from '../../lib/session-status'
import { Button } from '@/components/ui/button'

interface SessionItemProps {
  active: boolean
  actionsDisabled?: boolean
  archived?: boolean
  onArchive?: (session: SessionRecord) => void
  onDelete?: (session: SessionRecord) => void
  onSelect: (session: SessionRecord) => void
  session: SessionRecord
  state?: SessionStateResponse
}

export function SessionItem({
  active,
  actionsDisabled = false,
  archived = false,
  onArchive,
  onDelete,
  onSelect,
  session,
  state,
}: SessionItemProps) {
  const status = state ? getSessionStatus(state) : 'Ready'
  const queuedCount = state?.runtime.queued_count ?? 0
  const approvalCount = state?.pending_approvals.length ?? 0

  return (
    <li className="group relative list-none">
      <Button
        size={null}
        variant={null}
        aria-current={active ? 'page' : undefined}
        className={`block w-full min-w-0 rounded-lg border py-2 pl-3 text-left transition-colors ${
          onArchive || onDelete ? 'pr-20' : 'pr-3'
        } ${
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
            {status === 'Approval'
              ? `! ${approvalCount > 1 ? `${approvalCount} approvals` : 'Approval'}`
              : status}
          </span>
          {queuedCount > 0 ? <span className="shrink-0">{queuedCount} queued</span> : null}
          {archived ? <span className="shrink-0">Archived</span> : null}
        </span>
      </Button>
      {onArchive || onDelete ? (
        <span className="pointer-events-none absolute right-1.5 top-1/2 flex -translate-y-1/2 items-center gap-0.5 rounded-md bg-muted/95 opacity-0 transition-opacity group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
          {onArchive ? (
            <Button
              size={null}
              variant={null}
              aria-label={`Archive ${sessionTitle(session)}`}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-background hover:text-foreground disabled:opacity-50"
              disabled={actionsDisabled}
              onClick={() => onArchive(session)}
              title="Archive"
              type="button"
            >
              <Archive aria-hidden="true" size={15} />
            </Button>
          ) : null}
          {onDelete ? (
            <Button
              size={null}
              variant={null}
              aria-label={`Delete ${sessionTitle(session)}`}
              className="rounded-md p-1.5 text-muted-foreground hover:bg-background hover:text-red-700 disabled:opacity-50"
              disabled={actionsDisabled}
              onClick={() => onDelete(session)}
              title="Delete"
              type="button"
            >
              <Trash2 aria-hidden="true" size={15} />
            </Button>
          ) : null}
        </span>
      ) : null}
    </li>
  )
}
