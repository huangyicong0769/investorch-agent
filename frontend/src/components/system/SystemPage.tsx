import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { Link } from 'react-router-dom'

import { systemQueryOptions, type DiagnosticCheck } from '../../api/management'
import { processesQueryOptions } from '../../api/queries'
import { ErrorNotice } from '../management/Feedback'
import { SessionProcesses } from '../processes/SessionProcesses'
import { Button } from '@/components/ui/button'
import { cn } from '../../lib/utils'
import { fieldLabel } from '../management/schema'

const statusOrder: Record<DiagnosticCheck['status'], number> = { error: 0, warning: 1, unknown: 2, ok: 3, disabled: 4 }
const statusLabels: Record<DiagnosticCheck['status'], string> = { error: 'Errors', warning: 'Warnings', unknown: 'Not checked', ok: 'Passed', disabled: 'Disabled' }

type SkillDiagnosticCheck = DiagnosticCheck & {
  invalid?: string[]
  missing?: string[]
  builtin_drift?: string[]
}

function SkillIssues({ check }: { check: SkillDiagnosticCheck }) {
  if (check.code !== 'skills') return null
  const groups = [
    { label: 'Invalid Skills', names: check.invalid },
    { label: 'Missing Skills', names: check.missing },
    { label: 'Changed built-ins', names: check.builtin_drift },
  ].filter((group) => group.names?.length)
  if (!groups.length) return null
  return <dl className="mt-3 space-y-3 text-sm">{groups.map(({ label, names }) => <div key={label}>
    <dt className="font-medium">{label}</dt>
    <dd className="mt-1"><ul aria-label={label} className="flex flex-wrap gap-1.5">{names?.map((name) => <li className="max-w-full break-all rounded bg-muted/60 px-2 py-1 text-xs" key={name}>{name}</li>)}</ul></dd>
  </div>)}</dl>
}

export function SystemPage({ standalone = false }: { standalone?: boolean }) {
  const query = useQuery(systemQueryOptions())
  const processes = useQuery({ ...processesQueryOptions(), enabled: !standalone, refetchInterval: standalone ? false : 2_000, staleTime: 0 })
  const checkedAt = query.data?.checks[0]?.checked_at
  const checks = [...(query.data?.checks ?? [])].sort((left, right) => statusOrder[left.status] - statusOrder[right.status])
  return <div className="mx-auto w-full max-w-5xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
    {standalone ? <Link className="mb-6 inline-block text-sm underline" to="/setup">Back to setup and recovery</Link> : null}
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">System Status</h1>
        <p className="mt-2 text-sm text-muted-foreground">Local checks and repair guidance. Refresh to inspect the current files.</p>
        {checkedAt ? <p className="mt-1 text-xs text-muted-foreground">Checked {new Date(checkedAt).toLocaleString()}</p> : null}
      </div>
      <Button disabled={query.isFetching} onClick={() => void query.refetch()} type="button" variant="outline"><RefreshCw aria-hidden="true" className={query.isFetching ? 'animate-spin' : ''} />{query.isFetching ? 'Checking…' : 'Refresh'}</Button>
    </header>
    {!standalone ? <div className="mt-6">
      <SessionProcesses scope="all" processes={processes.data?.processes ?? []} loading={processes.isPending} error={processes.error} onRetry={() => void processes.refetch()} />
    </div> : null}
    {query.data ? <div aria-label="Diagnostic summary" className="mt-6 flex flex-wrap gap-x-5 gap-y-2 rounded-xl border border-border bg-card px-4 py-3 text-sm" role="status">
      {(Object.keys(statusOrder) as DiagnosticCheck['status'][]).map((status) => {
        const count = checks.filter((check) => check.status === status).length
        return count ? <span className={cn('tabular-nums', status === 'error' ? 'text-destructive' : status === 'warning' ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground')} key={status}><strong className="font-semibold">{count}</strong> {statusLabels[status]}</span> : null
      })}
      {!checks.length ? <span className="text-muted-foreground">No diagnostics returned.</span> : null}
    </div> : null}
    <div className="mt-6 space-y-3">
      <ErrorNotice error={query.error} fallback="Local diagnostics could not be loaded." />
      {query.isPending ? <p className="text-sm text-muted-foreground" role="status">Inspecting local state…</p> : null}
      {checks.map((check) => <section className="rounded-xl border border-border bg-card p-4 sm:p-5" key={check.code}>
        <p className="mb-1 text-xs text-muted-foreground">{fieldLabel(check.code)}</p>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="min-w-0 break-words text-sm font-semibold">{check.summary}</h2>
          <span className={cn('rounded-md px-2 py-1 text-[11px] font-semibold uppercase', check.status === 'error' ? 'bg-destructive/10 text-destructive' : check.status === 'warning' ? 'bg-amber-500/10 text-amber-700 dark:text-amber-400' : 'bg-muted text-muted-foreground')}>{check.status}</span>
        </div>
        {check.detail ? <p className="mt-2 break-words text-sm text-muted-foreground">{check.detail}</p> : null}
        <SkillIssues check={check} />
        {check.remediation ? <p className="mt-3 break-words rounded-lg bg-muted/60 p-3 text-sm">{check.remediation}</p> : null}
      </section>)}
    </div>
    <p className="mt-6 text-xs text-muted-foreground">These checks do not connect to model providers, download data, initialize databases, or apply repairs. Offline CLI: <code>investorch doctor</code> and <code>investorch upgrade check</code>.</p>
  </div>
}
