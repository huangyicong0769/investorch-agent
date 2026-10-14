import { infiniteQueryOptions, queryOptions, type QueryClient } from '@tanstack/react-query'
import type { RuntimeSnapshot, SessionContextEntry, SessionContextResponse } from './types'

import {
  getArchivedSessions,
  getBootstrap,
  getDefaults,
  getPortfolio,
  getPortfolioLedger,
  getPortfolios,
  getProcesses,
  getSession,
  getSessionContext,
  getSessionDeletionPreview,
  getSessionHistory,
  getSessionRelatedPortfolios,
  getSessionState,
  getSessions,
} from './client'

export const queryKeys = {
  all: ['investorch'] as const,
  archivedSessions: () => [...queryKeys.all, 'sessions', 'archived'] as const,
  bootstrap: () => [...queryKeys.all, 'bootstrap'] as const,
  defaults: () => [...queryKeys.all, 'defaults'] as const,
  portfolioRecords: () => [...queryKeys.all, 'portfolio'] as const,
  processes: () => [...queryKeys.all, 'processes'] as const,
  portfolio: (portfolioId: string) => [...queryKeys.portfolioRecords(), portfolioId] as const,
  portfolioLedger: (portfolioId: string) => [...queryKeys.portfolioRecords(), portfolioId, 'ledger'] as const,
  portfolios: () => [...queryKeys.all, 'portfolios'] as const,
  session: (sessionId: string) => [...queryKeys.all, 'session', sessionId] as const,
  sessionContexts: () => [...queryKeys.all, 'session-context'] as const,
  sessionContext: (sessionId: string) => [...queryKeys.sessionContexts(), sessionId] as const,
  sessionDeletionPreview: (sessionId: string) => [...queryKeys.all, 'session', sessionId, 'deletion-preview'] as const,
  sessionHistory: (sessionId: string, beforeSeq?: number, limit?: number) =>
    [...queryKeys.all, 'session', sessionId, 'history', { beforeSeq, limit }] as const,
  sessionHistoryPages: (sessionId: string) =>
    [...queryKeys.all, 'session', sessionId, 'history-pages'] as const,
  sessionState: (sessionId: string) => [...queryKeys.all, 'session', sessionId, 'state'] as const,
  sessionRelatedPortfolios: (sessionId: string) =>
    [...queryKeys.all, 'session', sessionId, 'related-portfolios'] as const,
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

export const portfoliosQueryOptions = () =>
  queryOptions({
    queryKey: queryKeys.portfolios(),
    queryFn: ({ signal }) => getPortfolios({ signal }),
  })

export const portfolioQueryOptions = (portfolioId: string) =>
  queryOptions({
    queryKey: queryKeys.portfolio(portfolioId),
    queryFn: ({ signal }) => getPortfolio(portfolioId, { signal }),
  })

export const portfolioLedgerQueryOptions = (portfolioId: string) =>
  queryOptions({
    queryKey: queryKeys.portfolioLedger(portfolioId),
    queryFn: ({ signal }) => getPortfolioLedger(portfolioId, { limit: 50, signal }),
  })

export const sessionRelatedPortfoliosQueryOptions = (sessionId: string) =>
  queryOptions({
    queryKey: queryKeys.sessionRelatedPortfolios(sessionId),
    queryFn: ({ signal }) => getSessionRelatedPortfolios(sessionId, { signal }),
  })

export const sessionQueryOptions = (sessionId: string) =>
  queryOptions({
    queryKey: queryKeys.session(sessionId),
    queryFn: ({ signal }) => getSession(sessionId, { signal }),
  })

// These listeners exist only for the duration of an HTTP read, including a first read without cached data.
type ContextReadEvent = RuntimeSnapshot | { kind: 'run_ended'; session_id: string }
const pendingContextReads = new Set<{ client: QueryClient; receive: (event: ContextReadEvent) => void }>()

export function recordContextEventDuringFetch(client: QueryClient, event: ContextReadEvent) {
  for (const read of pendingContextReads) {
    if (read.client === client) read.receive(event)
  }
}

export const sessionContextQueryOptions = (sessionId: string) => queryOptions({
  queryKey: queryKeys.sessionContext(sessionId),
  queryFn: async ({ signal, client, queryKey }) => {
    const before = client.getQueryData<SessionContextResponse>(queryKey)
    const received = new Map<string, RuntimeSnapshot>()
    const ended = new Set<string>()
    const read = { client, receive: (event: ContextReadEvent) => {
      if (event.kind === 'runtime_state') received.set(event.session_id, event)
      else ended.add(event.session_id)
    } }
    pendingContextReads.add(read)
    let response: SessionContextResponse
    try {
      do {
        ended.clear()
        response = await getSessionContext(sessionId, { signal })
        // A child's metadata may still be loading, so its end cannot always invalidate its parent yet.
        // Re-read Journal when an end arrived during this read instead of guessing its outcome from idle.
      } while (ended.has(sessionId) || response.children.some((child) => ended.has(child.session.session_id)))
    } finally {
      pendingContextReads.delete(read)
    }
    const current = client.getQueryData<SessionContextResponse>(queryKey)
    // A slow Journal read must not overwrite runtime updates received while it was in flight.
    const mergeLive = (entry: SessionContextEntry, previous?: SessionContextEntry, live?: SessionContextEntry) => {
      const runtime = received.get(entry.session.session_id) ?? live?.runtime
      if (!runtime || runtime === previous?.runtime) return entry
      const terminal = entry.status === 'completed' || entry.status === 'failed' || entry.status === 'cancelled'
      return {
        ...entry,
        runtime,
        status: runtime.run_phase ?? (runtime.queued_count || runtime.pending_steer_count ? 'pending' : terminal ? entry.status : 'idle'),
      } satisfies SessionContextEntry
    }
    return {
      ...response,
      ...mergeLive(response, before, current),
      children: response.children.map((child) => mergeLive(child,
        before?.children.find((entry) => entry.session.session_id === child.session.session_id),
        current?.children.find((entry) => entry.session.session_id === child.session.session_id),
      )),
    }
  },
})

export const sessionDeletionPreviewQueryOptions = (sessionId: string) => queryOptions({
  queryKey: queryKeys.sessionDeletionPreview(sessionId),
  queryFn: ({ signal }) => getSessionDeletionPreview(sessionId, { signal }),
  staleTime: 0,
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

export const sessionHistoryInfiniteQueryOptions = (sessionId: string, pageSize: number) =>
  infiniteQueryOptions({
    queryKey: queryKeys.sessionHistoryPages(sessionId),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam, signal }) =>
      getSessionHistory(sessionId, {
        beforeSeq: pageParam,
        limit: pageSize,
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
