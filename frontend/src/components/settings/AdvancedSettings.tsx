import { useState, type ReactNode } from 'react'
import { Activity, Calculator, ChevronDown, FileSearch, FolderOpen, Globe, ListChecks, ScrollText, Terminal, type LucideIcon } from 'lucide-react'

import type { ConfigSchema } from '../../api/management'
import type { JsonObject, JsonValue } from '../../api/types'
import { cn } from '../../lib/utils'
import { fieldLabel, objectValue, resolveSchema, schemaAtPath } from '../management/schema'
import { Badge } from '../ui/badge'
import { Button } from '../ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'

interface SectionInfo {
  path: string
  title: string
  description: string
  icon: LucideIcon
  previews: { key: string; label: string; suffix?: string }[]
  omit?: string[]
  expanded?: boolean
}

const groups: { title: string; description: string; sections: SectionInfo[] }[] = [
  {
    title: 'Run limits & tools',
    description: 'Control how much work an Agent can do and the limits of its built-in tools.',
    sections: [
      { path: 'runtime', title: 'Agent capacity', description: 'Turn and concurrency limits for Agent runs.', icon: ListChecks, previews: [], omit: ['default_timezone'], expanded: true },
      { path: 'execution', title: 'Shell commands', description: 'Command timeouts and the folder used for background job output.', icon: Terminal, previews: [], expanded: true },
      { path: 'explore', title: 'File reading & search', description: 'Bound the amount of file content and search results returned to the Agent.', icon: FileSearch, previews: [{ key: 'max_read_chars', label: 'Read limit', suffix: ' characters' }, { key: 'max_search_results', label: 'Search results' }] },
      { path: 'calculate', title: 'Calculation limits', description: 'Keep expression size and numerical complexity within the configured limits.', icon: Calculator, previews: [{ key: 'max_expression_chars', label: 'Expression', suffix: ' characters' }, { key: 'max_nodes', label: 'Max nodes' }] },
    ],
  },
  {
    title: 'Interfaces',
    description: 'Tune display and connection behavior for the Web and terminal clients.',
    sections: [
      { path: 'web', title: 'Web interface', description: 'Message history, reconnect timing, input size and local server settings.', icon: Globe, previews: [{ key: 'history_page_size', label: 'History page', suffix: ' messages' }, { key: 'default_port', label: 'Server port' }], omit: ['theme_color', 'theme_mode'] },
      { path: 'tui', title: 'Terminal interface', description: 'Sidebar width, panel heights, spacing and refresh timing in the terminal app.', icon: Terminal, previews: [{ key: 'sidebar_width', label: 'Sidebar', suffix: ' columns' }, { key: 'composer_height', label: 'Input', suffix: ' rows' }] },
    ],
  },
  {
    title: 'Diagnostics',
    description: 'Choose what the application records when you need to investigate a problem.',
    sections: [
      { path: 'logging', title: 'Application logs', description: 'Log detail and file rotation for local application logs.', icon: ScrollText, previews: [], expanded: true },
      { path: 'observability', title: 'Agent tracing', description: 'Enable or disable the Agent SDK’s tracing integration.', icon: Activity, previews: [], expanded: true },
    ],
  },
]

interface Props {
  schema: ConfigSchema
  savedValues: JsonObject
  value: (path: string) => JsonValue | undefined
  renderFields: (path: string, omit?: string[], singleColumn?: boolean) => ReactNode
}

