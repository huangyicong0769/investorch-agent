import { useQuery } from '@tanstack/react-query'
import { AlertCircle, Check, ChevronRight, CircleDashed, RefreshCw, TriangleAlert } from 'lucide-react'
import { Link } from 'react-router-dom'

import { systemQueryOptions, type DiagnosticCheck } from '../../api/management'
import { processesQueryOptions } from '../../api/queries'
import { ErrorNotice } from '../management/Feedback'
import { SessionProcesses } from '../processes/SessionProcesses'
import { Button } from '@/components/ui/button'
import { cn } from '../../lib/utils'
import { fieldLabel } from '../management/schema'

const checkLabels: Record<string, string> = { package: 'Application', web_assets: 'Web interface', config: 'Configuration', credentials: 'Model credentials', sessions: 'Sessions', portfolio_schema: 'Portfolio storage', skills: 'Skills', mcp: 'MCP', rqalpha: 'RQAlpha', cnequity: 'CNEquity', filesystem: 'Local folders' }
const statusLabels: Record<DiagnosticCheck['status'], string> = { error: 'Error', warning: 'Review', unknown: 'Not checked', ok: 'Passed', disabled: 'Disabled' }

type SkillDiagnosticCheck = DiagnosticCheck & {
  invalid?: string[]
  missing?: string[]
  builtin_drift?: string[]
}

function CommandText({ text }: { text: string }) {
  return text.split(/(investorch(?: (?:--update|doctor|upgrade check|migrate portfolio|web))?)/g).map((part, index) =>
    part.startsWith('investorch') ? <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground" key={index}>{part}</code> : part,
  )
}

