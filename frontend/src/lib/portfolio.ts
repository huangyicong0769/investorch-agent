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

export function instrumentLabel(instrument: { code: string; market: string }): string {
  return `${instrument.code} · ${instrument.market}`
}

export function formatPortfolioTimestamp(value: string): string {
  const match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.exec(value)
  if (!match) {
    return value
  }
  const [, date, time, rawOffset] = match
  const offset = rawOffset === 'Z' ? '+00:00' : rawOffset
  return `${date} ${time} ${offset}`
}
