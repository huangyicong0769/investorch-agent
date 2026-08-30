import { useQuery } from '@tanstack/react-query'
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useMatch } from 'react-router-dom'

import { bootstrapQueryOptions } from '../api/queries'
import { ConversationPage } from '../components/conversation/ConversationPage'
import { SessionSidebar } from '../components/sidebar/SessionSidebar'
import { errorMessage } from '../lib/errors'
import { sessionPath } from '../lib/session'
import { LiveWebSocketProvider } from '../websocket/LiveWebSocketProvider'

function AppShell() {
  const sessionMatch = useMatch('/c/:sessionId')
  const selectedSessionId = sessionMatch?.params.sessionId ?? null

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
