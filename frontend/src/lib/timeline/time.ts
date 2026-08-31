function parsedDate(value: string): Date | null {
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? null : date
}

export function timelineDayKey(value: string): string {
  const date = parsedDate(value)
  if (!date) {
    return value
  }
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
}

export function formatTimelineTime(value: string): string {
  const date = parsedDate(value)
  if (!date) {
    return '—'
  }
  return new Intl.DateTimeFormat(undefined, {
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

export function formatTimelineDay(value: string): string {
  const date = parsedDate(value)
  if (!date) {
    return 'Unknown time'
  }

  const today = new Date()
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate())
  const startOfDate = new Date(date.getFullYear(), date.getMonth(), date.getDate())
  const dayDifference = Math.round((startOfToday.getTime() - startOfDate.getTime()) / 86_400_000)
  const time = formatTimelineTime(value)

  if (dayDifference === 0) {
    return `Today · ${time}`
  }
  if (dayDifference === 1) {
    return `Yesterday · ${time}`
  }
  return `${new Intl.DateTimeFormat(undefined, {
    month: 'short',
    day: 'numeric',
    year: date.getFullYear() === today.getFullYear() ? undefined : 'numeric',
  }).format(date)} · ${time}`
}

export function formatDuration(durationMs: number): string {
  const totalSeconds = Math.max(0, Math.round(durationMs / 1000))
  const seconds = totalSeconds % 60
  const totalMinutes = Math.floor(totalSeconds / 60)
  const minutes = totalMinutes % 60
  const hours = Math.floor(totalMinutes / 60)

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`
  }
  if (totalMinutes > 0) {
    return `${totalMinutes}m ${seconds}s`
  }
  return `${seconds}s`
}
