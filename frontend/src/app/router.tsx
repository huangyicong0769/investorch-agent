import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { PanelLeft } from 'lucide-react'
import { BrowserRouter, Link, Outlet, Route, Routes, useMatch } from 'react-router-dom'

import { discardUnusedSession } from '../api/client'
import { queryKeys } from '../api/queries'
import { useWebConfig } from '../config/WebConfigContext'
import type { BootstrapResponse, SessionListResponse } from '../api/types'
import { ConversationPage } from '../components/conversation/ConversationPage'
import { SessionSidebar } from '../components/sidebar/SessionSidebar'
import { LiveWebSocketProvider } from '../websocket/LiveWebSocketProvider'
import { Button } from '@/components/ui/button'

function AppShell() {
  const webConfig = useWebConfig()
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

    const discardTimer = window.setTimeout(() => {
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
    }, webConfig.unused_session_discard_delay_ms)

    return () => {
      cancelled = true
      window.clearTimeout(discardTimer)
      controller.abort()
    }
  }, [queryClient, selectedSessionId, webConfig.unused_session_discard_delay_ms])

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
  return null
}

function NotFoundRoute() {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="text-center">
        <h1 className="text-lg font-semibold">Page not found</h1>
        <Link className="mt-4 inline-block text-sm underline" to="/">
          Open InvestOrch Agent
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
