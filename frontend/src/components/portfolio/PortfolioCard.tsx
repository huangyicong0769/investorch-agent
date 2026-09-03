import { ArrowUpRight } from 'lucide-react'
import { Link } from 'react-router-dom'

import type { PortfolioSummary } from '../../api/types'
import { logicalCashEntries, portfolioPath, strategySourceLabel } from '../../lib/portfolio'
import { cn } from '../../lib/utils'

interface PortfolioCardProps {
  portfolio: PortfolioSummary
}

export function PortfolioCard({ portfolio }: PortfolioCardProps) {
  const cash = logicalCashEntries(portfolio.logical_cash)
  const archived = portfolio.status === 'ARCHIVED'

  return (
    <Link
      className={cn(
        'group flex min-h-44 flex-col rounded-xl border border-border bg-card p-5 outline-none transition-[border-color,background-color,box-shadow] hover:border-muted-foreground/40 hover:bg-muted/30 hover:shadow-sm focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40',
        archived && 'bg-card/60 text-muted-foreground',
      )}
      to={portfolioPath(portfolio.portfolio_id)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className={cn('truncate text-base font-semibold', archived ? 'text-foreground/75' : 'text-foreground')}>
            {portfolio.name}
          </h2>
          <p className="mt-1 text-xs font-medium tracking-wide text-muted-foreground">
            {portfolio.status} · {portfolio.base_currency}
          </p>
        </div>
        <ArrowUpRight
          aria-hidden="true"
          className="mt-0.5 text-muted-foreground transition-colors group-hover:text-foreground"
          size={16}
        />
      </div>

      <div className="mt-6 space-y-2 text-sm">
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-muted-foreground">Logical cash</span>
          {cash.length === 0 ? (
            <span className="text-muted-foreground">Not recorded</span>
          ) : (
            <span className="text-right font-medium tabular-nums">
              {cash.map(([currency, amount]) => `${currency} ${amount}`).join(' · ')}
            </span>
          )}
        </div>
        <div className="flex items-baseline justify-between gap-4">
          <span className="text-muted-foreground">Holdings</span>
          <span className="font-medium tabular-nums">{portfolio.holdings_count}</span>
        </div>
      </div>

      {portfolio.strategy_binding ? (
        <p
          className="mt-auto truncate pt-5 text-xs text-muted-foreground"
          title={portfolio.strategy_binding.source_path}
        >
          Strategy · {strategySourceLabel(portfolio.strategy_binding.source_path)}
        </p>
      ) : null}
    </Link>
  )
}
