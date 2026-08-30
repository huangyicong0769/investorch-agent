import { useId, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'

import { archivedSessionsQueryOptions } from '../../api/queries'
import type { SessionRecord } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionMatches } from '../../lib/session'
import { SessionItem } from './SessionItem'

interface ArchivedSessionsDialogProps {
  onClose: () => void
  onSelect: (session: SessionRecord) => void
  open: boolean
  selectedSessionId: string | null
}

export function ArchivedSessionsDialog({
  onClose,
  onSelect,
  open,
  selectedSessionId,
}: ArchivedSessionsDialogProps) {
  const [search, setSearch] = useState('')
  const titleId = useId()
  const searchId = useId()
  const archivedQuery = useQuery({
    ...archivedSessionsQueryOptions(),
    enabled: open,
  })

  if (!open) {
    return null
  }

  const sessions = archivedQuery.data?.sessions.filter((session) => sessionMatches(session, search)) ?? []

  return (
    <div
      aria-labelledby={titleId}
      aria-modal="true"
      className="fixed inset-0 z-30 flex bg-black/25"
      onClick={(event) => {
        if (event.target === event.currentTarget) {
          onClose()
        }
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          onClose()
        }
      }}
      role="dialog"
    >
      <section className="ml-auto flex h-full w-full max-w-sm flex-col border-l border-border bg-card p-4 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold" id={titleId}>
            Archived sessions
          </h2>
          <button
            aria-label="Close archived sessions"
            className="rounded-lg p-2 hover:bg-muted"
            onClick={onClose}
            type="button"
          >
            <X aria-hidden="true" size={18} />
          </button>
        </div>

        <label className="sr-only" htmlFor={searchId}>
          Search archived sessions
        </label>
        <input
          autoFocus
          className="mt-4 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          id={searchId}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search archived"
          type="search"
          value={search}
        />

        <div className="mt-4 min-h-0 flex-1 overflow-y-auto">
          {archivedQuery.isPending ? (
            <p className="px-3 py-2 text-sm text-muted-foreground">Loading archived sessions…</p>
          ) : null}
          {archivedQuery.isError ? (
            <div className="px-3 py-2 text-sm" role="alert">
              <p className="text-red-700">
                {errorMessage(archivedQuery.error, 'Archived sessions could not be loaded.')}
              </p>
              <button className="mt-2 underline" onClick={() => void archivedQuery.refetch()} type="button">
                Retry
              </button>
            </div>
          ) : null}
          {archivedQuery.isSuccess && sessions.length === 0 ? (
            <p className="px-3 py-2 text-sm text-muted-foreground">No archived sessions found.</p>
          ) : null}
          <ul className="space-y-1">
            {sessions.map((session) => (
              <SessionItem
                active={session.session_id === selectedSessionId}
                archived
                key={session.session_id}
                onSelect={(selected) => {
                  onSelect(selected)
                  onClose()
                }}
                session={session}
              />
            ))}
          </ul>
        </div>
      </section>
    </div>
  )
}
