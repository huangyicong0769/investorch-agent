import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { ArrowUpRight, Bot, BriefcaseBusiness, ChevronRight, ListChecks } from 'lucide-react'
import { Link } from 'react-router-dom'

import { processesQueryOptions, sessionContextQueryOptions, sessionRelatedPortfoliosQueryOptions } from '../../api/queries'
import type { RuntimeSnapshot, SessionContextStatus, SessionRecord } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { portfolioPath } from '../../lib/portfolio'
import { sessionPath, sessionTitle } from '../../lib/session'
import { SessionProcesses } from '../processes/SessionProcesses'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const labels: Record<SessionContextStatus, string> = {
  running: 'Running', waiting_approval: 'Approval', stopping: 'Stopping', pending: 'Pending',
  idle: 'Idle', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled',
}

export function SessionContextCard({ session, runtime }: { session: SessionRecord; runtime: RuntimeSnapshot }) {
  const [open, setOpen] = useState(() => window.matchMedia('(min-width: 1280px)').matches)
  const context = useQuery(sessionContextQueryOptions(session.session_id))
  const portfolios = useQuery(sessionRelatedPortfoliosQueryOptions(session.session_id))
  const processes = useQuery({ ...processesQueryOptions(), enabled: open, refetchInterval: open ? 2_000 : false, staleTime: 0 })
  const ownedProcesses = processes.data?.processes.filter((process) => process.owner_session_id === session.session_id) ?? []
  const outcome = context.data?.status
  const status = runtime.run_phase ?? (runtime.queued_count || runtime.pending_steer_count ? 'pending' :
    outcome === 'completed' || outcome === 'failed' || outcome === 'cancelled' || outcome === 'pending' ? outcome : 'idle')
  const children = context.data?.children ?? []
  const activeChildren = children.filter((child) => ['running', 'waiting_approval', 'stopping', 'pending'].includes(child.status)).length
  const completedChildren = children.filter((child) => child.status === 'completed').length

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          className="rounded-xl text-muted-foreground data-[state=open]:bg-muted data-[state=open]:text-foreground"
          size="icon-sm"
          variant="ghost"
          aria-label="Toggle pinned summary"
          title="Toggle pinned summary"
        >
          <ListChecks size={18} aria-hidden="true" />
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        side="bottom"
        sideOffset={16}
        collisionPadding={12}
        aria-label="Session summary"
        className="session-summary w-80 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-2xl border-border/60 bg-card p-5 text-sm shadow-[0_8px_30px_-12px_rgba(0,0,0,0.22)]"
        onOpenAutoFocus={(event) => event.preventDefault()}
        onInteractOutside={(event) => event.preventDefault()}
      >
        <div className="space-y-2 pb-4">
          <p className="truncate text-sm font-medium text-muted-foreground" title={sessionTitle(session)}>{sessionTitle(session)}</p>
          <p className="flex items-center gap-2 text-xs text-muted-foreground">
            <span aria-hidden="true" className={`size-1.5 rounded-full ${status === 'running' || status === 'pending' ? 'bg-primary' : status === 'failed' ? 'bg-destructive' : 'bg-muted-foreground/60'}`} />
            {session.archived_at ? 'Archived · ' : ''}{labels[status]}
          </p>
          {session.spawned_by_session_id ? (
            <Link className="block truncate text-xs text-muted-foreground hover:text-foreground" to={sessionPath(session.spawned_by_session_id)} onClick={() => setOpen(false)}>
              Parent · {context.data?.parent ? sessionTitle(context.data.parent) : session.spawned_by_session_id.slice(0, 8)}
            </Link>
          ) : null}
          {session.branch_from_session_id ? (
            <Link className="block truncate text-xs text-muted-foreground hover:text-foreground" to={sessionPath(session.branch_from_session_id)} onClick={() => setOpen(false)}>
              Fork source · {context.data?.fork_source ? sessionTitle(context.data.fork_source) : session.branch_from_session_id.slice(0, 8)}
            </Link>
          ) : null}
        </div>
        {portfolios.data?.portfolios.length ? (
          <section className="border-t border-border/50 py-4">
            <h2 className="mb-2 text-xs text-muted-foreground">Related Portfolios</h2>
            <ul className="space-y-1">
              {portfolios.data.portfolios.map((portfolio) => (
                <li key={portfolio.portfolio_id}>
                  <Link className="flex items-center gap-2.5 rounded-md py-1.5 hover:text-primary focus-visible:outline-ring" to={portfolioPath(portfolio.portfolio_id)} onClick={() => setOpen(false)}>
                    <BriefcaseBusiness className="size-4 shrink-0" aria-hidden="true" />
                    <span className="min-w-0 flex-1 truncate" title={portfolio.name}>{portfolio.name}</span>
                    <span className="text-xs text-muted-foreground">{portfolio.status}</span>
                    <ArrowUpRight aria-hidden="true" size={14} />
                  </Link>
                </li>
              ))}
            </ul>
          </section>
        ) : null}
        {children.length ? (
          <section className="border-t border-border/50 py-4">
            <h2 className="mb-2 text-xs text-muted-foreground">Subagents</h2>
            <Collapsible key={session.session_id}>
              <CollapsibleTrigger className="group flex w-full items-center gap-2.5 rounded-md py-1.5 text-left hover:text-primary focus-visible:outline-ring">
                <Bot className="size-4 shrink-0" aria-hidden="true" />
                <span className="flex-1">{children.length} {children.length === 1 ? 'subagent' : 'subagents'}</span>
                <span className="text-xs text-muted-foreground">{activeChildren ? `${activeChildren} active` : completedChildren ? `${completedChildren} completed` : 'View details'}</span>
                <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" aria-hidden="true" />
              </CollapsibleTrigger>
              <CollapsibleContent className="summary-section-content">
                <ul className="space-y-1 pt-2">
                  {children.map((child) => (
                    <li key={child.session.session_id}>
                      <Link className="flex items-center gap-2 rounded-md py-1.5 text-xs hover:text-primary focus-visible:outline-ring" to={sessionPath(child.session.session_id)} onClick={() => setOpen(false)}>
                        <span className="min-w-0 flex-1 truncate" title={sessionTitle(child.session)}>{sessionTitle(child.session)}</span>
                        <span className="shrink-0 text-muted-foreground">{child.session.archived_at ? 'Archived · ' : ''}{labels[child.status]}</span>
                        <ArrowUpRight aria-hidden="true" size={12} />
                      </Link>
                    </li>
                  ))}
                </ul>
              </CollapsibleContent>
            </Collapsible>
          </section>
        ) : null}
        <SessionProcesses key={session.session_id} processes={ownedProcesses} loading={processes.isPending} error={processes.error} onRetry={() => void processes.refetch()} />
        {context.isPending ? <p className="pt-3 text-xs text-muted-foreground" role="status">Loading summary…</p> : null}
        {context.error || context.data?.errors.length ? (
          <div className="border-t border-border/50 pt-4 text-xs" role="alert">
            <p className="text-muted-foreground">{errorMessage(context.error, 'Some Session history is unavailable.')}</p>
            <Button size="xs" variant="ghost" onClick={() => void context.refetch()}>Retry context</Button>
          </div>
        ) : null}
        {portfolios.error ? (
          <div className="border-t border-border/50 pt-4 text-xs" role="alert">
            <p>{errorMessage(portfolios.error, 'Related Portfolios unavailable.')}</p>
            <Button size="xs" variant="ghost" onClick={() => void portfolios.refetch()}>Retry Portfolios</Button>
          </div>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
