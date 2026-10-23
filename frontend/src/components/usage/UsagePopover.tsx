import type { SessionPresentationState, TokenUsage } from '../../api/types'
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
  // A measured numerator belongs to its model snapshot, even after selecting another model.
  const contextCapacity = presentation.context_window_tokens
    ?? (presentation.main_context_tokens === null ? contextWindowTokens : null)
  const capacity = compactTokens(contextCapacity)
  const mainContext = compactTokens(presentation.main_context_tokens)
  const estimated = presentation.context_kind === 'estimated'
  const summary = `${estimated ? 'Estimated ' : ''}${mainContext} / ${capacity}${presentation.compacting ? ' · Compacting…' : ''}`
  const contextRatio = contextCapacity && presentation.main_context_tokens !== null
    ? Math.min(1, Math.max(0, presentation.main_context_tokens / contextCapacity))
    : null
  const rows: [string, keyof Omit<TokenUsage, 'last_request_total_tokens'>][] = [
    ['Requests', 'requests'],
    ['Input', 'input_tokens'],
    ['Cached', 'cached_input_tokens'],
    ['Cache write', 'cache_write_input_tokens'],
    ['Output', 'output_tokens'],
    ['Reasoning', 'reasoning_output_tokens'],
    ['Total', 'total_tokens'],
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
          className="z-30 w-80 max-w-[calc(100vw-1.5rem)] rounded-xl border border-border bg-card p-4 shadow-xl"
        >
          <h2 className="text-sm font-semibold">Context &amp; usage</h2>
          <div className="mt-3 space-y-1 text-xs">
            <div className="flex items-center justify-between gap-4">
              <span>{estimated ? 'Main context (estimated)' : 'Main context'}</span>
              <span className="tabular-nums">{presentation.main_context_tokens === null ? '—' : exactTokens(presentation.main_context_tokens)} / {contextCapacity === null ? '—' : exactTokens(contextCapacity)}</span>
            </div>
            {presentation.context_model ? <p className="break-words text-muted-foreground">{presentation.context_model}</p> : null}
            {presentation.compacting ? <p role="status" className="text-muted-foreground">Compacting context…</p>
              : presentation.context_kind === 'unknown' ? <p className="text-muted-foreground">Context has not been measured yet.</p> : null}
          </div>
          <table className="mt-4 w-full text-xs">
            <caption className="sr-only">Token usage by main agent and auxiliary requests</caption>
            <thead><tr className="text-muted-foreground">
              <th scope="col" className="pb-2 text-left font-normal">Usage</th>
              <th scope="col" className="pb-2 text-right font-normal">Main</th>
              <th scope="col" className="pb-2 text-right font-normal">Auxiliary</th>
            </tr></thead>
            <tbody>{rows.map(([label, key]) => (
              <tr key={key}>
                <th scope="row" className="py-1 text-left font-normal text-muted-foreground">{label}</th>
                <td className="py-1 text-right tabular-nums">{exactTokens(presentation.main_usage[key])}</td>
                <td className="py-1 text-right tabular-nums">{exactTokens(presentation.auxiliary_usage[key])}</td>
              </tr>
            ))}</tbody>
          </table>
        </PopoverContent>
      </Popover>
    </div>
  )
}
