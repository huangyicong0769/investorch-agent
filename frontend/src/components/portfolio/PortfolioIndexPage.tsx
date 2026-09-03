import { useQuery } from '@tanstack/react-query'

import { portfoliosQueryOptions } from '../../api/queries'
import { errorMessage } from '../../lib/errors'
import { PortfolioCard } from './PortfolioCard'
import { Button } from '@/components/ui/button'

function PortfolioCardSkeleton() {
  return (
    <div
      aria-hidden="true"
      className="min-h-44 animate-pulse rounded-xl border border-border bg-card p-5 motion-reduce:animate-none"
    >
      <div className="h-5 w-2/5 rounded bg-muted" />
      <div className="mt-2 h-3 w-1/3 rounded bg-muted" />
      <div className="mt-7 h-4 rounded bg-muted" />
      <div className="mt-3 h-4 rounded bg-muted" />
    </div>
  )
}

export function PortfolioIndexPage() {
  const portfoliosQuery = useQuery(portfoliosQueryOptions())
  const portfolios = portfoliosQuery.data?.portfolios ?? []

  return (
    <div className="min-h-dvh">
      <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
        <header>
          <h1 className="text-2xl font-semibold tracking-tight">Portfolios</h1>
          <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
            View and work with your logical investment portfolios.
          </p>
        </header>

        {portfoliosQuery.isPending ? (
          <div aria-label="Loading portfolios" className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            <PortfolioCardSkeleton />
            <PortfolioCardSkeleton />
            <PortfolioCardSkeleton />
          </div>
        ) : null}

        {portfoliosQuery.isError ? (
          <div className="mt-8 rounded-xl border border-border bg-card p-6" role="alert">
            <p className="text-sm text-destructive">
              {errorMessage(portfoliosQuery.error, 'Portfolios could not be loaded.')}
            </p>
            <Button
              className="mt-4"
              onClick={() => void portfoliosQuery.refetch()}
              size="sm"
              type="button"
              variant="outline"
            >
              Retry
            </Button>
          </div>
        ) : null}

        {portfoliosQuery.isSuccess && portfolios.length === 0 ? (
          <div className="mt-8 rounded-xl border border-dashed border-border bg-card/60 px-6 py-14 text-center">
            <h2 className="text-base font-semibold">No portfolios yet</h2>
            <p className="mt-2 text-sm text-muted-foreground">Create one with the Agent.</p>
          </div>
        ) : null}

        {portfoliosQuery.isSuccess && portfolios.length > 0 ? (
          <div className="mt-8 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {portfolios.map((portfolio) => (
              <PortfolioCard key={portfolio.portfolio_id} portfolio={portfolio} />
            ))}
          </div>
        ) : null}
      </div>
    </div>
  )
}
