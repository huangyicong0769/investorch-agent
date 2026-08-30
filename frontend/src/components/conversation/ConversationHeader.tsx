import type { SessionRecord } from '../../api/types'
import { sessionTitle, shortSessionId } from '../../lib/session'
import { useWebSocketStatus } from '../../websocket/LiveWebSocketProvider'
import { SessionMenu } from './SessionMenu'

interface ConversationHeaderProps {
  session: SessionRecord
}

export function ConversationHeader({ session }: ConversationHeaderProps) {
  const archived = session.archived_at !== null
  const connectionStatus = useWebSocketStatus()

  return (
    <header className="flex min-h-16 items-center justify-between gap-4 border-b border-border px-6">
      <div className="min-w-0">
        <h1 className="truncate text-sm font-semibold">{sessionTitle(session)}</h1>
        <div className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
          {session.branch_from_session_id ? (
            <span title={session.branch_from_session_id}>Forked from {shortSessionId(session.branch_from_session_id)}</span>
          ) : null}
          {archived ? <span className="rounded-full border border-border px-2 py-0.5">Archived</span> : null}
          {connectionStatus !== 'connected' ? (
            <span className="text-[11px]" role="status">
              {connectionStatus === 'reconnecting' ? 'Reconnecting…' : 'Disconnected'}
            </span>
          ) : null}
        </div>
      </div>
      {archived ? null : <SessionMenu session={session} />}
    </header>
  )
}
