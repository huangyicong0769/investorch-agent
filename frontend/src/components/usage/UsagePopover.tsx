import { Gauge } from 'lucide-react'

import type { SessionPresentationState } from '../../api/types'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

interface UsagePopoverProps {
  contextWindowTokens: number | null
  presentation: SessionPresentationState
}

function compactTokens(value: number | null): string {
  if (value === null) {
    return '—'
  }
  return new Intl.NumberFormat('en', {
    maximumFractionDigits: 1,
    notation: 'compact',
  }).format(value)
}

function exactTokens(value: number): string {
  return new Intl.NumberFormat('en').format(value)
}

export function UsagePopover({ contextWindowTokens, presentation }: UsagePopoverProps) {
  const capacity = compactTokens(contextWindowTokens)
  const mainContext = compactTokens(presentation.main_context_tokens)
  const summary = `${mainContext} / ${capacity}`
  const usage = presentation.usage

  const rows = [
    ['Requests', exactTokens(usage.requests)],
    ['Input', exactTokens(usage.input_tokens)],
    ['Cached', exactTokens(usage.cached_input_tokens)],
    ['Cache write', exactTokens(usage.cache_write_input_tokens)],
    ['Output', exactTokens(usage.output_tokens)],
    ['Reasoning', exactTokens(usage.reasoning_output_tokens)],
    ['Total', exactTokens(usage.total_tokens)],
    [
      'Main context',
      `${presentation.main_context_tokens === null ? '—' : exactTokens(presentation.main_context_tokens)} / ${
        contextWindowTokens === null ? '—' : exactTokens(contextWindowTokens)
      }`,
    ],
  ]

  return (
    <div className="relative">
      <Popover>
        <PopoverTrigger asChild>
          <Button
            size={null}
            variant={null}
            aria-label={`Context usage: ${summary}`}
            className="flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
            title="Context and token usage"
            type="button"
          >
            <Gauge aria-hidden="true" size={14} />
            {summary}
          </Button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          aria-label="Context and token usage"
          collisionPadding={12}
          side="top"
          sideOffset={8}
          className="z-30 w-64 max-w-[calc(100vw-1.5rem)] rounded-xl border border-border bg-card p-4 shadow-xl"
        >
          <h2 className="text-sm font-semibold">Context &amp; usage</h2>
          <dl className="mt-3 space-y-2 text-xs">
            {rows.map(([label, value]) => (
              <div className="flex items-center justify-between gap-4" key={label}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="text-right tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </PopoverContent>
      </Popover>
    </div>
  )
}
