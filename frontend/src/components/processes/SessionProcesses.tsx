import { ChevronDown } from 'lucide-react'

import type { BackgroundJob } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

function formatTimestamp(value: string | null): string {
  if (value === null) return '—'
  const timestamp = new Date(value)
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString()
}

function ProcessItem({ process }: { process: BackgroundJob }) {
  return (
    <li>
      <Collapsible>
        <CollapsibleTrigger asChild>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="group h-auto min-h-10 w-full justify-start gap-2 px-1.5 py-2 text-left text-xs font-normal"
          >
            <ChevronDown aria-hidden="true" className="size-3 shrink-0 -rotate-90 transition-transform group-data-[state=open]:rotate-0" />
            <span className="min-w-0 flex-1 truncate font-mono" title={process.command}>{process.command}</span>
            <Badge variant="secondary" className="px-1.5 py-0 text-[10px] capitalize">{process.status}</Badge>
          </Button>
        </CollapsibleTrigger>
        <CollapsibleContent className="space-y-3 px-1.5 pb-3">
          <pre className="max-h-28 overflow-auto whitespace-pre-wrap break-all rounded-md bg-muted p-2 font-mono text-xs leading-5">{process.command}</pre>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1">
            <dt className="text-muted-foreground">Started</dt>
            <dd className="break-words">{formatTimestamp(process.started_at)}</dd>
            {process.finished_at !== null ? <>
              <dt className="text-muted-foreground">Finished</dt>
              <dd className="break-words">{formatTimestamp(process.finished_at)}</dd>
            </> : null}
            {process.exit_code !== null ? <>
              <dt className="text-muted-foreground">Exit code</dt>
              <dd>{process.exit_code}</dd>
            </> : null}
            <dt className="text-muted-foreground">PID</dt>
            <dd>{process.pid}</dd>
          </dl>
          <dl className="space-y-2">
            <div>
              <dt className="text-muted-foreground">stdout log</dt>
              <dd className="break-all">{process.stdout_log}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">stderr log</dt>
              <dd className="break-all">{process.stderr_log}</dd>
            </div>
          </dl>
        </CollapsibleContent>
      </Collapsible>
    </li>
  )
}

interface SessionProcessesProps {
  processes: BackgroundJob[]
  loading: boolean
  error: unknown
  onRetry: () => void
}

export function SessionProcesses({ processes, loading, error, onRetry }: SessionProcessesProps) {
  return (
    <section className="border-t border-border p-3" aria-label="Session processes">
      <h2 className="mb-2 font-medium text-muted-foreground">Processes <span className="tabular-nums">{processes.length || ''}</span></h2>
      {loading ? <p className="text-muted-foreground" role="status">Loading processes…</p> : null}
      {error ? <div role="alert">
        <p className="text-destructive">{errorMessage(error, 'Processes could not be loaded.')}</p>
        <Button type="button" size="xs" variant="ghost" onClick={onRetry}>Retry processes</Button>
      </div> : null}
      {!loading && !error && processes.length === 0 ? <p className="text-muted-foreground">No background processes for this conversation.</p> : null}
      {processes.length ? <ul className="divide-y divide-border/60">
        {processes.map((process) => <ProcessItem key={process.job_id} process={process} />)}
      </ul> : null}
    </section>
  )
}
