import { useQuery } from '@tanstack/react-query'
import { ArrowLeft } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { portfolioQueryOptions } from '../../api/queries'
import { errorMessage } from '../../lib/errors'
import { HoldingsTable } from './HoldingsTable'
import { PortfolioLedger } from './PortfolioLedger'
import { PortfolioOverview } from './PortfolioOverview'
import { Button } from '@/components/ui/button'

function DetailLoading() {
  return (
    <div aria-label="Loading Portfolio" className="animate-pulse space-y-7 motion-reduce:animate-none">
      <div>
        <div className="h-4 w-28 rounded bg-muted" />
        <div className="mt-7 h-8 w-56 rounded bg-muted" />
        <div className="mt-3 h-4 w-36 rounded bg-muted" />
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="h-24 rounded-xl bg-muted" />
        <div className="h-24 rounded-xl bg-muted" />
        <div className="h-24 rounded-xl bg-muted" />
      </div>
      <div className="h-64 rounded-xl bg-muted" />
    </div>
  )
}

export function PortfolioDetailPage() {
  const { portfolioId = '' } = useParams()
  const portfolioQuery = useQuery({
    ...portfolioQueryOptions(portfolioId),
    enabled: Boolean(portfolioId),
  })

  if (portfolioQuery.isPending) {
    return (
      <div className="mx-auto min-h-dvh w-full max-w-6xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
        <DetailLoading />
      </div>
    )
  }

  if (portfolioQuery.isError) {
    const missing = portfolioQuery.error instanceof ApiError && portfolioQuery.error.code === 'portfolio_not_found'
    return (
      <div className="flex min-h-dvh items-center justify-center px-4">
        <div className="max-w-md text-center">
          <h1 className="text-xl font-semibold">{missing ? 'Portfolio not found' : 'Unable to load Portfolio'}</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {missing
              ? 'This Portfolio does not exist or is no longer available.'
              : errorMessage(portfolioQuery.error, 'The Portfolio could not be loaded.')}
          </p>
          <div className="mt-5 flex justify-center gap-3">
            <Button asChild size="sm" variant="outline">
              <Link to="/portfolios">Back to Portfolios</Link>
            </Button>
            {!missing ? (
              <Button onClick={() => void portfolioQuery.refetch()} size="sm" type="button">
                Retry
              </Button>
            ) : null}
          </div>
        </div>
      </div>
    )
  }

  const detail = portfolioQuery.data

  return (
    <div className="min-h-dvh">
      <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
        <header>
          <Link
            className="inline-flex items-center gap-1.5 rounded text-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/40"
            to="/portfolios"
          >
            <ArrowLeft aria-hidden="true" size={15} />
            Portfolios
          </Link>
          <div className="mt-6">
            <h1 className="break-words text-2xl font-semibold tracking-tight">{detail.portfolio.name}</h1>
            <p className="mt-2 text-xs font-medium tracking-wide text-muted-foreground">
              {detail.portfolio.status} · {detail.portfolio.base_currency}
            </p>
            {detail.portfolio.description ? (
              <p className="mt-3 max-w-2xl text-sm leading-6 text-muted-foreground">
                {detail.portfolio.description}
              </p>
            ) : null}
          </div>
        </header>

        <div className="mt-8 space-y-6">
          <PortfolioOverview detail={detail} />
          <HoldingsTable holdings={detail.state.holdings} />
          <PortfolioLedger portfolioId={detail.portfolio.portfolio_id} />
        </div>
      </div>
    </div>
  )
}
