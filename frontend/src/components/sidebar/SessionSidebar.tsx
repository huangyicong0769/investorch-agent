import { useEffect, useId, useRef, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, Plus, Search, X } from 'lucide-react'
import { useNavigate } from 'react-router-dom'

import { archiveSession, createSession, deleteSession } from '../../api/client'
import {
  bootstrapQueryOptions,
  queryKeys,
  sessionsQueryOptions,
  sessionStateQueryOptions,
} from '../../api/queries'
import type {
  BootstrapResponse,
  DeleteSessionResponse,
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
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
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
  const deleteDescriptionId = useId()
  const mobileCloseButtonRef = useRef<HTMLButtonElement>(null)
  const archivedTriggerRef = useRef<HTMLButtonElement>(null)
  const [search, setSearch] = useState('')
  const [archivedOpen, setArchivedOpen] = useState(false)
  const [deleteCandidate, setDeleteCandidate] = useState<SessionRecord | null>(null)
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

  const archiveMutation = useMutation({
    mutationFn: (session: SessionRecord) => archiveSession(session.session_id),
    onSuccess: async (response: SessionResponse) => {
      const archivedSession = response.session
      queryClient.setQueryData(queryKeys.session(archivedSession.session_id), response)
      queryClient.setQueryData<SessionStateResponse>(
        queryKeys.sessionState(archivedSession.session_id),
        (current) => (current ? { ...current, session: archivedSession } : current),
      )
      queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
        current
          ? {
              sessions: current.sessions.filter(
                (session) => session.session_id !== archivedSession.session_id,
              ),
            }
          : current,
      )
      queryClient.setQueryData<SessionListResponse>(queryKeys.archivedSessions(), (current) =>
        current
          ? {
              sessions: [
                archivedSession,
                ...current.sessions.filter(
                  (session) => session.session_id !== archivedSession.session_id,
                ),
              ],
            }
          : current,
      )
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
        current
          ? {
              ...current,
              sessions: current.sessions.filter(
                (session) => session.session_id !== archivedSession.session_id,
              ),
            }
          : current,
      )
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.sessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.archivedSessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.bootstrap() }),
      ])
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (session: SessionRecord) => deleteSession(session.session_id, { confirm: true }),
    onSuccess: async (response: DeleteSessionResponse, deletedSession: SessionRecord) => {
      const deletedSessionId = deletedSession.session_id
      queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
        current
          ? { sessions: current.sessions.filter((session) => session.session_id !== deletedSessionId) }
          : current,
      )
      queryClient.setQueryData<SessionListResponse>(queryKeys.archivedSessions(), (current) =>
        current
          ? { sessions: current.sessions.filter((session) => session.session_id !== deletedSessionId) }
          : current,
      )
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
        current
          ? {
              ...current,
              initial_session_id:
                current.initial_session_id === deletedSessionId && response.replacement_session_id
                  ? response.replacement_session_id
                  : current.initial_session_id,
              sessions: current.sessions.filter((session) => session.session_id !== deletedSessionId),
            }
          : current,
      )
      queryClient.removeQueries({ exact: true, queryKey: queryKeys.session(deletedSessionId) })
      queryClient.removeQueries({ exact: true, queryKey: queryKeys.sessionState(deletedSessionId) })
      queryClient.removeQueries({ exact: true, queryKey: queryKeys.sessionHistoryPages(deletedSessionId) })
      setDeleteCandidate(null)

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.sessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.archivedSessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.bootstrap() }),
      ])
      if (deletedSessionId === selectedSessionId) {
        const bootstrap = await queryClient.fetchQuery(bootstrapQueryOptions())
        navigate(sessionPath(response.replacement_session_id ?? bootstrap.initial_session_id))
        onMobileNavigate()
      }
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

      <ScrollArea
        className="mt-3 min-h-0 flex-1"
        viewportProps={{ className: '[&>div]:!block [&>div]:w-full [&>div]:min-w-0' }}
      >
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
                actionsDisabled={archiveMutation.isPending || deleteMutation.isPending}
                active={session.session_id === selectedSessionId}
                key={session.session_id}
                onArchive={(target) => archiveMutation.mutate(target)}
                onDelete={(target) => {
                  deleteMutation.reset()
                  setDeleteCandidate(target)
                }}
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
      <Dialog
        open={deleteCandidate !== null}
        onOpenChange={(open) => {
          if (!open && !deleteMutation.isPending) {
            setDeleteCandidate(null)
          }
        }}
      >
        <DialogContent
          aria-describedby={deleteDescriptionId}
          className="w-full max-w-[calc(100%-2rem)] gap-0 rounded-xl border border-border bg-card p-5 shadow-xl sm:max-w-sm"
          onEscapeKeyDown={(event) => {
            if (deleteMutation.isPending) {
              event.preventDefault()
            }
          }}
          onPointerDownOutside={(event) => {
            if (deleteMutation.isPending) {
              event.preventDefault()
            }
          }}
          overlayClassName="bg-black/25"
          showCloseButton={false}
        >
          <DialogTitle className="text-base font-semibold">Delete session?</DialogTitle>
          <p className="mt-2 text-sm text-muted-foreground" id={deleteDescriptionId}>
            {deleteCandidate
              ? `“${deleteCandidate.title?.trim() || deleteCandidate.session_id.slice(0, 8)}” and its timeline will be permanently deleted. This cannot be undone.`
              : null}
          </p>
          {deleteMutation.error ? (
            <p className="mt-3 text-sm text-red-700" role="alert">
              {errorMessage(deleteMutation.error, 'The session could not be deleted. Try again.')}
            </p>
          ) : null}
          <div className="mt-5 flex justify-end gap-2">
            <Button
              size={null}
              variant={null}
              className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-60"
              disabled={deleteMutation.isPending}
              onClick={() => setDeleteCandidate(null)}
              type="button"
            >
              Cancel
            </Button>
            <Button
              size={null}
              variant={null}
              className="rounded-lg bg-red-700 px-3 py-2 text-sm text-white hover:bg-red-800 disabled:opacity-60"
              disabled={!deleteCandidate || deleteMutation.isPending}
              onClick={() => {
                if (deleteCandidate) {
                  deleteMutation.mutate(deleteCandidate)
                }
              }}
              type="button"
            >
              {deleteMutation.isPending ? 'Deleting…' : 'Delete'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
