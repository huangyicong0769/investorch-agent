import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import type {
  TimelineActivityGroupViewModel,
  TimelineActivityViewModel,
  TimelineApprovalViewModel,
  TimelineToolViewModel,
  TimelineUnmatchedToolOutputViewModel,
} from '../../lib/timeline/project'
import { formatJsonValue } from '../../lib/timeline/project'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
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
  const [open, setOpen] = useState(false)

  return (
    <Collapsible
      className="mt-2 rounded border border-border/80 bg-background/70"
      onOpenChange={setOpen}
      open={open}
    >
      <CollapsibleTrigger asChild>
        <Button
          className="flex w-full cursor-pointer items-center justify-start gap-0 px-2 py-1 text-left text-xs font-normal text-muted-foreground"
          size={null}
          type="button"
          variant={null}
        >
          {open ? (
            <ChevronDown aria-hidden="true" className="mr-2 shrink-0" size={14} />
          ) : (
            <ChevronRight aria-hidden="true" className="mr-2 shrink-0" size={14} />
          )}
          <span>{label}</span>
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="relative border-t border-border/80">
        <div className="flex justify-end px-2 pt-2">
          <CopyButton value={text} />
        </div>
        <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-2 pb-2 font-mono text-xs leading-5">
          {text}
        </pre>
      </CollapsibleContent>
    </Collapsible>
  )
}

function ToolActivity({ item }: { item: TimelineToolViewModel }) {
  return (
    <div className="text-xs">
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
    <div className="text-xs">
      <span className="font-medium">Unmatched tool output</span>
      <Detail label="Observation" value={item.output} />
    </div>
  )
}

function ApprovalActivity({ item }: { item: TimelineApprovalViewModel }) {
  const automatic = item.source === 'permission'
  const status = item.approved
    ? automatic
      ? '✓ Auto-approved'
      : '✓ Approved'
    : automatic
      ? '⊘ Auto-rejected'
      : '⊘ Rejected'
  const source = automatic ? 'AutoReview' : 'User'

  return (
    <div className="text-xs">
      <div className="font-medium">
        {status} · {item.toolName}
      </div>
      <dl className="mt-2 grid gap-1 text-muted-foreground">
        <div className="flex gap-2">
          <dt className="shrink-0">Tool</dt>
          <dd className="min-w-0 break-words text-foreground">{item.toolName}</dd>
        </div>
        <div className="flex gap-2">
          <dt className="shrink-0">Source</dt>
          <dd className="min-w-0 break-words text-foreground">{source}</dd>
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

function activityTitle(item: TimelineActivityViewModel): string {
  if (item.type === 'reasoning') {
    return 'Thinking…'
  }
  if (item.type === 'tool') {
    return item.label ?? `Calling ${item.name}…`
  }
  if (item.type === 'unmatched_tool_output') {
    return 'Unmatched tool output'
  }

  const automatic = item.source === 'permission'
  const status = item.approved
    ? automatic
      ? '✓ Auto-approved'
      : '✓ Approved'
    : automatic
      ? '⊘ Auto-rejected'
      : '⊘ Rejected'
  return `${status} · ${item.toolName}`
}

function ActivityDetails({ item }: { item: TimelineActivityViewModel }) {
  if (item.type === 'reasoning') {
    return (
      <div className="text-xs">
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

function ActivityStep({ item }: { item: TimelineActivityViewModel }) {
  const [open, setOpen] = useState(false)

  return (
    <Collapsible
      className="rounded-md border border-border/70 bg-background/70"
      onOpenChange={setOpen}
      open={open}
    >
      <CollapsibleTrigger asChild>
        <Button
          className="flex w-full cursor-pointer items-center justify-start gap-0 px-3 py-2 text-left text-xs font-normal text-muted-foreground"
          size={null}
          type="button"
          variant={null}
        >
          {open ? (
            <ChevronDown aria-hidden="true" className="mr-2 shrink-0" size={14} />
          ) : (
            <ChevronRight aria-hidden="true" className="mr-2 shrink-0" size={14} />
          )}
          <span className="min-w-0 truncate">{activityTitle(item)}</span>
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="border-t border-border/70 p-3">
        <ActivityDetails item={item} />
      </CollapsibleContent>
    </Collapsible>
  )
}

export function ActivityGroup({ group }: ActivityGroupProps) {
  const [open, setOpen] = useState(!group.collapsed)

  return (
    <Collapsible
      className="my-2 max-w-full rounded-lg border border-border/80 bg-card"
      data-seq={group.seq}
      onOpenChange={setOpen}
      open={open}
    >
      <CollapsibleTrigger asChild>
        <Button
          className="flex w-full cursor-pointer items-center justify-start gap-0 px-3 py-2 text-left text-sm font-normal text-muted-foreground"
          size={null}
          type="button"
          variant={null}
        >
          {open ? (
            <ChevronDown aria-hidden="true" className="mr-2 shrink-0" size={16} />
          ) : (
            <ChevronRight aria-hidden="true" className="mr-2 shrink-0" size={16} />
          )}
          <span>{group.title}</span>
        </Button>
      </CollapsibleTrigger>
      <CollapsibleContent className="space-y-2 border-t border-border/80 p-3">
        {group.items.map((item) => (
          <ActivityStep item={item} key={item.id} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  )
}
