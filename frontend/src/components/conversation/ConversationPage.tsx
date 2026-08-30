import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'

import { sessionStateQueryOptions } from '../../api/queries'
import { errorMessage } from '../../lib/errors'
import { ConversationTimeline } from '../timeline/ConversationTimeline'
import { ConversationHeader } from './ConversationHeader'

export function ConversationPage() {
  const { sessionId = '' } = useParams<'sessionId'>()
  const stateQuery = useQuery({
    ...sessionStateQueryOptions(sessionId),
    enabled: Boolean(sessionId),
  })

  if (!sessionId) {
    return <ConversationError message="The session URL is incomplete." />
  }

  if (stateQuery.isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground" role="status">
        Loading conversation…
      </div>
    )
  }

  if (stateQuery.isError) {
    return (
      <ConversationError
        message={errorMessage(stateQuery.error, 'The session could not be loaded.')}
        onRetry={() => void stateQuery.refetch()}
      />
    )
  }

  return (
    <section className="flex h-screen min-w-0 flex-col bg-background">
      <ConversationHeader session={stateQuery.data.session} />
      <ConversationTimeline sessionId={sessionId} />
      {stateQuery.data.session.archived_at ? (
        <p className="mx-auto w-full max-w-4xl px-6 pb-5 text-center text-xs text-muted-foreground">
          This archived session is read-only.
        </p>
      ) : null}
    </section>
  )
}

interface ConversationErrorProps {
  message: string
  onRetry?: () => void
}

function ConversationError({ message, onRetry }: ConversationErrorProps) {
  return (
    <div className="flex min-h-screen items-center justify-center px-6">
      <div className="max-w-sm text-center">
        <h1 className="text-lg font-semibold">Session unavailable</h1>
        <p className="mt-2 text-sm text-muted-foreground" role="alert">
          {message}
        </p>
        <div className="mt-5 flex justify-center gap-2">
          {onRetry ? (
            <button
              className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted"
              onClick={onRetry}
              type="button"
            >
              Retry
            </button>
          ) : null}
          <Link className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground" to="/">
            Open initial session
          </Link>
        </div>
      </div>
    </div>
  )
}
