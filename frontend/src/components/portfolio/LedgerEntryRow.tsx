import { ChevronRight } from 'lucide-react'

import type { PortfolioLedgerEntry } from '../../api/types'
import { formatPortfolioTimestamp, instrumentLabel } from '../../lib/portfolio'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '../ui/collapsible'

interface LedgerEntryRowProps {
  entry: PortfolioLedgerEntry
}

function entrySummary(entry: PortfolioLedgerEntry): string {
  const payload = entry.payload
  switch (entry.entry_type) {
    case 'OPENING_POSITION':
      return 'instrument' in payload && payload.instrument && 'quantity' in payload
        ? `${instrumentLabel(payload.instrument)} · Opening quantity ${payload.quantity}`
        : 'Opening position'
    case 'OPENING_CASH':
      return 'currency' in payload && 'amount' in payload
        ? `${payload.currency} · Opening cash ${payload.amount}`
        : 'Opening cash'
    case 'TRADE':
      return 'side' in payload && payload.instrument
        ? `${instrumentLabel(payload.instrument)} · ${payload.side} · ${payload.quantity} @ ${payload.price}`
        : 'Realized trade'
    case 'CASH_FLOW':
      return 'currency' in payload && 'amount' in payload
        ? `${payload.currency} · Cash flow ${payload.amount}`
        : 'Cash flow'
    case 'INCOME':
      return 'gross_amount' in payload
        ? `${payload.currency} · Income ${payload.gross_amount}${payload.instrument ? ` · ${instrumentLabel(payload.instrument)}` : ''}`
        : 'Income'
    case 'TRANSFER':
      if ('direction' in payload && 'quantity' in payload && payload.instrument) {
        return `${instrumentLabel(payload.instrument)} · Transfer ${payload.direction} · ${payload.quantity}`
      }
      return 'direction' in payload && 'amount' in payload
        ? `${payload.currency} · Transfer ${payload.direction} · ${payload.amount}`
        : 'Transfer'
    case 'ADJUSTMENT':
      if ('resulting_quantity' in payload && payload.instrument) {
        return `${instrumentLabel(payload.instrument)} · Quantity adjusted to ${payload.resulting_quantity}`
      }
      return 'resulting_amount' in payload
        ? `${payload.currency} · Cash adjusted to ${payload.resulting_amount}`
        : 'Adjustment'
    case 'VOID':
      return 'target_entry_id' in payload ? `Target ${payload.target_entry_id} · ${payload.reason}` : 'Voided entry'
  }
}

function detailLabel(key: string): string {
  const words = key.replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function detailValue(key: string, value: unknown): string {
  if (value === null) {
    return key.includes('cost') ? 'Unknown' : 'None'
  }
  if (typeof value === 'object') {
    if ('code' in value && typeof value.code === 'string' && 'market' in value && typeof value.market === 'string') {
      return instrumentLabel({ code: value.code, market: value.market })
    }
    return 'Unsupported value'
  }
  return String(value)
}

function DetailRow({ label, value }: { label: string; value: string | number | null }) {
  return (
    <div className="grid gap-1 py-1.5 sm:grid-cols-[9rem_minmax(0,1fr)] sm:gap-4">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="break-all text-xs tabular-nums">{value ?? 'None'}</dd>
    </div>
  )
}

export function LedgerEntryRow({ entry }: LedgerEntryRowProps) {
  return (
    <Collapsible className="border-b border-border/70 last:border-b-0">
      <CollapsibleTrigger className="group flex w-full items-start gap-3 px-5 py-4 text-left outline-none transition-colors hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/40">
        <ChevronRight
          aria-hidden="true"
          className="mt-0.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-90"
          size={16}
        />
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
            <span className="text-xs font-semibold tracking-wide">{entry.entry_type}</span>
            <time className="text-xs text-muted-foreground tabular-nums" dateTime={entry.effective_at}>
              {formatPortfolioTimestamp(entry.effective_at)}
            </time>
          </span>
          <span className="mt-1.5 block break-words text-sm text-muted-foreground">{entrySummary(entry)}</span>
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <dl className="border-t border-border/60 bg-muted/20 px-5 py-3 pl-12">
          <DetailRow label="Entry ID" value={entry.entry_id} />
          <DetailRow label="Operation ID" value={entry.operation_id} />
          <DetailRow label="Sequence" value={entry.sequence} />
          <DetailRow label="Effective at" value={entry.effective_at} />
          <DetailRow label="Recorded at" value={entry.recorded_at} />
          <DetailRow label="Source" value={entry.source} />
          <DetailRow label="External reference" value={entry.external_ref} />
          {Object.entries(entry.payload).map(([key, value]) => (
            <DetailRow key={key} label={detailLabel(key)} value={detailValue(key, value)} />
          ))}
        </dl>
      </CollapsibleContent>
    </Collapsible>
  )
}