function AdvancedSection({ info, schema, value, renderFields }: Omit<Props, 'savedValues'> & { info: SectionInfo }) {
  const [open, setOpen] = useState(false)
  const Icon = info.icon
  const count = Object.keys(resolveSchema(schemaAtPath(schema, info.path), schema).properties ?? {}).filter((key) => !info.omit?.includes(key)).length
  const fields = <div className="border-t border-border px-5 py-5">{renderFields(info.path, info.omit, info.expanded)}</div>

  return <Collapsible className={cn('min-w-0 overflow-hidden rounded-xl border border-border bg-card', open && 'xl:col-span-2')} onOpenChange={setOpen} open={open}>
    <div className="space-y-4 p-5">
      <div className="flex items-start gap-3">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><Icon aria-hidden="true" className="size-4" /></span>
        <div className="min-w-0"><h4 className="text-sm font-semibold">{info.title}</h4><p className="mt-1 text-xs leading-5 text-muted-foreground">{info.description}</p></div>
      </div>
      {!info.expanded ? <>
        <dl className="grid grid-cols-2 gap-3 rounded-lg bg-muted/50 px-3 py-3">
          {info.previews.map(({ key, label, suffix }) => <div className="min-w-0" key={key}><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-1 break-words text-xs font-medium tabular-nums">{String(value(`${info.path}.${key}`) ?? 'Not set')}{suffix}</dd></div>)}
        </dl>
        <CollapsibleTrigger asChild><Button aria-label={`${open ? 'Hide' : 'Edit'} ${info.title} settings`} className="w-full justify-between" size="sm" type="button" variant="outline"><span>{open ? 'Hide settings' : `Edit ${count} settings`}</span><ChevronDown aria-hidden="true" className={cn('size-4 transition-transform', open && 'rotate-180')} /></Button></CollapsibleTrigger>
      </> : null}
    </div>
    {info.expanded ? fields : <CollapsibleContent>{fields}</CollapsibleContent>}
  </Collapsible>
}

function StorageSettings({ values }: { values: JsonObject }) {
  const paths = objectValue(values.paths)
  const names: Record<string, { title: string; description: string }> = {
    root: { title: 'Workspace root', description: 'Base directory for this InvestOrch instance' },
    workspace: { title: 'Working files', description: 'Files the Agent can work with, relative to the root' },
    state: { title: 'Application state', description: 'Stored application data, relative to the root' },
  }
  return <section className="min-w-0 rounded-xl border border-border bg-card">
    <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-5">
      <div className="flex items-start gap-3"><span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground"><FolderOpen aria-hidden="true" className="size-4" /></span><div><h3 className="text-sm font-semibold">Data directories</h3><p className="mt-1 text-xs leading-5 text-muted-foreground">Where this workspace stores its files and application data.</p></div></div>
      <Badge variant="secondary">Read only</Badge>
    </div>
    <dl className="divide-y divide-border px-5">
      {Object.entries(paths).map(([key, directory]) => <div className="grid min-w-0 gap-3 py-4 sm:grid-cols-[11rem_minmax(0,1fr)]" key={key}>
        <dt><span className="block text-sm font-medium">{names[key]?.title ?? fieldLabel(key)}</span>{names[key] ? <span className="mt-1 block text-xs leading-5 text-muted-foreground">{names[key].description}</span> : null}</dt>
        <dd className="min-w-0 self-start break-all rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-6">{String(directory)}</dd>
      </div>)}
    </dl>
    <details className="group border-t border-border px-5">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-3 rounded py-4 text-sm font-medium outline-none focus-visible:ring-2 focus-visible:ring-ring [&::-webkit-details-marker]:hidden">Move this workspace to another location<ChevronDown aria-hidden="true" className="size-4 shrink-0 transition-transform group-open:rotate-180" /></summary>
      <div className="space-y-3 pb-5 text-sm leading-6 text-muted-foreground">
        <p>Directory changes require an offline migration. Updating a path does not move existing files.</p>
        <ol className="list-decimal space-y-3 pl-5 [&_code]:break-all [&_code]:text-xs">
          <li>Stop all Web, terminal and console clients. Create a backup outside the data root with <code>investorch backup create --output /safe/path/backup.zip</code>, then run <code>investorch backup verify /safe/path/backup.zip</code>.</li>
          <li>Copy the state and working files to the intended locations, retaining the originals. Update <code>paths.state</code> and <code>paths.workspace</code> in <code>investorch.toml</code> while the application is stopped. The root is selected at startup.</li>
          <li>Run <code>investorch doctor --root /path/to/root</code> before restarting. Verify your Sessions, Portfolios and Skills before removing the originals.</li>
        </ol>
      </div>
    </details>
  </section>
}

export function AdvancedSettings({ schema, savedValues, value, renderFields }: Props) {
  const shown = new Set(['paths', 'models', 'secrets', 'permission', 'interaction', 'compaction', 'activity', 'images', 'backtest', 'mcp', 'cnequity', ...groups.flatMap((group) => group.sections.map(({ path }) => path))])
  const additional = Object.keys(schema.properties ?? {}).filter((path) => !shown.has(path))
  return <div className="space-y-8">
    {groups.map((group) => <section className="space-y-4" key={group.title}>
      <div><h3 className="text-sm font-semibold">{group.title}</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">{group.description}</p></div>
      <div className="grid items-start gap-4 xl:grid-cols-2">{group.sections.filter(({ path }) => path in (schema.properties ?? {})).map((info) => <AdvancedSection info={info} key={info.path} renderFields={renderFields} schema={schema} value={value} />)}</div>
    </section>)}
    <section className="space-y-4"><div><h3 className="text-sm font-semibold">Storage</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">Inspect the saved directory layout and find migration instructions.</p></div><StorageSettings values={savedValues} /></section>
    {additional.map((path) => <section className="space-y-4 rounded-xl border border-border bg-card p-5" key={path}><h3 className="text-sm font-semibold">{fieldLabel(path)}</h3>{renderFields(path)}</section>)}
  </div>
}
