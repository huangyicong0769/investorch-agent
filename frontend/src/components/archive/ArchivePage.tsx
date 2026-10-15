import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Archive, ArchiveRestore, ArrowUpRight, Search } from 'lucide-react'
import { Link } from 'react-router-dom'

import { unarchiveSession } from '../../api/client'
import { archivedSessionsQueryOptions } from '../../api/queries'
import { reconcileSessionArchive } from '../../api/sessionLifecycle'
import { sessionMatches, sessionPath, sessionTitle } from '../../lib/session'
import { ErrorNotice } from '../management/Feedback'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function ArchivePage() {
  const queryClient = useQueryClient()
  const [search, setSearch] = useState('')
  const [notice, setNotice] = useState('')
  const query = useQuery(archivedSessionsQueryOptions())
  const restore = useMutation({
    mutationFn: (sessionId: string) => unarchiveSession(sessionId),
    onSuccess: async ({ session }) => {
      await reconcileSessionArchive(queryClient, session)
      setNotice(`“${sessionTitle(session)}” restored to Sessions.`)
    },
  })
  const sessions = query.data?.sessions.filter((session) => sessionMatches(session, search)) ?? []

  return <section className="mx-auto w-full max-w-5xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
    <header>
      <h1 className="text-2xl font-semibold tracking-tight">Archive</h1>
      <p className="mt-2 text-sm text-muted-foreground">Browse archived conversations, or restore one to continue working.</p>
    </header>
    <div className="relative mt-8">
      <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-3 size-4 text-muted-foreground" />
      <Input aria-label="Search archived sessions" className="h-10 pl-9" onChange={(event) => setSearch(event.target.value)} placeholder="Search by title or session ID" type="search" value={search} />
    </div>
    <div className="mt-5 space-y-4">
      <ErrorNotice error={query.error ?? restore.error} fallback="Archived sessions could not be loaded or restored." />
      {query.isError ? <Button onClick={() => void query.refetch()} variant="outline">Retry</Button> : null}
      {notice ? <p className="text-sm text-muted-foreground" role="status">{notice}</p> : null}
      {query.isPending ? <p className="text-sm text-muted-foreground" role="status">Loading archived sessions…</p> : null}
      {query.isSuccess && sessions.length === 0 ? <div className="rounded-xl border border-dashed border-border px-6 py-12 text-center">
        <Archive aria-hidden="true" className="mx-auto mb-3 size-6 text-muted-foreground" />
        <h2 className="text-sm font-medium">{search.trim() ? 'No matching conversations' : 'No archived conversations'}</h2>
        <p className="mt-2 text-sm text-muted-foreground">{search.trim() ? 'Try another title or session ID.' : 'Conversations you archive will appear here.'}</p>
      </div> : null}
      <ul className="space-y-3">
        {sessions.map((session) => <li className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-4 sm:p-5" key={session.session_id}>
          <Link className="group min-w-0 flex-1 basis-48 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring/40" to={sessionPath(session.session_id)}>
            <div className="flex items-center gap-2"><span className="min-w-0 break-words text-sm font-semibold group-hover:text-primary">{sessionTitle(session)}</span><ArrowUpRight aria-hidden="true" className="size-4 shrink-0 text-muted-foreground" /></div>
            {session.archived_at ? <p className="mt-1.5 text-xs text-muted-foreground">Archived <time dateTime={session.archived_at}>{new Date(session.archived_at).toLocaleString()}</time></p> : null}
          </Link>
          <Button aria-label={`Restore ${sessionTitle(session)}`} disabled={restore.isPending} onClick={() => { setNotice(''); restore.mutate(session.session_id) }} size="sm" variant="outline"><ArchiveRestore aria-hidden="true" />{restore.isPending && restore.variables === session.session_id ? 'Restoring…' : 'Restore'}</Button>
        </li>)}
      </ul>
    </div>
  </section>
}
