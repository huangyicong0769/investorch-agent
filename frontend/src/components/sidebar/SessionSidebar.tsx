import { useEffect, useId, useRef, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Plus, Search, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { createSession } from '../../api/client'
import { queryKeys, sessionsQueryOptions, sessionStateQueryOptions } from '../../api/queries'
import type {
  SessionListResponse,
  SessionRecord,
  SessionResponse,
  SessionStateResponse,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionMatches, sessionPath } from '../../lib/session'
import { cn } from '../../lib/utils'
import { ProcessesSheet } from '../processes/ProcessesSheet'
import { GlobalSettingsPopover } from '../settings/GlobalSettingsPopover'
import { ArchivedSessionsDialog } from './ArchivedSessionsDialog'
import { SessionItem } from './SessionItem'
import { Button } from '@/components/ui/button'
import { ScrollArea } from '@/components/ui/scroll-area'

interface SessionSidebarProps {
  mobileOpen: boolean
  onMobileClose: () => void
  onMobileNavigate: () => void
  selectedSessionId: string | null
}

export function SessionSidebar({
  mobileOpen,
  onMobileClose,
  onMobileNavigate,
  selectedSessionId,
}: SessionSidebarProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const searchId = useId()
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null)
  const archivedTriggerRef = useRef<HTMLButtonElement>(null)
  const [search, setSearch] = useState('')
  const [archivedOpen, setArchivedOpen] = useState(false)
  const sessionsQuery = useQuery(sessionsQueryOptions())
  const sessions = sessionsQuery.data?.sessions ?? []
  const stateQueries = useQueries({
    queries: sessions.map((session) => sessionStateQueryOptions(session.session_id)),
  })
  const selectedStateQuery = useQuery({
    ...sessionStateQueryOptions(selectedSessionId ?? ''),
    enabled: Boolean(selectedSessionId),
  })

  useEffect(() => {
    if (mobileOpen) {
      mobileCloseButtonRef.current?.focus()
    }
  }, [mobileOpen])

  const statesById = new Map<string, SessionStateResponse>()
  stateQueries.forEach((query, index) => {
    const session = sessions[index]
    if (session && query.data) {
      statesById.set(session.session_id, query.data)
    }
  })
  if (selectedStateQuery.data) {
    statesById.set(selectedStateQuery.data.session.session_id, selectedStateQuery.data)
  }

  const selectedArchived =
    selectedStateQuery.data?.session.archived_at &&
    !sessions.some((session) => session.session_id === selectedStateQuery.data?.session.session_id)
      ? selectedStateQuery.data.session
      : null
  const filteredSessions = sessions.filter((session) => sessionMatches(session, search))

  const createMutation = useMutation({
    mutationFn: createSession,
    onSuccess: async (response: SessionResponse) => {
      queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) => ({
        sessions: [
          response.session,
          ...(current?.sessions.filter((item) => item.session_id !== response.session.session_id) ?? []),
        ],
      }))
      queryClient.setQueryData(queryKeys.session(response.session.session_id), response)
      setSearch('')
      navigate(sessionPath(response.session.session_id))
      onMobileNavigate()
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
    },
  })

  const selectSession = (session: SessionRecord) => {
    navigate(sessionPath(session.session_id))
    if (session.session_id === selectedSessionId) {
      onMobileClose()
    } else {
      onMobileNavigate()
    }
  }

  return (
    <aside
      className={cn(
        'fixed inset-y-0 z-40 flex h-dvh w-72 max-w-[calc(100vw-3rem)] shrink-0 flex-col border-r border-border bg-card px-3 py-4 shadow-xl md:static md:z-auto md:h-screen md:max-w-none md:shadow-none',
        mobileOpen ? 'visible left-0' : 'invisible -left-full md:visible',
      )}
      id="session-sidebar"
    >
      <div className="flex items-center justify-between px-2">
        <div className="text-sm font-semibold">QMT Agent</div>
        <Button
          size={null}
          variant={null}
          aria-label="Close session sidebar"
          className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground md:hidden"
          onClick={onMobileClose}
          ref={mobileCloseButtonRef}
          type="button"
        >
          <X aria-hidden="true" size={18} />
        </Button>
      </div>
      <Button
        size={null}
        variant={null}
        className="mt-4 flex items-center justify-start gap-2 rounded-lg border border-border px-3 py-2 text-sm font-medium hover:bg-muted disabled:opacity-60"
        disabled={createMutation.isPending}
        onClick={() => createMutation.mutate()}
        type="button"
      >
        <Plus aria-hidden="true" size={16} />
        {createMutation.isPending ? 'Creating…' : 'New'}
      </Button>
      {createMutation.isError ? (
        <p className="mt-2 px-1 text-xs text-red-700" role="alert">
          {errorMessage(createMutation.error, 'A new session could not be created.')}
        </p>
      ) : null}

      <div className="relative mt-3">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-2.5 text-muted-foreground"
          size={15}
        />
        <label className="sr-only" htmlFor={searchId}>
          Search sessions
        </label>
        <input
          className="w-full rounded-lg border border-border bg-background py-2 pl-9 pr-3 text-sm outline-none focus:border-primary"
          id={searchId}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search"
          type="search"
          value={search}
        />
      </div>

      <ScrollArea className="mt-3 min-h-0 flex-1">
        <nav aria-label="Sessions">
          {selectedArchived ? (
            <div className="mb-3">
              <p className="mb-1 px-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                Selected archived
              </p>
              <ul>
                <SessionItem
                  active
                  archived
                  onSelect={selectSession}
                  session={selectedArchived}
                  state={statesById.get(selectedArchived.session_id)}
                />
              </ul>
            </div>
          ) : null}

          {sessionsQuery.isPending ? (
            <p className="px-3 py-2 text-sm text-muted-foreground">Loading sessions…</p>
          ) : null}
          {sessionsQuery.isError ? (
            <div className="px-3 py-2 text-sm" role="alert">
              <p className="text-red-700">{errorMessage(sessionsQuery.error, 'Sessions could not be loaded.')}</p>
              <Button
                size={null}
                variant={null}
                className="mt-2 underline"
                onClick={() => void sessionsQuery.refetch()}
                type="button"
              >
                Retry
              </Button>
            </div>
          ) : null}
          {sessionsQuery.isSuccess && filteredSessions.length === 0 ? (
            <p className="px-3 py-2 text-sm text-muted-foreground">No sessions found.</p>
          ) : null}
          <ul className="space-y-1">
            {filteredSessions.map((session) => (
              <SessionItem
                active={session.session_id === selectedSessionId}
                key={session.session_id}
                onSelect={selectSession}
                session={session}
                state={statesById.get(session.session_id)}
              />
            ))}
          </ul>
        </nav>
      </ScrollArea>

      <div className="mt-3 space-y-1 border-t border-border pt-3">
        <Button
          size={null}
          variant={null}
          className="flex w-full items-center justify-start gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-muted"
          onClick={() => setArchivedOpen(true)}
          ref={archivedTriggerRef}
          type="button"
        >
          <Archive aria-hidden="true" size={16} />
          Archived
        </Button>
        <GlobalSettingsPopover />
        <ProcessesSheet />
      </div>

      <ArchivedSessionsDialog
        onClose={() => setArchivedOpen(false)}
        onRestoreFocus={() => archivedTriggerRef.current?.focus()}
        onSelect={selectSession}
        open={archivedOpen}
        selectedSessionId={selectedSessionId}
      />
    </aside>
  )
}
