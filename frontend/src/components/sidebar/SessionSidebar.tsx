import { useEffect, useId, useRef, useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Archive, BriefcaseBusiness, Plus, Puzzle, Search, Settings, X } from 'lucide-react'
import { NavLink, useNavigate } from 'react-router-dom'

import { archiveSession, createSession, deleteSession, cleanupDeletedSessions } from '../../api/client'
import { committedDeletionIds, reconcileSessionArchive, removeDeletedSessionCaches } from '../../api/sessionLifecycle'
import { DeletionPreview } from '../conversation/DeletionPreview'
import {
  queryKeys,
  sessionsQueryOptions,
  sessionStateQueryOptions,
  sessionDeletionPreviewQueryOptions,
} from '../../api/queries'
import type {
  DeleteSessionResponse,
  SessionListResponse,
  SessionRecord,
  SessionResponse,
  SessionStateResponse,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionMatches, sessionPath } from '../../lib/session'
import { cn } from '../../lib/utils'
import { SessionItem } from './SessionItem'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
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
  const [search, setSearch] = useState('')
  const [deleteCandidate, setDeleteCandidate] = useState<SessionRecord | null>(null)
  const deletePreview = useQuery({ ...sessionDeletionPreviewQueryOptions(deleteCandidate?.session_id ?? ''), enabled: Boolean(deleteCandidate) })
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
    onSuccess: (response: SessionResponse) => reconcileSessionArchive(queryClient, response.session),
  })

  const deleteMutation = useMutation({
    mutationFn: async ({ session, sessionIds, cleanupIds }: { session: SessionRecord; sessionIds?: string[]; cleanupIds?: string[] }) => {
      if (cleanupIds?.length) {
        await cleanupDeletedSessions(cleanupIds)
        return { deleted: true as const, session_id: session.session_id, replacement_session_id: null, deleted_session_ids: cleanupIds }
      }
      return deleteSession(session.session_id, { confirm: true, session_ids: sessionIds })
    },
    onSuccess: async (response: DeleteSessionResponse, { session: deletedSession }) => {
      const ids = response.deleted_session_ids ?? [deletedSession.session_id]
      setDeleteCandidate(null)
      if (selectedSessionId && ids.includes(selectedSessionId)) {
        navigate('/')
        onMobileNavigate()
      }
      await removeDeletedSessionCaches(queryClient, ids)
    },
    onError: async (error) => {
      const ids = committedDeletionIds(error)
      if (ids.length) {
        if (selectedSessionId && ids.includes(selectedSessionId)) navigate('/')
        await removeDeletedSessionCaches(queryClient, ids)
      }
    },
  })
  const cleanupIds = committedDeletionIds(deleteMutation.error)

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
        'fixed inset-y-0 z-40 flex h-dvh w-72 max-w-[calc(100vw-3rem)] shrink-0 flex-col border-r border-border/70 bg-sidebar px-3 py-5 shadow-xl md:sticky md:top-0 md:z-auto md:max-w-none md:self-start md:shadow-none',
        mobileOpen ? 'visible left-0' : 'invisible -left-full md:visible',
      )}
      id="session-sidebar"
    >
      <div className="flex items-center justify-between px-2">
        <div className="flex items-center gap-2.5 text-sm font-semibold tracking-tight"><span aria-hidden="true" className="flex size-8 items-center justify-center rounded-xl bg-primary text-xs font-bold text-primary-foreground">IO</span>InvestOrch Agent</div>
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
        className="mt-5 flex min-h-11 items-center justify-start gap-2 rounded-xl bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:opacity-60"
        disabled={createMutation.isPending}
        onClick={() => createMutation.mutate()}
        type="button"
      >
        <Plus aria-hidden="true" size={16} />
        {createMutation.isPending ? 'Creating…' : 'New'}
      </Button>
      {createMutation.isError ? (
        <p className="mt-2 px-1 text-xs text-destructive" role="alert">
          {errorMessage(createMutation.error, 'A new session could not be created.')}
        </p>
      ) : null}

      <NavLink
        className={({ isActive }) =>
          cn(
            'mt-2 flex items-center gap-2 min-h-10 rounded-lg px-3 py-2 text-sm outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40',
            isActive ? 'bg-accent font-medium text-accent-foreground' : 'text-muted-foreground',
          )
        }
        onClick={onMobileClose}
        to="/portfolios"
      >
        <BriefcaseBusiness aria-hidden="true" size={16} />
        Portfolios
      </NavLink>

      <NavLink className={({ isActive }) => cn('mt-1 flex items-center gap-2 min-h-10 rounded-lg px-3 py-2 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40', isActive ? 'bg-accent font-medium text-accent-foreground' : 'text-muted-foreground')} onClick={onMobileClose} to="/skills"><Puzzle aria-hidden="true" size={16} />Skills</NavLink>

      <div className="relative mt-5">
        <Search
          aria-hidden="true"
          className="pointer-events-none absolute left-3 top-3 text-muted-foreground"
          size={15}
        />
        <label className="sr-only" htmlFor={searchId}>
          Search sessions
        </label>
        <Input
          className="h-10 rounded-lg border-transparent bg-card/70 pl-9 shadow-none"
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
          <p className="mb-1 px-3 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            Sessions
          </p>
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
              <p className="text-destructive">{errorMessage(sessionsQuery.error, 'Sessions could not be loaded.')}</p>
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
        <NavLink className={({ isActive }) => cn('flex items-center gap-2 min-h-10 rounded-lg px-3 py-2 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40', isActive && 'bg-accent font-medium text-accent-foreground')} onClick={onMobileNavigate} to="/archive"><Archive aria-hidden="true" size={16} />Archive</NavLink>
        <NavLink className={({ isActive }) => cn('flex items-center gap-2 min-h-10 rounded-lg px-3 py-2 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40', isActive && 'bg-accent font-medium text-accent-foreground')} onClick={onMobileClose} to="/settings"><Settings aria-hidden="true" size={16} />Settings</NavLink>
        <NavLink className={({ isActive }) => cn('flex items-center gap-2 min-h-10 rounded-lg px-3 py-2 text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/40', isActive && 'bg-accent font-medium text-accent-foreground')} onClick={onMobileClose} to="/system"><Activity aria-hidden="true" size={16} />System Status</NavLink>
      </div>

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
          <DialogTitle className="text-base font-semibold">Delete session{deletePreview.data && deletePreview.data.sessions.length > 1 ? ` and ${deletePreview.data.sessions.length - 1} spawned descendants` : ''}?</DialogTitle>
          <p className="mt-2 text-sm text-muted-foreground" id={deleteDescriptionId}>
            {deleteCandidate
              ? `“${deleteCandidate.title?.trim() || deleteCandidate.session_id.slice(0, 8)}” and its timeline will be permanently deleted. This cannot be undone.`
              : null}
          </p>
          <DeletionPreview preview={deletePreview.data} pending={deletePreview.isPending} error={deletePreview.error} onRetry={() => void deletePreview.refetch()} onNavigate={() => setDeleteCandidate(null)} />
          {deleteMutation.error ? (
            <p className="mt-3 text-sm text-destructive" role="alert">
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
              variant="destructive"
              className="rounded-lg px-3 py-2 text-sm disabled:opacity-60"
              disabled={!deleteCandidate || deleteMutation.isPending || (!cleanupIds.length && (!deletePreview.data || Boolean(deletePreview.data.blocked.length)))}
              onClick={() => {
                if (deleteCandidate) {
                  deleteMutation.mutate({ session: deleteCandidate, sessionIds: deletePreview.data?.sessions.map((member) => member.session_id), cleanupIds })
                }
              }}
              type="button"
            >
              {deleteMutation.isPending ? 'Deleting…' : cleanupIds.length ? 'Retry cleanup' : 'Delete'}
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
