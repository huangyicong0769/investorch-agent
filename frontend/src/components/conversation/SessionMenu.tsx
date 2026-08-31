import { useId, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MoreHorizontal } from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { toast } from 'sonner'

import {
  ApiError,
  archiveSession,
  clearSession,
  compactSession,
  forkSession,
  renameSession,
  unarchiveSession,
} from '../../api/client'
import { queryKeys } from '../../api/queries'
import type {
  BootstrapResponse,
  ClearSessionResponse,
  CompactionResult,
  SessionListResponse,
  SessionRecord,
  SessionResponse,
  SessionStateResponse,
  TokenUsage,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionPath, shortSessionId } from '../../lib/session'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogTitle } from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

interface SessionMenuProps {
  session: SessionRecord
}

type LifecycleAction = 'archive' | 'clear' | 'compact' | 'fork' | 'unarchive'

type LifecycleResult =
  | { action: 'archive' | 'fork' | 'unarchive'; response: SessionResponse }
  | { action: 'clear'; response: ClearSessionResponse }
  | { action: 'compact'; response: CompactionResult }

function withoutSession(records: SessionRecord[], sessionId: string): SessionRecord[] {
  return records.filter((record) => record.session_id !== sessionId)
}

function upsertSession(records: SessionRecord[], replacement: SessionRecord): SessionRecord[] {
  return [replacement, ...withoutSession(records, replacement.session_id)]
}

function addUsage(current: TokenUsage, added: TokenUsage): TokenUsage {
  return {
    requests: current.requests + added.requests,
    input_tokens: current.input_tokens + added.input_tokens,
    cached_input_tokens: current.cached_input_tokens + added.cached_input_tokens,
    cache_write_input_tokens: current.cache_write_input_tokens + added.cache_write_input_tokens,
    output_tokens: current.output_tokens + added.output_tokens,
    reasoning_output_tokens: current.reasoning_output_tokens + added.reasoning_output_tokens,
    total_tokens: current.total_tokens + added.total_tokens,
    last_request_total_tokens: added.last_request_total_tokens ?? current.last_request_total_tokens,
  }
}

function lifecycleError(error: unknown): string {
  if (
    error instanceof ApiError &&
    error.code === 'compaction_failed' &&
    error.details.consistency_uncertain === true
  ) {
    return 'Context storage may be damaged. See system log.'
  }
  return errorMessage(error, 'The session action could not be completed. Try again.')
}

