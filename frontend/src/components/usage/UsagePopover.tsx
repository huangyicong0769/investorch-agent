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
  const contextRatio = contextWindowTokens && presentation.main_context_tokens !== null
    ? Math.min(1, Math.max(0, presentation.main_context_tokens / contextWindowTokens))
    : null

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
            size="icon-sm"
            variant="ghost"
            aria-label={`Context usage: ${summary}`}
            className="size-8 rounded-full text-muted-foreground hover:text-foreground"
            title={`Context usage: ${summary}`}
            type="button"
          >
            <svg aria-hidden="true" className="size-4 -rotate-90" viewBox="0 0 20 20" fill="none">
              <circle cx="10" cy="10" r="7" stroke="currentColor" strokeWidth="2" className="opacity-25" />
              {contextRatio !== null ? <circle cx="10" cy="10" r="7" pathLength="1" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeDasharray={`${contextRatio} 1`} /> : null}
            </svg>
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
