import { useQuery } from '@tanstack/react-query'
import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { bootstrapQueryOptions, sessionStateQueryOptions } from '../../api/queries'
import { errorMessage } from '../../lib/errors'
import { ApprovalCard } from '../approval/ApprovalCard'
import { Composer } from '../composer/Composer'
import { PlanCard } from '../plan/PlanCard'
import { QueueStrip } from '../queue/QueueStrip'
import { ConversationTimeline } from '../timeline/ConversationTimeline'
import { ConversationHeader } from './ConversationHeader'
import type { PendingDirectMessage } from './interaction'
import { Button } from '@/components/ui/button'

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
    <section className="flex h-dvh min-w-0 flex-col bg-background">
      <ConversationHeader
        contextWindowTokens={bootstrapQuery.data?.context_window_tokens ?? null}
        presentation={stateQuery.data.presentation}
        session={stateQuery.data.session}
      />
      <ConversationTimeline
        onPendingMessageCanonical={() => {
          if (pendingMessage) {
            clearPendingMessage(sessionId, pendingMessage.runId)
          }
        }}
        pendingMessage={pendingMessage}
        sessionId={sessionId}
      />
      <div className="mx-auto w-full max-w-4xl space-y-2 px-4 pb-4 pt-2 sm:px-6">
        <PlanCard presentation={stateQuery.data.presentation} runtime={stateQuery.data.runtime} />
        <QueueStrip
          archived={stateQuery.data.session.archived_at !== null}
          sessionId={sessionId}
          state={stateQuery.data}
        />
        <ApprovalCard approvals={stateQuery.data.pending_approvals} sessionId={sessionId} />
        <Composer
          archived={stateQuery.data.session.archived_at !== null}
          draft={draft}
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
            <Button
              className="rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted"
              onClick={onRetry}
              size={null}
              type="button"
              variant={null}
            >
              Retry
            </Button>
          ) : null}
          <Link className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground" to="/">
            Open initial session
          </Link>
        </div>
      </div>
    </div>
  )
}
