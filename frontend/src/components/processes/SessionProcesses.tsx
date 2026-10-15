import { ChevronDown, Terminal } from 'lucide-react'

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
        <CollapsibleContent className="summary-section-content space-y-3 px-1.5 pb-3 text-xs">
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
  const running = processes.filter((process) => process.status === 'running').length
  const lost = processes.filter((process) => process.status === 'lost').length
  const status = running ? `${running} running` : lost ? `${lost} lost` : 'Finished'

  return (
    <section className="border-t border-border/50 pt-4" aria-label="Session processes">
      <h2 className="mb-2 text-xs text-muted-foreground">Background processes</h2>
      {loading ? <p className="text-xs text-muted-foreground" role="status">Loading processes…</p> : null}
      {error ? <div className="text-xs" role="alert">
        <p className="text-destructive">{errorMessage(error, 'Processes could not be loaded.')}</p>
        <Button type="button" size="xs" variant="ghost" onClick={onRetry}>Retry processes</Button>
      </div> : null}
      {!loading && !error && processes.length === 0 ? <p className="text-xs text-muted-foreground">No background processes.</p> : null}
      {processes.length ? <Collapsible>
        <CollapsibleTrigger className="group flex w-full items-center gap-2.5 rounded-md py-1.5 text-left hover:text-primary focus-visible:outline-ring">
          <Terminal className="size-4 shrink-0" aria-hidden="true" />
          <span className="flex-1">{processes.length} {processes.length === 1 ? 'process' : 'processes'}</span>
          <span className="text-xs text-muted-foreground">{status}</span>
          <ChevronDown className="size-3.5 -rotate-90 text-muted-foreground transition-transform group-data-[state=open]:rotate-0" aria-hidden="true" />
        </CollapsibleTrigger>
        <CollapsibleContent className="summary-section-content">
          <ul className="divide-y divide-border/50 pt-2">
            {processes.map((process) => <ProcessItem key={process.job_id} process={process} />)}
          </ul>
        </CollapsibleContent>
      </Collapsible> : null}
    </section>
  )
}
