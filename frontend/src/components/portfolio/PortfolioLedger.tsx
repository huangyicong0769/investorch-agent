import { useQuery } from '@tanstack/react-query'

import { portfolioLedgerQueryOptions } from '../../api/queries'
import { errorMessage } from '../../lib/errors'
import { LedgerEntryRow } from './LedgerEntryRow'
import { Button } from '@/components/ui/button'

interface PortfolioLedgerProps {
  portfolioId: string
}

export function PortfolioLedger({ portfolioId }: PortfolioLedgerProps) {
  const ledgerQuery = useQuery(portfolioLedgerQueryOptions(portfolioId))
  const ledger = ledgerQuery.data
  const entries = ledger?.entries ?? []

  return (
    <section aria-labelledby="portfolio-ledger-heading" className="overflow-hidden rounded-xl border border-border bg-card">
      <div className="flex items-baseline justify-between gap-4 border-b border-border px-5 py-4">
        <h2 className="text-base font-semibold" id="portfolio-ledger-heading">
          Recent Ledger
        </h2>
        {ledger?.has_older ? (
          <p className="text-xs text-muted-foreground tabular-nums">
            Showing {ledger.returned} of {ledger.total}
          </p>
        ) : null}
      </div>

      {ledgerQuery.isPending ? (
        <div aria-label="Loading Ledger" className="animate-pulse divide-y divide-border/70 motion-reduce:animate-none">
          {[0, 1, 2].map((item) => (
            <div className="px-5 py-4" key={item}>
              <div className="h-3 w-1/4 rounded bg-muted" />
              <div className="mt-3 h-4 w-3/5 rounded bg-muted" />
            </div>
          ))}
        </div>
      ) : null}

      {ledgerQuery.isError ? (
        <div className="px-5 py-8 text-center" role="alert">
          <p className="text-sm text-destructive">
            {errorMessage(ledgerQuery.error, 'Recent Ledger entries could not be loaded.')}
          </p>
          <Button
            className="mt-4"
            onClick={() => void ledgerQuery.refetch()}
            size="sm"
            type="button"
            variant="outline"
          >
            Retry
          </Button>
        </div>
      ) : null}

      {ledgerQuery.isSuccess && entries.length === 0 ? (
        <p className="px-5 py-10 text-center text-sm text-muted-foreground">No Ledger entries</p>
      ) : null}

      {ledgerQuery.isSuccess && entries.length > 0 ? (
        <div>
          {entries.map((entry) => (
            <LedgerEntryRow entry={entry} key={entry.entry_id} />
          ))}
        </div>
      ) : null}
    </section>
  )
}