export function SessionMenu({ session }: SessionMenuProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const triggerRef = useRef<HTMLButtonElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [title, setTitle] = useState(session.title ?? '')
  const [validationError, setValidationError] = useState<string | null>(null)
  const dialogTitleId = useId()
  const titleInputId = useId()
  const archived = session.archived_at !== null

  const renameMutation = useMutation({
    mutationFn: (nextTitle: string) => renameSession(session.session_id, { title: nextTitle }),
    onSuccess: async (response: SessionResponse) => {
      const replacement = response.session
      queryClient.setQueryData(queryKeys.session(session.session_id), response)
      queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(session.session_id), (current) =>
        current ? { ...current, session: replacement } : current,
      )
      queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
        current ? { sessions: upsertSession(current.sessions, replacement) } : current,
      )
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
        current ? { ...current, sessions: upsertSession(current.sessions, replacement) } : current,
      )
      setRenameOpen(false)
      setValidationError(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.sessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.sessionState(session.session_id) }),
      ])
    },
  })

  const lifecycleMutation = useMutation<LifecycleResult, Error, LifecycleAction>({
    mutationFn: async (action) => {
      switch (action) {
        case 'archive':
          return { action, response: await archiveSession(session.session_id) }
        case 'clear':
          return { action, response: await clearSession(session.session_id, { confirm: true }) }
        case 'compact':
          return { action, response: await compactSession(session.session_id) }
        case 'fork':
          return { action, response: await forkSession(session.session_id) }
        case 'unarchive':
          return { action, response: await unarchiveSession(session.session_id) }
      }
    },
    onSuccess: async (result) => {
      setMenuOpen(false)

      if (result.action === 'compact') {
        queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(session.session_id), (current) =>
          current
            ? {
                ...current,
                presentation: {
                  ...current.presentation,
                  usage: addUsage(current.presentation.usage, result.response.usage),
                  main_context_tokens: result.response.changed
                    ? null
                    : current.presentation.main_context_tokens,
                },
              }
            : current,
        )
        if (result.response.changed) {
          toast.success('Session context compacted.')
        } else {
          toast.info('Session context is already empty or compacted.')
        }
        await queryClient.invalidateQueries({
          exact: true,
          queryKey: queryKeys.sessionState(session.session_id),
        })
        return
      }

      if (result.action === 'clear') {
        const replacementSessionId = result.response.replacement_session_id
        navigate(sessionPath(replacementSessionId))
        queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
          current ? { sessions: withoutSession(current.sessions, session.session_id) } : current,
        )
        queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
          current
            ? {
                ...current,
                initial_session_id:
                  current.initial_session_id === session.session_id
                    ? replacementSessionId
                    : current.initial_session_id,
                sessions: withoutSession(current.sessions, session.session_id),
              }
            : current,
        )
        queryClient.removeQueries({ exact: true, queryKey: queryKeys.session(session.session_id) })
        queryClient.removeQueries({ exact: true, queryKey: queryKeys.sessionState(session.session_id) })
        queryClient.removeQueries({ exact: true, queryKey: queryKeys.sessionHistoryPages(session.session_id) })
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.sessions() }),
          queryClient.invalidateQueries({ queryKey: queryKeys.bootstrap() }),
        ])
        return
      }

      const replacement = result.response.session
      queryClient.setQueryData(queryKeys.session(replacement.session_id), result.response)

      if (result.action === 'fork') {
        queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
          current ? { sessions: upsertSession(current.sessions, replacement) } : current,
        )
        queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
          current ? { ...current, sessions: upsertSession(current.sessions, replacement) } : current,
        )
        navigate(sessionPath(replacement.session_id))
      } else {
        const nowArchived = result.action === 'archive'
        queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(session.session_id), (current) =>
          current ? { ...current, session: replacement } : current,
        )
        queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
          current
            ? {
                sessions: nowArchived
                  ? withoutSession(current.sessions, replacement.session_id)
                  : upsertSession(current.sessions, replacement),
              }
            : current,
        )
        queryClient.setQueryData<SessionListResponse>(queryKeys.archivedSessions(), (current) =>
          current
            ? {
                sessions: nowArchived
                  ? upsertSession(current.sessions, replacement)
                  : withoutSession(current.sessions, replacement.session_id),
              }
            : current,
        )
        queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
          current
            ? {
                ...current,
                sessions: nowArchived
                  ? withoutSession(current.sessions, replacement.session_id)
                  : upsertSession(current.sessions, replacement),
              }
            : current,
        )
      }

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.sessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.archivedSessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.bootstrap() }),
        queryClient.invalidateQueries({
          exact: true,
          queryKey: queryKeys.sessionState(replacement.session_id),
        }),
      ])
    },
  })

  const openRename = () => {
    setTitle(session.title ?? '')
    setValidationError(null)
    renameMutation.reset()
    lifecycleMutation.reset()
    setMenuOpen(false)
    setRenameOpen(true)
  }

  const closeRename = () => {
    if (!renameMutation.isPending) {
      setRenameOpen(false)
      setValidationError(null)
    }
  }

  const submitRename = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const nextTitle = title.trim()
    if (!nextTitle) {
      setValidationError('Enter a session title.')
      return
    }
    setValidationError(null)
    renameMutation.mutate(nextTitle)
  }

  const runAction = (action: LifecycleAction) => {
    if (
      action === 'clear' &&
      typeof window !== 'undefined' &&
      !window.confirm('Clear this session and start a replacement session?')
    ) {
      return
    }
    lifecycleMutation.reset()
    lifecycleMutation.mutate(action)
  }

  const pendingAction = lifecycleMutation.isPending ? lifecycleMutation.variables : null

  return (
    <>
      <div className="relative">
        <DropdownMenu open={menuOpen} onOpenChange={setMenuOpen}>
          <DropdownMenuTrigger asChild>
            <Button
              size={null}
              variant={null}
              aria-expanded={menuOpen}
              aria-haspopup="menu"
              aria-label="Session actions"
              className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              ref={triggerRef}
              type="button"
            >
              <MoreHorizontal aria-hidden="true" size={18} />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            align="end"
            aria-label="Session actions"
            collisionPadding={12}
            onCloseAutoFocus={(event) => {
              if (renameOpen) {
                event.preventDefault()
              }
            }}
            sideOffset={6}
            className="z-20 min-w-48 rounded-lg border border-border bg-card p-1 shadow-lg"
          >
            {archived ? (
              <DropdownMenuItem
                className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted data-[disabled]:opacity-60"
                disabled={lifecycleMutation.isPending}
                onSelect={(event) => {
                  event.preventDefault()
                  runAction('unarchive')
                }}
              >
                {pendingAction === 'unarchive' ? 'Unarchiving…' : 'Unarchive'}
              </DropdownMenuItem>
            ) : (
              <>
                <DropdownMenuItem
                  className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted data-[disabled]:opacity-60"
                  disabled={lifecycleMutation.isPending}
                  onSelect={openRename}
                >
                  Rename
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted data-[disabled]:opacity-60"
                  disabled={lifecycleMutation.isPending}
                  onSelect={(event) => {
                    event.preventDefault()
                    runAction('fork')
                  }}
                >
                  {pendingAction === 'fork' ? 'Forking…' : 'Fork'}
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted data-[disabled]:opacity-60"
                  disabled={lifecycleMutation.isPending}
                  onSelect={(event) => {
                    event.preventDefault()
                    runAction('compact')
                  }}
                >
                  {pendingAction === 'compact' ? 'Compacting…' : 'Compact context'}
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted data-[disabled]:opacity-60"
                  disabled={lifecycleMutation.isPending}
                  onSelect={(event) => {
                    event.preventDefault()
                    runAction('archive')
                  }}
                >
                  {pendingAction === 'archive' ? 'Archiving…' : 'Archive'}
                </DropdownMenuItem>
                <DropdownMenuItem
                  className="w-full rounded-md px-3 py-2 text-left text-sm text-red-700 hover:bg-muted data-[disabled]:opacity-60"
                  disabled={lifecycleMutation.isPending}
                  onSelect={(event) => {
                    event.preventDefault()
                    runAction('clear')
                  }}
                >
                  {pendingAction === 'clear' ? 'Clearing…' : 'Clear session'}
                </DropdownMenuItem>
              </>
            )}

            {lifecycleMutation.error ? (
              <p className="max-w-64 px-3 py-2 text-xs text-red-700" role="alert">
                {lifecycleError(lifecycleMutation.error)}
              </p>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <Dialog
        open={renameOpen}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            closeRename()
            return
          }
          setRenameOpen(true)
        }}
      >
        <DialogContent
          aria-labelledby={dialogTitleId}
          onEscapeKeyDown={(event) => {
            if (renameMutation.isPending) {
              event.preventDefault()
            }
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault()
            triggerRef.current?.focus()
          }}
          onPointerDownOutside={(event) => {
            if (renameMutation.isPending) {
              event.preventDefault()
            }
          }}
          overlayClassName="bg-black/25"
          showCloseButton={false}
          className="w-full max-w-[calc(100%-2rem)] gap-0 border-0 bg-transparent p-0 shadow-none sm:max-w-sm"
        >
          <form
            className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-xl"
            onSubmit={submitRename}
          >
            <DialogTitle className="text-base font-semibold" id={dialogTitleId}>
              Rename session
            </DialogTitle>
            <p className="mt-1 text-xs text-muted-foreground">{shortSessionId(session.session_id)}</p>
            <label className="mt-4 block text-sm font-medium" htmlFor={titleInputId}>
              Session title
            </label>
            <input
              aria-describedby={validationError || renameMutation.error ? `${titleInputId}-error` : undefined}
              autoFocus
              className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              disabled={renameMutation.isPending}
              id={titleInputId}
              onChange={(event) => setTitle(event.target.value)}
              value={title}
            />
            {validationError || renameMutation.error ? (
              <p className="mt-2 text-sm text-red-700" id={`${titleInputId}-error`} role="alert">
                {validationError ?? errorMessage(renameMutation.error, 'The session could not be renamed. Try again.')}
              </p>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <Button
                size={null}
                variant={null}
                className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-60"
                disabled={renameMutation.isPending}
                onClick={closeRename}
                type="button"
              >
                Cancel
              </Button>
              <Button
                size={null}
                variant={null}
                className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-60"
                disabled={renameMutation.isPending}
                type="submit"
              >
                {renameMutation.isPending ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </form>
        </DialogContent>
      </Dialog>
    </>
  )
}
