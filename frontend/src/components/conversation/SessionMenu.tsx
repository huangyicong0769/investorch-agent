import { useEffect, useId, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MoreHorizontal } from 'lucide-react'

import { renameSession } from '../../api/client'
import { queryKeys } from '../../api/queries'
import type {
  BootstrapResponse,
  SessionListResponse,
  SessionRecord,
  SessionResponse,
  SessionStateResponse,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { shortSessionId } from '../../lib/session'

interface SessionMenuProps {
  session: SessionRecord
}

function replaceSession(records: SessionRecord[], replacement: SessionRecord): SessionRecord[] {
  return records.map((record) => (record.session_id === replacement.session_id ? replacement : record))
}

export function SessionMenu({ session }: SessionMenuProps) {
  const queryClient = useQueryClient()
  const menuRef = useRef<HTMLDivElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)
  const [renameOpen, setRenameOpen] = useState(false)
  const [title, setTitle] = useState(session.title ?? '')
  const [validationError, setValidationError] = useState<string | null>(null)
  const dialogTitleId = useId()
  const titleInputId = useId()

  useEffect(() => {
    if (!menuOpen) {
      return
    }

    const closeOnOutsideClick = (event: PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) {
        setMenuOpen(false)
      }
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setMenuOpen(false)
      }
    }
    document.addEventListener('pointerdown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [menuOpen])

  const mutation = useMutation({
    mutationFn: (nextTitle: string) => renameSession(session.session_id, { title: nextTitle }),
    onSuccess: async (response: SessionResponse) => {
      const replacement = response.session
      queryClient.setQueryData(queryKeys.session(session.session_id), response)
      queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(session.session_id), (current) =>
        current ? { ...current, session: replacement } : current,
      )
      queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) =>
        current ? { sessions: replaceSession(current.sessions, replacement) } : current,
      )
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
        current ? { ...current, sessions: replaceSession(current.sessions, replacement) } : current,
      )
      setRenameOpen(false)
      setValidationError(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.sessions() }),
        queryClient.invalidateQueries({ queryKey: queryKeys.sessionState(session.session_id) }),
      ])
    },
  })

  const openRename = () => {
    setTitle(session.title ?? '')
    setValidationError(null)
    mutation.reset()
    setMenuOpen(false)
    setRenameOpen(true)
  }

  const closeRename = () => {
    if (!mutation.isPending) {
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
    mutation.mutate(nextTitle)
  }

  return (
    <>
      <div className="relative" ref={menuRef}>
        <button
          aria-expanded={menuOpen}
          aria-haspopup="menu"
          aria-label="Session actions"
          className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
          onClick={() => setMenuOpen((open) => !open)}
          type="button"
        >
          <MoreHorizontal aria-hidden="true" size={18} />
        </button>
        {menuOpen ? (
          <div
            aria-label="Session actions"
            className="absolute right-0 top-10 z-20 min-w-36 rounded-lg border border-border bg-card p-1 shadow-lg"
            role="menu"
          >
            <button
              className="w-full rounded-md px-3 py-2 text-left text-sm hover:bg-muted"
              onClick={openRename}
              role="menuitem"
              type="button"
            >
              Rename
            </button>
          </div>
        ) : null}
      </div>

      {renameOpen ? (
        <div
          aria-labelledby={dialogTitleId}
          aria-modal="true"
          className="fixed inset-0 z-40 flex items-center justify-center bg-black/25 p-4"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              closeRename()
            }
          }}
          onKeyDown={(event) => {
            if (event.key === 'Escape') {
              closeRename()
            }
          }}
          role="dialog"
        >
          <form
            className="w-full max-w-sm rounded-xl border border-border bg-card p-5 shadow-xl"
            onSubmit={submitRename}
          >
            <h2 className="text-base font-semibold" id={dialogTitleId}>
              Rename session
            </h2>
            <p className="mt-1 text-xs text-muted-foreground">{shortSessionId(session.session_id)}</p>
            <label className="mt-4 block text-sm font-medium" htmlFor={titleInputId}>
              Session title
            </label>
            <input
              aria-describedby={validationError || mutation.error ? `${titleInputId}-error` : undefined}
              autoFocus
              className="mt-2 w-full rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
              disabled={mutation.isPending}
              id={titleInputId}
              onChange={(event) => setTitle(event.target.value)}
              value={title}
            />
            {validationError || mutation.error ? (
              <p className="mt-2 text-sm text-red-700" id={`${titleInputId}-error`} role="alert">
                {validationError ?? errorMessage(mutation.error, 'The session could not be renamed. Try again.')}
              </p>
            ) : null}
            <div className="mt-5 flex justify-end gap-2">
              <button
                className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted disabled:opacity-60"
                disabled={mutation.isPending}
                onClick={closeRename}
                type="button"
              >
                Cancel
              </button>
              <button
                className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-60"
                disabled={mutation.isPending}
                type="submit"
              >
                {mutation.isPending ? 'Saving…' : 'Save'}
              </button>
            </div>
          </form>
        </div>
      ) : null}
    </>
  )
}
