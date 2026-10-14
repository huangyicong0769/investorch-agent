import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowUpRight, PanelsTopLeft } from 'lucide-react'
import { Link } from 'react-router-dom'

import { sessionContextQueryOptions, sessionRelatedPortfoliosQueryOptions } from '../../api/queries'
import type { RuntimeSnapshot, SessionContextStatus, SessionRecord } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { portfolioPath } from '../../lib/portfolio'
import { sessionPath, sessionTitle } from '../../lib/session'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

const labels: Record<SessionContextStatus, string> = {
  running: 'Running', waiting_approval: 'Approval', stopping: 'Stopping', pending: 'Pending',
  idle: 'Idle', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled',
}

export function SessionContextCard({ session, runtime }: { session: SessionRecord; runtime: RuntimeSnapshot }) {
  const [open, setOpen] = useState(false)
  const context = useQuery(sessionContextQueryOptions(session.session_id))
  const portfolios = useQuery(sessionRelatedPortfoliosQueryOptions(session.session_id))
  const outcome = context.data?.status
  const status = runtime.run_phase ?? (runtime.queued_count || runtime.pending_steer_count ? 'pending' :
    outcome === 'completed' || outcome === 'failed' || outcome === 'cancelled' || outcome === 'pending' ? outcome : 'idle')
  const content = (
    <div className="max-h-[calc(100dvh-10rem)] overflow-y-auto rounded-xl border border-border bg-card text-xs shadow-sm">
      <div className="space-y-1 p-3">
        <p className="truncate text-sm font-semibold" title={sessionTitle(session)}>{sessionTitle(session)}</p>
        <p className="text-muted-foreground">{session.archived_at ? 'Archived · ' : ''}{labels[status]}</p>
        {session.spawned_by_session_id ? (
          <Link className="block truncate text-muted-foreground hover:text-foreground" to={sessionPath(session.spawned_by_session_id)} onClick={() => setOpen(false)}>
            Parent · {context.data?.parent ? sessionTitle(context.data.parent) : session.spawned_by_session_id.slice(0, 8)}
          </Link>
        ) : null}
        {session.branch_from_session_id ? (
          <Link className="block truncate text-muted-foreground hover:text-foreground" to={sessionPath(session.branch_from_session_id)} onClick={() => setOpen(false)}>
            Fork source · {context.data?.fork_source ? sessionTitle(context.data.fork_source) : session.branch_from_session_id.slice(0, 8)}
          </Link>
        ) : null}
      </div>
      {portfolios.data?.portfolios.length ? (
        <div className="border-t border-border p-3">
          <h2 className="mb-2 font-medium text-muted-foreground">Related Portfolios</h2>
          <ul className="space-y-1">
            {portfolios.data.portfolios.map((portfolio) => (
              <li key={portfolio.portfolio_id}>
                <Link className="flex items-center gap-2 rounded-md p-1.5 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" to={portfolioPath(portfolio.portfolio_id)} onClick={() => setOpen(false)}>
                  <span className="min-w-0 flex-1 truncate" title={portfolio.name}>{portfolio.name}</span>
                  <span className="text-[10px] text-muted-foreground">{portfolio.status}</span>
                  <ArrowUpRight aria-hidden="true" size={12} />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {context.data?.children.length ? (
        <div className="border-t border-border p-3">
          <h2 className="mb-2 font-medium text-muted-foreground">Subagents <span className="tabular-nums">{context.data.children.length}</span></h2>
          <ul className="space-y-1">
            {context.data.children.map((child) => (
              <li key={child.session.session_id}>
                <Link className="flex items-center gap-2 rounded-md p-1.5 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" to={sessionPath(child.session.session_id)} onClick={() => setOpen(false)}>
                  <span className="min-w-0 flex-1 truncate" title={sessionTitle(child.session)}>{sessionTitle(child.session)}</span>
                  <span className="shrink-0 text-[10px] text-muted-foreground">{child.session.archived_at ? 'Archived · ' : ''}{labels[child.status]}</span>
                  <ArrowUpRight aria-hidden="true" size={12} />
                </Link>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {context.isPending ? <p className="px-3 pb-3 text-muted-foreground" role="status">Loading context…</p> : null}
      {context.error || context.data?.errors.length ? (
        <div className="border-t border-border p-3" role="alert">
          <p className="text-muted-foreground">{errorMessage(context.error, 'Some Session history is unavailable.')}</p>
          <Button size="xs" variant="ghost" onClick={() => void context.refetch()}>Retry context</Button>
        </div>
      ) : null}
      {portfolios.error ? (
        <div className="border-t border-border p-3" role="alert">
          <p>{errorMessage(portfolios.error, 'Related Portfolios unavailable.')}</p>
          <Button size="xs" variant="ghost" onClick={() => void portfolios.refetch()}>Retry Portfolios</Button>
        </div>
      ) : null}
    </div>
  )
  return <>
    <aside aria-label="Session context" className="absolute right-4 top-20 hidden w-72 xl:block">{content}</aside>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="xl:hidden" size="sm" variant="ghost" aria-label="Open Session context"><PanelsTopLeft size={16} aria-hidden="true" />Context</Button>
      </DialogTrigger>
      <DialogContent className="max-h-[90dvh] max-w-sm gap-3 p-4" aria-describedby={undefined}>
        <DialogTitle className="text-sm">Session context</DialogTitle>
        {content}
      </DialogContent>
    </Dialog>
  </>
}
