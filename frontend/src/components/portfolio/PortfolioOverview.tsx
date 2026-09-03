import type { PortfolioDetailResponse } from '../../api/types'
import { logicalCashEntries } from '../../lib/portfolio'

interface PortfolioOverviewProps {
  detail: PortfolioDetailResponse
}

export function PortfolioOverview({ detail }: PortfolioOverviewProps) {
  const cash = logicalCashEntries(detail.state.cash)
  const strategy = detail.portfolio.strategy_binding

  return (
    <section aria-labelledby="portfolio-overview-heading">
      <h2 className="sr-only" id="portfolio-overview-heading">
        Overview
      </h2>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs font-medium text-muted-foreground">Logical cash</p>
          {cash.length === 0 ? (
            <p className="mt-2 text-sm text-muted-foreground">Not recorded</p>
          ) : (
            <div className="mt-2 space-y-1">
              {cash.map(([currency, amount]) => (
                <p className="break-all text-sm font-semibold tabular-nums" key={currency}>
                  {currency} {amount}
                </p>
              ))}
            </div>
          )}
        </div>
        <div className="rounded-xl border border-border bg-card p-4">
          <p className="text-xs font-medium text-muted-foreground">Holdings</p>
          <p className="mt-2 text-sm font-semibold tabular-nums">{detail.state.holdings.length}</p>
        </div>
        <div className="min-w-0 rounded-xl border border-border bg-card p-4">
          <p className="text-xs font-medium text-muted-foreground">Strategy</p>
          {strategy ? (
            <p className="mt-2 break-words text-sm font-medium" title={strategy.source_path}>
              {strategy.source_path}
            </p>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">No strategy binding</p>
          )}
        </div>
      </div>
    </section>
  )
}
