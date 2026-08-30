import { useEffect, useRef, useState } from 'react'

import type { SessionPresentationState } from '../../api/types'

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
  const containerRef = useRef<HTMLDivElement>(null)
  const [open, setOpen] = useState(false)
  const capacity = compactTokens(contextWindowTokens)
  const mainContext = compactTokens(presentation.main_context_tokens)
  const summary = `${mainContext} / ${capacity}`
  const usage = presentation.usage

  useEffect(() => {
    if (!open) {
      return
    }
    const closeOnOutsideClick = (event: PointerEvent) => {
      if (event.target instanceof Node && !containerRef.current?.contains(event.target)) {
        setOpen(false)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [open])

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
    <div className="relative" ref={containerRef}>
      <button
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label={`Token usage: ${summary}`}
        className="rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground"
        onClick={() => setOpen((current) => !current)}
        type="button"
      >
        {summary}
      </button>
      {open ? (
        <div
          aria-label="Token usage"
          className="absolute right-0 top-full z-30 mt-2 w-64 rounded-xl border border-border bg-card p-4 shadow-xl"
          role="dialog"
        >
          <h2 className="text-sm font-semibold">Usage</h2>
          <dl className="mt-3 space-y-2 text-xs">
            {rows.map(([label, value]) => (
              <div className="flex items-center justify-between gap-4" key={label}>
                <dt className="text-muted-foreground">{label}</dt>
                <dd className="text-right tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      ) : null}
    </div>
  )
}
