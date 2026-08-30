import { queryOptions } from '@tanstack/react-query'

import {
  getArchivedSessions,
  getBootstrap,
  getDefaults,
  getProcesses,
  getSession,
  getSessionHistory,
  getSessionState,
  getSessions,
} from './client'

export const queryKeys = {
  all: ['qmt'] as const,
  archivedSessions: () => [...queryKeys.all, 'sessions', 'archived'] as const,
  bootstrap: () => [...queryKeys.all, 'bootstrap'] as const,
  defaults: () => [...queryKeys.all, 'defaults'] as const,
  processes: () => [...queryKeys.all, 'processes'] as const,
  session: (sessionId: string) => [...queryKeys.all, 'session', sessionId] as const,
  sessionHistory: (sessionId: string, beforeSeq?: number, limit?: number) =>
    [...queryKeys.all, 'session', sessionId, 'history', { beforeSeq, limit }] as const,
  sessionState: (sessionId: string) => [...queryKeys.all, 'session', sessionId, 'state'] as const,
  sessions: () => [...queryKeys.all, 'sessions'] as const,
}

export const bootstrapQueryOptions = () =>
  queryOptions({
    queryKey: queryKeys.bootstrap(),
    queryFn: ({ signal }) => getBootstrap({ signal }),
  })

export const sessionsQueryOptions = () =>
  queryOptions({
    queryKey: queryKeys.sessions(),
    queryFn: ({ signal }) => getSessions({ signal }),
  })

export const archivedSessionsQueryOptions = () =>
  queryOptions({
    queryKey: queryKeys.archivedSessions(),
    queryFn: ({ signal }) => getArchivedSessions({ signal }),
  })

export const sessionQueryOptions = (sessionId: string) =>
  queryOptions({
    queryKey: queryKeys.session(sessionId),
    queryFn: ({ signal }) => getSession(sessionId, { signal }),
  })

export const sessionStateQueryOptions = (sessionId: string) =>
  queryOptions({
    queryKey: queryKeys.sessionState(sessionId),
    queryFn: ({ signal }) => getSessionState(sessionId, { signal }),
  })

export const sessionHistoryQueryOptions = (sessionId: string, beforeSeq?: number, limit?: number) =>
  queryOptions({
    queryKey: queryKeys.sessionHistory(sessionId, beforeSeq, limit),
    queryFn: ({ signal }) => getSessionHistory(sessionId, { beforeSeq, limit, signal }),
  })

export const defaultsQueryOptions = () =>
  queryOptions({
    queryKey: queryKeys.defaults(),
    queryFn: ({ signal }) => getDefaults({ signal }),
  })

export const processesQueryOptions = () =>
  queryOptions({
    queryKey: queryKeys.processes(),
    queryFn: ({ signal }) => getProcesses({ signal }),
  })
