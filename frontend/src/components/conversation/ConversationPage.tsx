import { useQuery } from '@tanstack/react-query'
import { Link, useOutletContext, useParams } from 'react-router-dom'

import {
  bootstrapQueryOptions,
  sessionStateQueryOptions,
} from '../../api/queries'
import { errorMessage } from '../../lib/errors'
import { ApprovalCard } from '../approval/ApprovalCard'
import { Composer } from '../composer/Composer'
import { QueueStrip } from '../queue/QueueStrip'
import { SessionContextCard } from './SessionContextCard'
import { ConversationTimeline } from '../timeline/ConversationTimeline'
import { ConversationHeader } from './ConversationHeader'
import { EMPTY_DRAFT } from './interaction'
import type { ConversationInteraction } from './useConversationInteraction'
import { Button } from '@/components/ui/button'

export function ConversationPage() {
  const { sessionId = '' } = useParams<'sessionId'>()
  const { drafts, pendingMessages, updateDraft, clearSubmittedDraft, setPendingMessage, clearPendingMessage } =
    useOutletContext<ConversationInteraction>()
  const stateQuery = useQuery({
    ...sessionStateQueryOptions(sessionId),
    enabled: Boolean(sessionId),
  })
  const bootstrapQuery = useQuery(bootstrapQueryOptions())
  const draft = drafts.get(sessionId) ?? EMPTY_DRAFT
  const pendingMessage = pendingMessages.get(sessionId) ?? null

  if (!sessionId) {
    return <ConversationError message="The session URL is incomplete." />
  }

  if (stateQuery.isError) {
    return (
      <ConversationError
        message={errorMessage(stateQuery.error, 'The session could not be loaded.')}
        onRetry={() => void stateQuery.refetch()}
      />
    )
  }

  if (stateQuery.isPending) {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-muted-foreground" role="status">
        Loading conversation…
      </div>
    )
  }

  return (
    <section data-conversation-layout className="relative flex h-dvh min-w-0 flex-col bg-background">
      <ConversationHeader session={stateQuery.data.session} context={<SessionContextCard session={stateQuery.data.session} runtime={stateQuery.data.runtime} presentation={stateQuery.data.presentation} />} />
      <ConversationTimeline
        onPendingMessageCanonical={() => {
          if (pendingMessage) {
            clearPendingMessage(sessionId, pendingMessage.runId)
          }
        }}
        pendingMessage={pendingMessage}
        runtime={stateQuery.data.runtime}
        sessionId={sessionId}
      />
      <div data-conversation-width className="mx-auto w-full max-w-4xl space-y-2 px-4 pb-4 pt-2 sm:px-6">
        <QueueStrip
          archived={stateQuery.data.session.archived_at !== null}
          sessionId={sessionId}
          state={stateQuery.data}
        />
        <ApprovalCard approvals={stateQuery.data.pending_approvals} sessionId={sessionId} />
        <Composer
          key={sessionId}
          archived={stateQuery.data.session.archived_at !== null}
          contextWindowTokens={bootstrapQuery.data?.context_window_tokens ?? null}
          draft={draft}
          onDraftChange={updateDraft}
          onDraftSubmitted={clearSubmittedDraft}
          onPendingDirectMessage={setPendingMessage}
          presentation={stateQuery.data.presentation}
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
