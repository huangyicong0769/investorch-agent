export function portfolioPath(portfolioId: string): string {
  return `/portfolios/${encodeURIComponent(portfolioId)}`
}

export function strategySourceLabel(sourcePath: string): string {
  const parts = sourcePath.split(/[\\/]/)
  return parts.at(-1) || sourcePath
}

export function logicalCashEntries(logicalCash: Record<string, string>): [string, string][] {
  return Object.entries(logicalCash)
}
