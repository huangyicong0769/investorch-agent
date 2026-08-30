import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import {
  clearSessionQueue,
  removeQueuedInput,
  resumeSessionQueue,
} from '../../api/client'
import { queryKeys } from '../../api/queries'
import type {
  BootstrapResponse,
  QueueMutationResponse,
  SessionStateResponse,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'

interface QueueStripProps {
  sessionId: string
  state: SessionStateResponse
  archived: boolean
}

interface SessionMutationVariables {
  sessionId: string
}

interface RemoveMutationVariables extends SessionMutationVariables {
  queueId: string
}

function applyQueueMutation(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string,
  response: QueueMutationResponse,
): void {
  queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(sessionId), (current) =>
    current ? { ...current, runtime: response.runtime, queue: response.queue } : current,
  )
  queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
    current?.initial_session_id === sessionId ? { ...current, runtime: response.runtime } : current,
  )
}

export function QueueStrip({ sessionId, state, archived }: QueueStripProps) {
  const queryClient = useQueryClient()
  const [expanded, setExpanded] = useState(false)
  const queueCount = state.runtime.queued_count
  const paused = state.runtime.queue_paused

  const removeMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId, queueId }: RemoveMutationVariables) =>
      removeQueuedInput(targetSessionId, queueId),
    onSuccess: (response, variables) => applyQueueMutation(queryClient, variables.sessionId, response),
  })
  const clearMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId }: SessionMutationVariables) =>
      clearSessionQueue(targetSessionId, { confirm: true }),
    onSuccess: (response, variables) => applyQueueMutation(queryClient, variables.sessionId, response),
  })
  const resumeMutation = useMutation({
    mutationFn: ({ sessionId: targetSessionId }: SessionMutationVariables) =>
      resumeSessionQueue(targetSessionId),
    onSuccess: (response, variables) => applyQueueMutation(queryClient, variables.sessionId, response),
  })

  useEffect(() => {
    setExpanded(false)
  }, [sessionId])

  if (queueCount === 0 && !paused) {
    return null
  }

  const mutationError =
    (removeMutation.variables?.sessionId === sessionId ? removeMutation.error : null) ??
    (clearMutation.variables?.sessionId === sessionId ? clearMutation.error : null) ??
    (resumeMutation.variables?.sessionId === sessionId ? resumeMutation.error : null)
  const removePending = removeMutation.isPending && removeMutation.variables?.sessionId === sessionId
  const clearPending = clearMutation.isPending && clearMutation.variables?.sessionId === sessionId
  const resumePending = resumeMutation.isPending && resumeMutation.variables?.sessionId === sessionId
  const mutationPending = removePending || clearPending || resumePending
  const visibleQueue = queueCount < state.queue.length ? state.queue.slice(-queueCount) : state.queue

  const clearQueue = () => {
    if (typeof window !== 'undefined' && !window.confirm('Clear all queued follow-ups?')) {
      return
    }
    clearMutation.mutate({ sessionId })
  }

  return (
    <div className="rounded-xl border border-border bg-card/80 px-3 py-2 text-sm">
      <div className="flex items-center justify-between gap-3">
        <span className="min-w-0 truncate text-muted-foreground">
          {paused ? `Queue paused · ${queueCount}` : `${queueCount} queued`}
        </span>
        <div className="flex shrink-0 items-center gap-2">
          {paused ? (
            <button
              className="rounded-md px-2 py-1 text-xs font-medium hover:bg-muted disabled:opacity-60"
              disabled={archived || mutationPending}
              onClick={() => resumeMutation.mutate({ sessionId })}
              type="button"
            >
              {resumePending ? 'Resuming…' : 'Resume'}
            </button>
          ) : null}
          <button
            aria-expanded={expanded}
            className="rounded-md px-2 py-1 text-xs text-muted-foreground hover:bg-muted disabled:opacity-60"
            disabled={mutationPending}
            onClick={() => setExpanded((open) => !open)}
            type="button"
          >
            Manage
          </button>
        </div>
      </div>

      {expanded ? (
        <div className="mt-2 border-t border-border pt-2" role="region">
          <p className="text-xs font-medium text-muted-foreground">Queued follow-ups</p>
          {visibleQueue.length > 0 ? (
            <ol className="mt-2 space-y-1.5">
              {visibleQueue.map((item, index) => (
                <li className="flex items-start justify-between gap-2 text-xs" key={item.queue_id}>
                  <span className="min-w-0 break-words">
                    {index + 1}. {item.text}
                  </span>
                  <button
                    className="shrink-0 rounded px-1.5 py-0.5 text-muted-foreground hover:bg-muted disabled:opacity-60"
                    disabled={archived || mutationPending}
                    onClick={() => removeMutation.mutate({ queueId: item.queue_id, sessionId })}
                    type="button"
                  >
                    Remove
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="mt-2 text-xs text-muted-foreground">No queued follow-ups.</p>
          )}
          {visibleQueue.length > 0 ? (
            <button
              className="mt-3 rounded-md border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-60"
              disabled={archived || mutationPending}
              onClick={clearQueue}
              type="button"
            >
              {clearPending ? 'Clearing…' : 'Clear all'}
            </button>
          ) : null}
        </div>
      ) : null}

      {mutationError ? (
        <p className="mt-2 text-xs text-red-700" role="alert">
          {errorMessage(mutationError, 'The queue could not be updated. Try again.')}
        </p>
      ) : null}
    </div>
  )
}
