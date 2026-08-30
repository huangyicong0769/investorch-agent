import type { SessionRecord } from '../api/types'

export function shortSessionId(sessionId: string): string {
  if (sessionId.length <= 12) {
    return sessionId
  }

  return `${sessionId.slice(0, 8)}…${sessionId.slice(-4)}`
}

export function sessionTitle(session: SessionRecord): string {
  return session.title?.trim() || shortSessionId(session.session_id)
}

export function sessionPath(sessionId: string): string {
  return `/c/${encodeURIComponent(sessionId)}`
}

export function sessionMatches(session: SessionRecord, search: string): boolean {
  const normalizedSearch = search.trim().toLocaleLowerCase()
  if (!normalizedSearch) {
    return true
  }

  return (
    session.title?.toLocaleLowerCase().includes(normalizedSearch) === true ||
    session.session_id.toLocaleLowerCase().startsWith(normalizedSearch)
  )
}