function SkillIssues({ check, standalone }: { check: SkillDiagnosticCheck; standalone: boolean }) {
  const groups = [
    { label: 'Invalid Skills', names: check.invalid, action: 'Repair these Skills or restore a working copy.' },
    { label: 'Missing Skills', names: check.missing, action: 'Restore the missing files or reinstall the affected Skills.' },
    { label: 'Built-in Skills with local differences', names: check.builtin_drift, action: 'Review local changes before replacing them with the bundled version.' },
  ].filter((group) => group.names?.length)
  if (check.code !== 'skills' || !groups.length) return null
  return <div className="mt-4 space-y-4">{groups.map(({ label, names, action }) => <div key={label}>
    <h4 className="text-xs font-medium text-muted-foreground">{label}</h4>
    <ul aria-label={label} className="mt-2 flex flex-wrap gap-1.5">{names?.map((name) => <li className="max-w-full break-all rounded-md border border-border/70 px-2 py-1 font-mono text-xs" key={name}>{name}</li>)}</ul>
    <p className="mt-2 text-sm leading-relaxed">{action}</p>
  </div>)}
    {!standalone ? <Link className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline" to="/skills">Manage Skills <ChevronRight aria-hidden="true" className="size-3.5" /></Link> : null}
  </div>
}

function CheckItem({ check, standalone }: { check: SkillDiagnosticCheck; standalone: boolean }) {
  const needsAttention = check.status === 'error' || check.status === 'warning'
  const skillIssues = check.code === 'skills' && Boolean(check.invalid?.length || check.missing?.length || check.builtin_drift?.length)
  const Icon = check.status === 'error' ? AlertCircle : check.status === 'warning' ? TriangleAlert : check.status === 'ok' ? Check : CircleDashed
  const tone = check.status === 'error' ? 'text-destructive' : check.status === 'warning' ? 'text-amber-700 dark:text-amber-400' : 'text-muted-foreground'
  return <article className={cn('px-4 py-4 sm:px-5', needsAttention && 'rounded-xl border bg-card', check.status === 'error' ? 'border-destructive/25' : check.status === 'warning' ? 'border-amber-500/25' : '')}>
    <div className="flex items-start gap-3">
      <Icon aria-hidden="true" className={cn('mt-0.5 size-4 shrink-0', tone)} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 className="text-sm font-semibold">{checkLabels[check.code] ?? fieldLabel(check.code)}</h3>
          <span className={cn('text-xs', tone)}>{statusLabels[check.status]}</span>
        </div>
        <p className="mt-1 break-words text-sm leading-relaxed text-muted-foreground">{check.summary}</p>
        <SkillIssues check={check} standalone={standalone} />
        {needsAttention && check.remediation && !skillIssues ? <div className="mt-4 border-l-2 border-border pl-3">
          <p className="text-xs font-medium text-muted-foreground">Next step</p>
          <p className="mt-1 break-words text-sm leading-relaxed"><CommandText text={check.remediation} /></p>
        </div> : null}
        {check.detail || (check.remediation && (!needsAttention || skillIssues)) ? <details className="group mt-3 text-sm">
          <summary className="flex w-fit cursor-pointer list-none items-center gap-1 text-xs text-muted-foreground hover:text-foreground [&::-webkit-details-marker]:hidden"><ChevronRight aria-hidden="true" className="size-3 transition-transform group-open:rotate-90" />{skillIssues ? 'Update instructions and details' : 'Technical details'}</summary>
          <div className="mt-2 space-y-2 border-l border-border pl-3 text-xs leading-relaxed text-muted-foreground">
            {check.detail ? <p className="break-words">{check.detail}</p> : null}
            {check.remediation && (!needsAttention || skillIssues) ? <p className="break-words"><CommandText text={check.remediation} /></p> : null}
          </div>
        </details> : null}
      </div>
    </div>
  </article>
}

export function SystemPage({ standalone = false }: { standalone?: boolean }) {
  const query = useQuery(systemQueryOptions())
  const processes = useQuery({ ...processesQueryOptions(), enabled: !standalone, refetchInterval: standalone ? false : 2_000, staleTime: 0 })
  const checkedAt = query.data?.checks[0]?.checked_at
  const checks = query.data?.checks ?? []
  const attention = checks.filter((check) => check.status === 'error' || check.status === 'warning').sort((left, right) => Number(right.status === 'error') - Number(left.status === 'error'))
  const unchecked = checks.filter((check) => check.status === 'unknown')
  const passed = checks.filter((check) => check.status === 'ok')
  const disabled = checks.filter((check) => check.status === 'disabled')
  return <div className="mx-auto w-full max-w-4xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
    {standalone ? <Link className="mb-6 inline-block text-sm underline" to="/setup">Back to setup and recovery</Link> : null}
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">System Status</h1>
        <p className="mt-2 text-sm text-muted-foreground">Local configuration, storage, and integrations.</p>
        {checkedAt ? <p className="mt-1 text-xs text-muted-foreground">Checked {new Date(checkedAt).toLocaleString()}</p> : null}
      </div>
      <Button disabled={query.isFetching} onClick={() => void query.refetch()} type="button" variant="outline"><RefreshCw aria-hidden="true" className={query.isFetching ? 'animate-spin' : ''} />{query.isFetching ? 'Checking…' : 'Refresh'}</Button>
    </header>
    <div className="mt-8">
      <ErrorNotice error={query.error} fallback="Local diagnostics could not be loaded." />
      {query.isPending ? <p className="text-sm text-muted-foreground" role="status">Inspecting local state…</p> : null}
      {query.data ? <>
        <div aria-label="Diagnostic summary" className="mb-6 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm" role="status">
          <span className={cn('font-medium', attention.some((check) => check.status === 'error') && 'text-destructive')}>{attention.length ? `${attention.length} ${attention.length === 1 ? 'item' : 'items'} to review` : 'No issues found in completed checks'}</span>
          <span className="text-muted-foreground">{passed.length} passed{unchecked.length ? ` · ${unchecked.length} not checked` : ''}{disabled.length ? ` · ${disabled.length} disabled` : ''}</span>
        </div>
        {attention.length ? <section aria-label="Needs attention" className="space-y-3">
          <h2 className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Needs attention</h2>
          {attention.map((check) => <CheckItem check={check} key={check.code} standalone={standalone} />)}
        </section> : null}
        {unchecked.length ? <section aria-label="Not checked" className="mt-8">
          <h2 className="text-sm font-medium">Not checked <span className="ml-1 text-xs text-muted-foreground">{unchecked.length}</span></h2>
          <p className="mt-1 text-xs text-muted-foreground">These checks could not be completed. See each item for details.</p>
          <div className="mt-3 divide-y divide-border/60 rounded-xl border border-border/60">{unchecked.map((check) => <CheckItem check={check} key={check.code} standalone={standalone} />)}</div>
        </section> : null}
        {passed.length || disabled.length ? <details className="group mt-8 rounded-xl border border-border/60">
          <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-4 text-sm [&::-webkit-details-marker]:hidden"><ChevronRight aria-hidden="true" className="size-4 text-muted-foreground transition-transform group-open:rotate-90" /><Check aria-hidden="true" className="size-4 text-muted-foreground" /><span className="font-medium">Passed and disabled</span><span className="ml-auto text-xs text-muted-foreground">{passed.length + disabled.length}</span></summary>
          <div className="divide-y divide-border/60 border-t border-border/60">{[...passed, ...disabled].map((check) => <CheckItem check={check} key={check.code} standalone={standalone} />)}</div>
        </details> : null}
        {!checks.length ? <p className="text-sm text-muted-foreground">No diagnostics returned.</p> : null}
      </> : null}
    </div>
    {!standalone ? <div className="mt-8">
      <SessionProcesses scope="all" processes={processes.data?.processes ?? []} loading={processes.isPending} error={processes.error} onRetry={() => void processes.refetch()} />
    </div> : null}
    <p className="mt-6 text-xs text-muted-foreground">Checks are read-only. For offline diagnostics, run <code className="font-mono">investorch doctor</code>.</p>
  </div>
}
