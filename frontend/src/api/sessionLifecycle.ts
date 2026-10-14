import type { QueryClient } from '@tanstack/react-query'
import { ApiError } from './client'
import { queryKeys } from './queries'
import type { BootstrapResponse, SessionListResponse } from './types'

export function committedDeletionIds(error: unknown): string[] {
  const value = error instanceof ApiError ? error.details.deleted_session_ids : null
  return Array.isArray(value) ? value.filter((id): id is string => typeof id === 'string') : []
}

export async function removeDeletedSessionCaches(client: QueryClient, ids: string[]) {
  const deleted = new Set(ids)
  for (const key of [queryKeys.sessions(), queryKeys.archivedSessions()]) {
    client.setQueryData<SessionListResponse>(key, (current) => current ? {
      sessions: current.sessions.filter((session) => !deleted.has(session.session_id)),
    } : current)
  }
  client.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) => current ? {
    ...current,
    sessions: current.sessions.filter((session) => !deleted.has(session.session_id)),
    initial_session_id: current.initial_session_id && deleted.has(current.initial_session_id) ? null : current.initial_session_id,
  } : current)
  for (const id of ids) {
    await client.cancelQueries({ queryKey: queryKeys.session(id) })
    client.removeQueries({ queryKey: queryKeys.session(id) })
    await client.cancelQueries({ queryKey: queryKeys.sessionContext(id) })
    client.removeQueries({ queryKey: queryKeys.sessionContext(id) })
  }
  await Promise.all([
    client.invalidateQueries({ queryKey: queryKeys.sessions() }),
    client.invalidateQueries({ queryKey: queryKeys.archivedSessions() }),
    client.invalidateQueries({ queryKey: queryKeys.bootstrap() }),
    client.invalidateQueries({ queryKey: queryKeys.sessionContexts() }),
  ])
}
