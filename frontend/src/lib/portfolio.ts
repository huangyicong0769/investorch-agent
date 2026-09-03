import type { PortfolioSummary } from '../api/types'

export function portfolioPath(portfolioId: string): string {
  return `/portfolios/${encodeURIComponent(portfolioId)}`
}

export function strategySourceLabel(sourcePath: string): string {
  const parts = sourcePath.split(/[\\/]/)
  return parts.at(-1) || sourcePath
}

export function logicalCashEntries(portfolio: PortfolioSummary): [string, string][] {
  return Object.entries(portfolio.logical_cash)
}
