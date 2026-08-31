import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { PanelLeft } from 'lucide-react'
import { BrowserRouter, Link, Navigate, Outlet, Route, Routes, useMatch } from 'react-router-dom'

import { discardUnusedSession } from '../api/client'
import { bootstrapQueryOptions, queryKeys } from '../api/queries'
import type { BootstrapResponse, SessionListResponse } from '../api/types'
import { ConversationPage } from '../components/conversation/ConversationPage'
import { SessionSidebar } from '../components/sidebar/SessionSidebar'
import { errorMessage } from '../lib/errors'
import { sessionPath } from '../lib/session'
import { LiveWebSocketProvider } from '../websocket/LiveWebSocketProvider'
import { Button } from '@/components/ui/button'

function AppShell() {
  const queryClient = useQueryClient()
  const sessionMatch = useMatch('/c/:sessionId')
  const selectedSessionId = sessionMatch?.params.sessionId ?? null
  const previousSessionIdRef = useRef<string | null>(null)
  const selectedSessionIdRef = useRef(selectedSessionId)
  const sidebarButtonRef = useRef<HTMLButtonElement>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  selectedSessionIdRef.current = selectedSessionId

  const hideMobileSidebar = useCallback(() => setMobileSidebarOpen(false), [])

  const closeMobileSidebar = useCallback(() => {
    hideMobileSidebar()
    window.requestAnimationFrame(() => sidebarButtonRef.current?.focus())
  }, [hideMobileSidebar])

  useEffect(() => {
    hideMobileSidebar()
  }, [hideMobileSidebar, selectedSessionId])

  useEffect(() => {
    if (!mobileSidebarOpen) {
      return
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        closeMobileSidebar()
      }
    }
    document.addEventListener('keydown', closeOnEscape)
    return () => document.removeEventListener('keydown', closeOnEscape)
  }, [closeMobileSidebar, mobileSidebarOpen])

  useEffect(() => {
    const previousSessionId = previousSessionIdRef.current
    previousSessionIdRef.current = selectedSessionId
    if (previousSessionId === null || previousSessionId === selectedSessionId) {
      return
    }

    const controller = new AbortController()
    let cancelled = false

    void discardUnusedSession(previousSessionId, { signal: controller.signal })
      .then((response) => {
        if (cancelled || selectedSessionIdRef.current === previousSessionId || !response.discarded) {
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

    return () => {
      cancelled = true
      controller.abort()
    }
  }, [queryClient, selectedSessionId])

  return (
    <div className="flex min-h-dvh bg-background text-foreground">
      <Button
        size={null}
        variant={null}
        aria-controls="session-sidebar"
        aria-expanded={mobileSidebarOpen}
        aria-label="Open session sidebar"
        className="fixed left-3 top-3 z-20 rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
        onClick={() => setMobileSidebarOpen(true)}
        ref={sidebarButtonRef}
        tabIndex={mobileSidebarOpen ? -1 : 0}
        type="button"
      >
        <PanelLeft aria-hidden="true" size={19} />
      </Button>
      {mobileSidebarOpen ? (
        <button
          aria-hidden="true"
          className="fixed inset-0 z-30 bg-black/35 md:hidden"
          onClick={closeMobileSidebar}
          tabIndex={-1}
          type="button"
        />
      ) : null}
      <SessionSidebar
        mobileOpen={mobileSidebarOpen}
        onMobileClose={closeMobileSidebar}
        onMobileNavigate={hideMobileSidebar}
        selectedSessionId={selectedSessionId}
      />
      <main className="min-w-0 flex-1" inert={mobileSidebarOpen ? true : undefined}>
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
          <Button
            size={null}
            variant={null}
            className="mt-5 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
            onClick={() => void bootstrapQuery.refetch()}
            type="button"
          >
            Retry
          </Button>
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
