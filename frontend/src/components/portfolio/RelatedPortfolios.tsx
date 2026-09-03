import { ArrowUpRight } from 'lucide-react'
import { Link } from 'react-router-dom'

import type { PortfolioSummary } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { logicalCashEntries, portfolioPath } from '../../lib/portfolio'
import { cn } from '../../lib/utils'
import { Button } from '@/components/ui/button'

interface RelatedPortfoliosProps {
  error: unknown | null
  onRetry: () => void
  portfolios: PortfolioSummary[]
}

export function RelatedPortfolios({ error, onRetry, portfolios }: RelatedPortfoliosProps) {
  if (error) {
    return (
      <div className="border-b border-border px-4 py-2 sm:px-6" role="alert">
        <div className="mx-auto flex max-w-4xl items-center justify-between gap-3 text-xs text-muted-foreground">
          <span>{errorMessage(error, 'Related Portfolios could not be loaded.')}</span>
          <Button onClick={onRetry} size="xs" type="button" variant="ghost">
            Retry
          </Button>
        </div>
      </div>
    )
  }

  if (portfolios.length === 0) {
    return null
  }

  return (
    <section aria-labelledby="related-portfolios-heading" className="border-b border-border bg-card/35 px-4 py-3 sm:px-6">
      <div className="mx-auto max-w-4xl">
        <h2 className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground" id="related-portfolios-heading">
          Related Portfolios
        </h2>
        <div className="mt-2 flex gap-2 overflow-x-auto pb-1">
          {portfolios.map((portfolio) => {
            const cash = logicalCashEntries(portfolio.logical_cash)
            const archived = portfolio.status === 'ARCHIVED'
            return (
              <Link
                className={cn(
                  'group min-w-48 max-w-64 flex-1 rounded-lg border border-border bg-card px-3 py-2.5 outline-none transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/40',
                  archived && 'text-muted-foreground',
                )}
                key={portfolio.portfolio_id}
                to={portfolioPath(portfolio.portfolio_id)}
              >
                <div className="flex items-start justify-between gap-3">
                  <span className={cn('truncate text-sm font-medium', !archived && 'text-foreground')}>
                    {portfolio.name}
                  </span>
                  <ArrowUpRight
                    aria-hidden="true"
                    className="mt-0.5 shrink-0 text-muted-foreground group-hover:text-foreground"
                    size={13}
                  />
                </div>
                <p className="mt-1 text-[11px] tracking-wide text-muted-foreground">
                  {portfolio.status} · {portfolio.base_currency} · {portfolio.holdings_count}{' '}
                  {portfolio.holdings_count === 1 ? 'holding' : 'holdings'}
                </p>
                <p className="mt-1 truncate text-xs tabular-nums text-muted-foreground">
                  Logical cash ·{' '}
                  {cash.length === 0
                    ? 'Not recorded'
                    : cash.map(([currency, amount]) => `${currency} ${amount}`).join(' · ')}
                </p>
              </Link>
            )
          })}
        </div>
      </div>
    </section>
  )
}
