import { useEffect, useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ListTree, X } from 'lucide-react'

import { processesQueryOptions } from '../../api/queries'
import type { BackgroundJob } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { shortSessionId } from '../../lib/session'

function formatTimestamp(value: string | null): string {
  if (value === null) {
    return '—'
  }
  const timestamp = new Date(value)
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString()
}

function ProcessCard({ process }: { process: BackgroundJob }) {
  return (
    <li className="rounded-xl border border-border p-3">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          {process.status}
        </span>
        <span className="text-xs text-muted-foreground">PID {process.pid}</span>
      </div>
      <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted px-3 py-2 font-mono text-xs leading-5">
        {process.command}
      </pre>
      <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
        <dt className="text-muted-foreground">Owner</dt>
        <dd className="min-w-0 break-words" title={process.owner_session_id}>
          {shortSessionId(process.owner_session_id)} · {process.owner_run_id}
        </dd>
        <dt className="text-muted-foreground">Started</dt>
        <dd>{formatTimestamp(process.started_at)}</dd>
        {process.finished_at !== null ? (
          <>
            <dt className="text-muted-foreground">Finished</dt>
            <dd>{formatTimestamp(process.finished_at)}</dd>
          </>
        ) : null}
        {process.exit_code !== null ? (
          <>
            <dt className="text-muted-foreground">Exit code</dt>
            <dd>{process.exit_code}</dd>
          </>
        ) : null}
      </dl>
      <details className="mt-3 text-xs text-muted-foreground">
        <summary className="cursor-pointer">Log paths</summary>
        <dl className="mt-2 grid gap-2">
          <div>
            <dt>stdout</dt>
            <dd className="break-all text-foreground">{process.stdout_log}</dd>
          </div>
          <div>
            <dt>stderr</dt>
            <dd className="break-all text-foreground">{process.stderr_log}</dd>
          </div>
        </dl>
      </details>
    </li>
  )
}

export function ProcessesSheet() {
  const titleId = useId()
  const [open, setOpen] = useState(false)
  const processesQuery = useQuery({
    ...processesQueryOptions(),
    enabled: open,
    refetchInterval: open ? 2_000 : false,
    staleTime: 0,
  })

  useEffect(() => {
    if (!open) {
      return
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [open])

  return (
    <>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-muted"
        onClick={() => setOpen(true)}
        type="button"
      >
        <ListTree aria-hidden="true" size={16} />
        Processes
      </button>

      {open ? (
        <div
          aria-labelledby={titleId}
          aria-modal="true"
          className="fixed inset-0 z-40 flex justify-end bg-black/25"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              setOpen(false)
            }
          }}
          role="dialog"
        >
          <section className="flex h-full w-full max-w-md flex-col border-l border-border bg-card shadow-xl">
            <header className="flex items-center justify-between border-b border-border px-5 py-4">
              <div>
                <h2 className="text-base font-semibold" id={titleId}>
                  Processes
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">Background jobs are read-only in WebUI 0.1.</p>
              </div>
              <button
                aria-label="Close processes"
                className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
                onClick={() => setOpen(false)}
                type="button"
              >
                <X aria-hidden="true" size={18} />
              </button>
            </header>

            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              {processesQuery.isPending ? (
                <p className="text-sm text-muted-foreground" role="status">
                  Loading processes…
                </p>
              ) : null}
              {processesQuery.isError ? (
                <div className="text-sm" role="alert">
                  <p className="text-red-700">
                    {errorMessage(processesQuery.error, 'Processes could not be loaded.')}
                  </p>
                  <button className="mt-2 underline" onClick={() => void processesQuery.refetch()} type="button">
                    Retry
                  </button>
                </div>
              ) : null}
              {processesQuery.isSuccess && processesQuery.data.processes.length === 0 ? (
                <p className="text-sm text-muted-foreground">No background processes.</p>
              ) : null}
              {processesQuery.data?.processes.length ? (
                <ul className="space-y-3">
                  {processesQuery.data.processes.map((process) => (
                    <ProcessCard key={process.job_id} process={process} />
                  ))}
                </ul>
              ) : null}
            </div>
          </section>
        </div>
      ) : null}
    </>
  )
}
