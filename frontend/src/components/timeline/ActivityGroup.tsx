import type {
  TimelineActivityGroupViewModel,
  TimelineActivityViewModel,
  TimelineApprovalViewModel,
  TimelineToolViewModel,
  TimelineUnmatchedToolOutputViewModel,
} from '../../lib/timeline/project'
import { formatJsonValue } from '../../lib/timeline/project'
import { CopyButton } from './CopyButton'

interface ActivityGroupProps {
  group: TimelineActivityGroupViewModel
}

interface DetailProps {
  label: string
  value: string
  formatJson?: boolean
}

function Detail({ label, value, formatJson = false }: DetailProps) {
  const text = formatJson ? formatJsonValue(value) : value

  return (
    <details className="mt-2 rounded border border-border/80 bg-background/70">
      <summary className="cursor-pointer px-2 py-1 text-xs text-muted-foreground">{label}</summary>
      <div className="relative border-t border-border/80">
        <div className="flex justify-end px-2 pt-2">
          <CopyButton value={text} />
        </div>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-2 pb-2 font-mono text-xs leading-5">
          {text}
        </pre>
      </div>
    </details>
  )
}

function ToolActivity({ item }: { item: TimelineToolViewModel }) {
  return (
    <div className="rounded-md border border-border/70 px-3 py-2 text-xs">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <span className="font-medium">Tool · {item.name}</span>
        {item.label ? <span className="text-muted-foreground">{item.label}</span> : null}
      </div>
      {item.arguments !== null ? <Detail formatJson label="Arguments" value={item.arguments} /> : null}
      {item.observation !== null ? <Detail label="Observation" value={item.observation} /> : null}
    </div>
  )
}

function UnmatchedOutput({ item }: { item: TimelineUnmatchedToolOutputViewModel }) {
  return (
    <div className="rounded-md border border-border/70 px-3 py-2 text-xs">
      <span className="font-medium">Unmatched tool output</span>
      <Detail label="Observation" value={item.output} />
    </div>
  )
}

function ApprovalActivity({ item }: { item: TimelineApprovalViewModel }) {
  const status = item.approved ? 'Approved' : 'Rejected'
  const source = item.source === 'permission' ? 'permission' : 'user'

  return (
    <div className="rounded-md border border-border/70 px-3 py-2 text-xs">
      <div className="font-medium">
        {status} · {source}
      </div>
      <dl className="mt-2 grid gap-1 text-muted-foreground">
        <div className="flex gap-2">
          <dt className="shrink-0">Tool</dt>
          <dd className="min-w-0 break-words text-foreground">{item.toolName}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0">Review decision</dt>
          <dd className="min-w-0 break-words text-foreground">{item.reviewDecision ?? '—'}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0">Review reason</dt>
          <dd className="min-w-0 whitespace-pre-wrap break-words text-foreground">{item.reviewReason ?? '—'}</dd>
        </div>
      </dl>
      {item.arguments !== null ? <Detail formatJson label="Arguments" value={item.arguments} /> : null}
    </div>
  )
}

function ActivityItem({ item }: { item: TimelineActivityViewModel }) {
  if (item.type === 'reasoning') {
    return (
      <div className="rounded-md border border-border/70 px-3 py-2 text-xs">
        <div className="font-medium">Reasoning</div>
        <p className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-words text-muted-foreground">{item.text}</p>
      </div>
    )
  }
  if (item.type === 'tool') {
    return <ToolActivity item={item} />
  }
  if (item.type === 'unmatched_tool_output') {
    return <UnmatchedOutput item={item} />
  }
  return <ApprovalActivity item={item} />
}

export function ActivityGroup({ group }: ActivityGroupProps) {
  return (
    <details className="my-2 max-w-full rounded-lg border border-border/80 bg-card" data-seq={group.seq}>
      <summary className="cursor-pointer list-none px-3 py-2 text-sm text-muted-foreground marker:hidden">
        <span aria-hidden="true" className="mr-2">
          ▸
        </span>
        {group.title}
      </summary>
      <div className="space-y-2 border-t border-border/80 p-3">
        {group.items.map((item) => (
          <ActivityItem item={item} key={item.id} />
        ))}
      </div>
    </details>
  )
}
