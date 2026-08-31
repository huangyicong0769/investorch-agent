import { useId, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { X } from 'lucide-react'

import { archivedSessionsQueryOptions } from '../../api/queries'
import type { SessionRecord } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionMatches } from '../../lib/session'
import { Sheet, SheetClose, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { SessionItem } from './SessionItem'

interface ArchivedSessionsDialogProps {
  onClose: () => void
  onRestoreFocus: () => void
  onSelect: (session: SessionRecord) => void
  open: boolean
  selectedSessionId: string | null
}

export function ArchivedSessionsDialog({
  onClose,
  onRestoreFocus,
  onSelect,
  open,
  selectedSessionId,
}: ArchivedSessionsDialogProps) {
  const [search, setSearch] = useState('')
  const searchId = useId()
  const searchInputRef = useRef<HTMLInputElement>(null)
  const archivedQuery = useQuery({
    ...archivedSessionsQueryOptions(),
    enabled: open,
  })

  const sessions = archivedQuery.data?.sessions.filter((session) => sessionMatches(session, search)) ?? []

  return (
    <Sheet
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) {
          onClose()
        }
      }}
    >
      <SheetContent
        side="right"
        overlayClassName="bg-black/25"
        showCloseButton={false}
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          onRestoreFocus()
        }}
        onOpenAutoFocus={(event) => {
          event.preventDefault()
          searchInputRef.current?.focus()
        }}
        className="h-full w-full max-w-sm gap-0 bg-card p-4 shadow-xl"
      >
        <div className="flex items-center justify-between">
          <SheetTitle className="text-base font-semibold">
            Archived sessions
          </SheetTitle>
          <SheetClose asChild>
            <button
              aria-label="Close archived sessions"
              className="rounded-lg p-2 hover:bg-muted"
              type="button"
            >
              <X aria-hidden="true" size={18} />
            </button>
          </SheetClose>
        </div>

        <label className="sr-only" htmlFor={searchId}>
          Search archived sessions
        </label>
        <input
          className="mt-4 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
          id={searchId}
          onChange={(event) => setSearch(event.target.value)}
          placeholder="Search archived"
          ref={searchInputRef}
          type="search"
          value={search}
        />

        <ScrollArea className="mt-4 min-h-0 flex-1">
          <div>
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
        </ScrollArea>
      </SheetContent>
    </Sheet>
  )
}
