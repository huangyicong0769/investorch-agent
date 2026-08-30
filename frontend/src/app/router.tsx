import { useEffect, useRef } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useMatch } from 'react-router-dom'

import { discardUnusedSession } from '../api/client'
import { bootstrapQueryOptions, queryKeys } from '../api/queries'
import type { BootstrapResponse, SessionListResponse } from '../api/types'
import { ConversationPage } from '../components/conversation/ConversationPage'
import { SessionSidebar } from '../components/sidebar/SessionSidebar'
import { errorMessage } from '../lib/errors'
import { sessionPath } from '../lib/session'
import { LiveWebSocketProvider } from '../websocket/LiveWebSocketProvider'

function AppShell() {
  const queryClient = useQueryClient()
  const sessionMatch = useMatch('/c/:sessionId')
  const selectedSessionId = sessionMatch?.params.sessionId ?? null
  const previousSessionIdRef = useRef<string | null>(null)

  useEffect(() => {
    const previousSessionId = previousSessionIdRef.current
    previousSessionIdRef.current = selectedSessionId
    if (previousSessionId === null || previousSessionId === selectedSessionId) {
      return
    }

    void discardUnusedSession(previousSessionId)
      .then((response) => {
        if (!response.discarded) {
          return
        }
        queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
          current
            ? { sessions: current.sessions.filter((session) => session.session_id !== previousSessionId) }
            : current,
        )
        queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
          current
            ? { ...current, sessions: current.sessions.filter((session) => session.session_id !== previousSessionId) }
            : current,
        )
        queryClient.removeQueries({ exact: true, queryKey: queryKeys.session(previousSessionId) })
        queryClient.removeQueries({ exact: true, queryKey: queryKeys.sessionState(previousSessionId) })
        queryClient.removeQueries({ exact: true, queryKey: queryKeys.sessionHistoryPages(previousSessionId) })
        void queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
      })
      .catch(() => undefined)
  }, [queryClient, selectedSessionId])

  return (
    <div className="flex min-h-screen bg-background text-foreground">
      <SessionSidebar selectedSessionId={selectedSessionId} />
      <main className="min-w-0 flex-1">
        <Outlet />
      </main>
    </div>
  )
}

function RootRoute() {
  const bootstrapQuery = useQuery(bootstrapQueryOptions())

  if (bootstrapQuery.isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground" role="status">
        Loading QMT Agent…
      </div>
    )
  }

  if (bootstrapQuery.isError) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <div className="max-w-sm text-center">
          <h1 className="text-lg font-semibold">QMT Agent is unavailable</h1>
          <p className="mt-2 text-sm text-muted-foreground" role="alert">
            {errorMessage(bootstrapQuery.error, 'The application could not be initialized.')}
          </p>
          <button
            className="mt-5 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
            onClick={() => void bootstrapQuery.refetch()}
            type="button"
          >
            Retry
          </button>
        </div>
      </div>
    )
  }

  return <Navigate replace to={sessionPath(bootstrapQuery.data.initial_session_id)} />
}

function NotFoundRoute() {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="text-center">
        <h1 className="text-lg font-semibold">Page not found</h1>
        <Link className="mt-4 inline-block text-sm underline" to="/">
          Open QMT Agent
        </Link>
      </div>
    </div>
  )
}

export function AppRouter() {
  return (
    <BrowserRouter>
      <LiveWebSocketProvider>
        <Routes>
          <Route element={<AppShell />}>
            <Route index element={<RootRoute />} />
            <Route element={<ConversationPage />} path="c/:sessionId" />
            <Route element={<NotFoundRoute />} path="*" />
          </Route>
        </Routes>
      </LiveWebSocketProvider>
    </BrowserRouter>
  )
}
