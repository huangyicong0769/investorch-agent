import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { PanelLeft } from 'lucide-react'
import { createBrowserRouter, Link, Navigate, Outlet, RouterProvider, useLocation, useMatch } from 'react-router-dom'

import { discardUnusedSession } from '../api/client'
import { bootstrapQueryOptions, queryKeys } from '../api/queries'
import { setupQueryOptions } from '../api/management'
import { ImageConfigProvider, WebConfigProvider, useWebConfig } from '../config/WebConfigContext'
import type { BootstrapResponse, SessionListResponse } from '../api/types'
import { ConversationPage } from '../components/conversation/ConversationPage'
import { PortfolioDetailPage } from '../components/portfolio/PortfolioDetailPage'
import { PortfolioIndexPage } from '../components/portfolio/PortfolioIndexPage'
import { SessionSidebar } from '../components/sidebar/SessionSidebar'
import { LiveWebSocketProvider } from '../websocket/LiveWebSocketProvider'
import { Button } from '@/components/ui/button'
import { SetupPage } from '../components/setup/SetupPage'
import { SystemPage } from '../components/system/SystemPage'
import { ErrorNotice } from '../components/management/Feedback'

function AppShell() {
  const webConfig = useWebConfig()
  const queryClient = useQueryClient()
  const location = useLocation()
  const sessionMatch = useMatch('/c/:sessionId')
  const selectedSessionId = sessionMatch?.params.sessionId ?? null
  const previousSessionIdRef = useRef<string | null>(null)
  const selectedSessionIdRef = useRef(selectedSessionId)
  const sidebarButtonRef = useRef<HTMLButtonElement>(null)
  const mainRef = useRef<HTMLElement>(null)
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  selectedSessionIdRef.current = selectedSessionId

  const hideMobileSidebar = useCallback(() => setMobileSidebarOpen(false), [])

  const closeMobileSidebar = useCallback(() => {
    hideMobileSidebar()
    window.requestAnimationFrame(() => sidebarButtonRef.current?.focus())
  }, [hideMobileSidebar])

  useEffect(() => {
    hideMobileSidebar()
    const focusFrame = window.requestAnimationFrame(() => mainRef.current?.focus())
    return () => window.cancelAnimationFrame(focusFrame)
  }, [hideMobileSidebar, location.pathname])

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
      <main
        className="min-w-0 flex-1 outline-none"
        inert={mobileSidebarOpen ? true : undefined}
        ref={mainRef}
        tabIndex={-1}
      >
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

function ReadyBoundary() {
  const bootstrap = useQuery({ ...bootstrapQueryOptions(), retry: false })
  if (bootstrap.isPending) return <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground" role="status">Opening your workspace…</div>
  if (bootstrap.isError) return <div className="mx-auto max-w-xl space-y-4 px-6 py-16">
    <h1 className="text-xl font-semibold">Workspace could not open</h1>
    <ErrorNotice error={bootstrap.error} fallback="The application runtime is unavailable." />
    <Button onClick={() => void bootstrap.refetch()} type="button" variant="outline">Retry</Button>
    <SystemPage standalone />
  </div>
  return <ImageConfigProvider value={bootstrap.data.image_config}>
    <WebConfigProvider value={bootstrap.data.web_config}>
      <LiveWebSocketProvider><Outlet /></LiveWebSocketProvider>
    </WebConfigProvider>
  </ImageConfigProvider>
}

function StartupGate() {
  const state = useQuery(setupQueryOptions())
  const location = useLocation()
  if (state.isPending) return <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground" role="status">Checking workspace…</div>
  if (state.isError) return <div className="mx-auto max-w-xl space-y-4 px-6 py-16">
    <h1 className="text-xl font-semibold">Cannot reach InvestOrch</h1>
    <ErrorNotice error={state.error} fallback="The local server could not be reached." />
    <Button onClick={() => void state.refetch()} type="button" variant="outline">Retry</Button>
  </div>
  if (state.data.status === 'ready') return <ReadyBoundary />
  if (location.pathname === '/system') return <SystemPage standalone />
  if (state.data.status === 'recovery_required') return <div className="mx-auto max-w-2xl px-6 py-16">
    <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">InvestOrch Agent</p>
    <h1 className="mt-3 text-2xl font-semibold">Your workspace needs attention</h1>
    <p className="mt-4 rounded-xl border border-border bg-card p-5 text-sm leading-6">{state.data.error ?? 'Existing configuration or storage requires repair before InvestOrch can start.'}</p>
    <p className="mt-4 text-sm text-muted-foreground">Review local diagnostics for the repair steps. Keep your existing files and make an offline backup before making changes.</p>
    <div className="mt-6 flex flex-wrap gap-3"><Button asChild><Link to="/system">Open System Status</Link></Button><Button onClick={() => void state.refetch()} type="button" variant="outline">Check again</Button></div>
    <p className="mt-6 text-sm text-muted-foreground">Offline checks: <code>investorch doctor</code> · <code>investorch upgrade check</code></p>
  </div>
  if (location.pathname !== '/setup') return <Navigate replace to="/setup" />
  return <SetupPage />
}

const router = createBrowserRouter([
  {
    element: <StartupGate />,
    children: [
      { path: 'setup', element: <Navigate replace to="/" /> },
      {
        element: <AppShell />,
        children: [
          { index: true, element: <RootRoute /> },
          { path: 'c/:sessionId', element: <ConversationPage /> },
          { path: 'portfolios', element: <PortfolioIndexPage /> },
          { path: 'portfolios/:portfolioId', element: <PortfolioDetailPage /> },
          { path: 'system', element: <SystemPage /> },
          { path: '*', element: <NotFoundRoute /> },
        ],
      },
    ],
  },
])

export function AppRouter() {
  return <RouterProvider router={router} />
}
