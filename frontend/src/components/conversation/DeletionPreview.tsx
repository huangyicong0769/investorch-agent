import { Link } from 'react-router-dom'
import type { SessionDeletionPreview } from '../../api/types'
import { sessionPath } from '../../lib/session'
import { Button } from '@/components/ui/button'

export function DeletionPreview({ preview, pending, error, onRetry, onNavigate }: {
  preview?: SessionDeletionPreview; pending: boolean; error: unknown; onRetry: () => void; onNavigate?: () => void
}) {
  return <div className="mt-3 text-xs">
    {pending ? <p role="status">Loading deletion scope…</p> : null}
    {error ? <div role="alert"><p>Deletion preview unavailable.</p><Button size="xs" variant="ghost" onClick={onRetry}>Reload preview</Button></div> : null}
    {preview ? <>
      <Button size="xs" variant="ghost" onClick={onRetry}>Refresh preview</Button>
      <p className="text-muted-foreground">{preview.sessions.length} {preview.sessions.length === 1 ? 'Session' : 'Sessions'}, including {preview.sessions.length - 1} spawned descendants. This cannot be undone.</p>
      <ul className="mt-2 max-h-40 space-y-1 overflow-y-auto break-words rounded-md border border-border p-2">
        {preview.sessions.map((session) => <li key={session.session_id}>{session.title?.trim() || session.session_id.slice(0, 8)}</li>)}
      </ul>
      {preview.blocked.map((blocked) => <p key={`${blocked.session_id}:${blocked.code}`} className="mt-2 break-words text-destructive">
        <Link className="underline" to={sessionPath(blocked.session_id)} onClick={onNavigate}>Open {preview.sessions.find((s) => s.session_id === blocked.session_id)?.title || blocked.session_id.slice(0, 8)}</Link>
        {' · '}{blocked.code === 'fork_referenced' ? 'Delete this independent Fork first.' : blocked.code === 'journal_delete_unsafe' ? 'History cannot be safely deleted. Check System Status.' : 'Stop its Run and resolve pending inputs before retrying.'}
      </p>)}
    </> : null}
  </div>
}
