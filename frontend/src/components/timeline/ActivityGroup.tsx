import { useState } from 'react'
import { Brain, ChevronRight, ListTree, ShieldCheck, ShieldX, Terminal, Wrench } from 'lucide-react'

import type {
  TimelineActivityGroupViewModel,
  TimelineActivityViewModel,
  TimelineApprovalViewModel,
  TimelineToolViewModel,
  TimelineUnmatchedToolOutputViewModel,
} from '../../lib/timeline/project'
import { formatJsonValue, reasoningTitle } from '../../lib/timeline/project'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { MessageImages } from '../images/MessageImages'
import { CopyButton } from '../ui/CopyButton'

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
    <div className="overflow-hidden rounded-lg bg-muted/50">
      <div className="flex items-center justify-between gap-3 px-3 py-1.5 text-[11px] text-muted-foreground">
        <span>{label}</span>
        <CopyButton value={text} />
      </div>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-3 pb-3 font-mono text-xs leading-5 text-foreground/85">
        {text}
      </pre>
    </div>
  )
}

function ToolActivity({ item }: { item: TimelineToolViewModel }) {
  return (
    <div className="space-y-2 text-xs">
      {item.label ? <p className="font-mono text-[11px] text-muted-foreground">{item.name}</p> : null}
      {item.arguments !== null ? <Detail formatJson label="Arguments" value={item.arguments} /> : null}
      {item.observation !== null ? <Detail label="Output" value={item.observation} /> : null}
      <MessageImages images={item.images} fallbackLabel="Tool output image" />
    </div>
  )
}

function UnmatchedOutput({ item }: { item: TimelineUnmatchedToolOutputViewModel }) {
  return (
    <div className="space-y-2 text-xs">
      <Detail label="Output" value={item.output} />
      <MessageImages images={item.images} fallbackLabel="Tool output image" />
    </div>
  )
}

function ApprovalActivity({ item }: { item: TimelineApprovalViewModel }) {
  return (
    <div className="space-y-3 text-xs">
      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-muted-foreground">
        <dt>Source</dt>
        <dd className="min-w-0 break-words text-foreground/85">{item.source === 'permission' ? 'AutoReview' : 'User'}</dd>
        <dt>Decision</dt>
        <dd className="min-w-0 break-words text-foreground/85">{item.reviewDecision ?? (item.approved ? 'Approved' : 'Rejected')}</dd>
        {item.reviewReason ? <>
          <dt>Reason</dt>
          <dd className="min-w-0 whitespace-pre-wrap break-words text-foreground/85">{item.reviewReason}</dd>
        </> : null}
      </dl>
      {item.arguments !== null ? <Detail formatJson label="Arguments" value={item.arguments} /> : null}
    </div>
  )
}

function activityTitle(item: TimelineActivityViewModel): string {
  if (item.type === 'reasoning') return reasoningTitle(item)
  if (item.type === 'tool') return item.label ?? item.name
  if (item.type === 'unmatched_tool_output') return 'Unmatched tool output'
  const automatic = item.source === 'permission'
  const status = item.approved
    ? automatic ? 'Auto-approved' : 'Approved'
    : automatic ? 'Auto-rejected' : 'Rejected'
  return `${status} · ${item.toolName}`
}

function ActivityIcon({ item }: { item: TimelineActivityViewModel }) {
  const Icon = item.type === 'reasoning' ? Brain
    : item.type === 'tool' ? Wrench
      : item.type === 'unmatched_tool_output' ? Terminal
        : item.approved ? ShieldCheck : ShieldX
  return <Icon aria-hidden="true" className="size-3.5 shrink-0" />
}

function ActivityDetails({ item }: { item: TimelineActivityViewModel }) {
  if (item.type === 'reasoning') {
    return <p className="max-h-80 overflow-auto whitespace-pre-wrap break-words text-sm leading-6 text-muted-foreground">{item.text}</p>
  }
  if (item.type === 'tool') return <ToolActivity item={item} />
  if (item.type === 'unmatched_tool_output') return <UnmatchedOutput item={item} />
  return <ApprovalActivity item={item} />
}

const triggerClass = 'group flex max-w-full cursor-pointer items-center gap-2 rounded-md py-2 text-left text-xs text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

function ActivityStep({ item, onOpenChange }: { item: TimelineActivityViewModel; onOpenChange?: (open: boolean) => void }) {
  return (
    <Collapsible onOpenChange={onOpenChange}>
      <CollapsibleTrigger className={triggerClass}>
        <ActivityIcon item={item} />
        <span className="min-w-0 truncate" title={activityTitle(item)}>{activityTitle(item)}</span>
        <ChevronRight aria-hidden="true" className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none" />
      </CollapsibleTrigger>
      <CollapsibleContent className="pb-3 pt-1">
        <ActivityDetails item={item} />
      </CollapsibleContent>
    </Collapsible>
  )
}

export function ActivityGroup({ group }: ActivityGroupProps) {
  const [open, setOpen] = useState(!group.collapsed)

  const single = group.items.length === 1

  return (
    <Collapsible className="my-1 max-w-full" data-seq={group.seq} onOpenChange={setOpen} open={single || open}>
      {!single ? <CollapsibleTrigger className={triggerClass}>
        <ListTree aria-hidden="true" className="size-3.5 shrink-0" />
        <span className="min-w-0 truncate" title={group.title}>{group.title}</span>
        <span className="text-muted-foreground/65">{group.items.length} steps</span>
        <ChevronRight aria-hidden="true" className="size-3.5 shrink-0 transition-transform group-data-[state=open]:rotate-90 motion-reduce:transition-none" />
      </CollapsibleTrigger> : null}
      <CollapsibleContent className={single ? undefined : 'mb-3 mt-1 space-y-0.5'}>
        {group.items.map((item) => <ActivityStep item={item} key={item.id} onOpenChange={single ? setOpen : undefined} />)}
      </CollapsibleContent>
    </Collapsible>
  )
}
