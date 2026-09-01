import { infiniteQueryOptions, queryOptions } from '@tanstack/react-query'

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

export const HISTORY_PAGE_SIZE = 200

export const queryKeys = {
  all: ['investorch'] as const,
  archivedSessions: () => [...queryKeys.all, 'sessions', 'archived'] as const,
  bootstrap: () => [...queryKeys.all, 'bootstrap'] as const,
  defaults: () => [...queryKeys.all, 'defaults'] as const,
  processes: () => [...queryKeys.all, 'processes'] as const,
  session: (sessionId: string) => [...queryKeys.all, 'session', sessionId] as const,
  sessionHistory: (sessionId: string, beforeSeq?: number, limit?: number) =>
    [...queryKeys.all, 'session', sessionId, 'history', { beforeSeq, limit }] as const,
  sessionHistoryPages: (sessionId: string) =>
    [...queryKeys.all, 'session', sessionId, 'history-pages'] as const,
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

export const sessionHistoryInfiniteQueryOptions = (sessionId: string) =>
  infiniteQueryOptions({
    queryKey: queryKeys.sessionHistoryPages(sessionId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getSessionHistory(sessionId, {
        beforeSeq: pageParam,
        limit: HISTORY_PAGE_SIZE,
        signal,
      }),
    getNextPageParam: (lastPage) =>
      lastPage.has_older && lastPage.oldest_seq !== null ? lastPage.oldest_seq : undefined,
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
