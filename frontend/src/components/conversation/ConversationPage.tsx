import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { bootstrapQueryOptions, sessionStateQueryOptions } from '../../api/queries'
import { errorMessage } from '../../lib/errors'
import { Composer } from '../composer/Composer'
import { QueueStrip } from '../queue/QueueStrip'
import { ConversationTimeline } from '../timeline/ConversationTimeline'
import { ConversationHeader } from './ConversationHeader'
import type { PendingDirectMessage } from './interaction'

export function ConversationPage() {
  const { sessionId = '' } = useParams<'sessionId'>()
  const [drafts, setDrafts] = useState<Map<string, string>>(() => new Map())
  const [pendingMessages, setPendingMessages] = useState<Map<string, PendingDirectMessage>>(() => new Map())
  const stateQuery = useQuery({
    ...sessionStateQueryOptions(sessionId),
    enabled: Boolean(sessionId),
  })
  const bootstrapQuery = useQuery(bootstrapQueryOptions())
  const draft = drafts.get(sessionId) ?? ''
  const pendingMessage = pendingMessages.get(sessionId) ?? null
  const updateDraft = useCallback(
    (targetSessionId: string, nextDraft: string) => {
      setDrafts((current) => {
        const next = new Map(current)
        if (nextDraft) {
          next.set(targetSessionId, nextDraft)
        } else {
          next.delete(targetSessionId)
        }
        return next
      })
    },
    [],
  )
  const clearSubmittedDraft = useCallback((targetSessionId: string, submittedText: string) => {
    setDrafts((current) => {
      if (current.get(targetSessionId)?.trim() !== submittedText) {
        return current
      }
      const next = new Map(current)
      next.delete(targetSessionId)
      return next
    })
  }, [])
  const setPendingMessage = useCallback(
    (targetSessionId: string, message: PendingDirectMessage) => {
      setPendingMessages((current) => new Map(current).set(targetSessionId, message))
    },
    [],
  )
  const clearPendingMessage = useCallback((targetSessionId: string, runId: string) => {
    setPendingMessages((current) => {
      if (current.get(targetSessionId)?.runId !== runId) {
        return current
      }
      const next = new Map(current)
      next.delete(targetSessionId)
      return next
    })
  }, [])

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
      <ConversationTimeline
        onPendingMessageCanonical={() => {
          if (pendingMessage) {
            clearPendingMessage(sessionId, pendingMessage.runId)
          }
        }}
        pendingMessage={pendingMessage}
        sessionId={sessionId}
      />
      <div className="mx-auto w-full max-w-4xl space-y-2 px-6 pb-4 pt-2">
        <QueueStrip
          archived={stateQuery.data.session.archived_at !== null}
          sessionId={sessionId}
          state={stateQuery.data}
        />
        <Composer
          archived={stateQuery.data.session.archived_at !== null}
          draft={draft}
          futureFollowUpBehavior={bootstrapQuery.data?.defaults.follow_up_behavior ?? null}
          onDraftChange={updateDraft}
          onDraftSubmitted={clearSubmittedDraft}
          onPendingDirectMessage={setPendingMessage}
          sessionId={sessionId}
          state={stateQuery.data}
        />
      </div>
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
