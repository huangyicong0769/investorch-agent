import { useEffect, useState } from 'react'
import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query'

import { clearSessionQueue, removeQueuedInput } from '../../api/client'
import type { SessionStateResponse } from '../../api/types'
import { applyQueueMutation, queueMutationKey } from './mutations'
import { errorMessage } from '../../lib/errors'
import { MessageImages } from '../images/MessageImages'
import { InputAuthor } from '../conversation/InputAuthor'
import { Button } from '@/components/ui/button'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Dialog, DialogContent, DialogDescription, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

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

export function QueueStrip({ sessionId, state, archived }: QueueStripProps) {
  const queryClient = useQueryClient()
  const mutationPending = useIsMutating({ mutationKey: queueMutationKey(sessionId) }) > 0
  const [expanded, setExpanded] = useState(false)
  const [clearSessionId, setClearSessionId] = useState<string | null>(null)
  const queueCount = state.runtime.queued_count
  const paused = state.runtime.queue_paused

  const removeMutation = useMutation({
    mutationKey: queueMutationKey(sessionId),
    mutationFn: ({ sessionId: targetSessionId, queueId }: RemoveMutationVariables) =>
      removeQueuedInput(targetSessionId, queueId),
    onSuccess: (response, variables) => applyQueueMutation(queryClient, variables.sessionId, response),
  })
  const clearMutation = useMutation({
    mutationKey: queueMutationKey(sessionId),
    mutationFn: ({ sessionId: targetSessionId }: SessionMutationVariables) =>
      clearSessionQueue(targetSessionId, { confirm: true }),
    onSuccess: (response, variables) => {
      applyQueueMutation(queryClient, variables.sessionId, response)
      setClearSessionId((current) => current === variables.sessionId ? null : current)
    },
  })

  useEffect(() => {
    setExpanded(false)
    setClearSessionId(null)
  }, [sessionId])

  if (queueCount === 0 && !paused) {
    return null
  }

  const confirmingClear = clearSessionId === sessionId
  const clearError = clearMutation.variables?.sessionId === sessionId ? clearMutation.error : null
  const mutationError =
    (removeMutation.variables?.sessionId === sessionId ? removeMutation.error : null) ??
    (confirmingClear ? null : clearError)
  const clearPending = clearMutation.isPending && clearMutation.variables?.sessionId === sessionId
  const visibleQueue = queueCount === 0 ? [] : queueCount < state.queue.length ? state.queue.slice(-queueCount) : state.queue

  const clearQueue = () => {
    if (clearSessionId === sessionId && !archived && !mutationPending) {
      clearMutation.mutate({ sessionId: clearSessionId })
    }
  }

  return (
    <Dialog open={confirmingClear} onOpenChange={(open) => {
      if (!clearPending) setClearSessionId(open ? sessionId : null)
    }}>
      <Collapsible
        className="rounded-xl border border-border bg-card/80 px-3 py-2 text-sm"
        onOpenChange={setExpanded}
        open={expanded}
      >
        <div className="flex items-center justify-between gap-3">
          <span className="min-w-0 truncate text-muted-foreground" title={paused ? 'Resume queued follow-ups with the main composer button.' : undefined}>
            {paused ? `Queue paused · ${queueCount}` : `${queueCount} queued`}
          </span>
          <div className="flex shrink-0 items-center gap-2">
            <CollapsibleTrigger asChild>
              <Button
                size="xs"
                variant="ghost"
                aria-expanded={expanded}
                className="text-muted-foreground"
                disabled={mutationPending}
                type="button"
              >
                Manage
              </Button>
            </CollapsibleTrigger>
          </div>
        </div>

        <CollapsibleContent className="mt-2 border-t border-border pt-2" role="region">
          <p className="text-xs font-medium text-muted-foreground">Queued follow-ups</p>
          {paused && queueCount > 0 ? <p className="mt-1 text-xs text-muted-foreground">With an empty message, use Resume queued follow-ups below to start the next queued message.</p> : null}
          {visibleQueue.length > 0 ? (
            <ol aria-label="Queued follow-ups" className="mt-2 max-h-[min(16rem,30dvh)] space-y-1.5 overflow-y-auto overscroll-contain rounded-sm pr-1 outline-none focus-visible:ring-2 focus-visible:ring-ring" tabIndex={0}>
              {visibleQueue.map((item, index) => (
                <li className="flex items-start justify-between gap-2 text-xs" key={item.queue_id}>
                  <div className="min-w-0 break-words">
                    {item.origin ? <div className="mb-1 text-muted-foreground"><InputAuthor origin={item.origin} /></div> : null}
                    {index + 1}. {item.text}
                    <MessageImages images={item.images} />
                  </div>
                  <Button
                    size="xs"
                    variant="ghost"
                    className="text-muted-foreground"
                    disabled={archived || mutationPending}
                    onClick={() => removeMutation.mutate({ queueId: item.queue_id, sessionId })}
                    type="button"
                  >
                    Remove
                  </Button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="mt-2 text-xs text-muted-foreground">No queued follow-ups.</p>
          )}
          {visibleQueue.length > 0 ? (
            <DialogTrigger asChild>
              <Button
                size="xs"
                variant="outline"
                className="mt-3"
                disabled={archived || mutationPending}
                onClick={() => clearMutation.reset()}
                type="button"
              >
                {clearPending ? 'Clearing…' : 'Clear all'}
              </Button>
            </DialogTrigger>
          ) : null}
        </CollapsibleContent>

        {mutationError ? (
          <p className="mt-2 break-words text-xs text-destructive" role="alert">
            {errorMessage(mutationError, 'The queue could not be updated. Try again.')}
          </p>
        ) : null}
      </Collapsible>
      <DialogContent
        className="sm:max-w-sm"
        onEscapeKeyDown={(event) => { if (clearPending) event.preventDefault() }}
        onPointerDownOutside={(event) => { if (clearPending) event.preventDefault() }}
        showCloseButton={!clearPending}
      >
        <DialogTitle>Clear all queued follow-ups?</DialogTitle>
        <DialogDescription>This removes all queued follow-ups from this conversation. The current run will continue.</DialogDescription>
        {clearError ? <p className="break-words text-sm text-destructive" role="alert">{errorMessage(clearError, 'The queue could not be cleared. Try again.')}</p> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Button disabled={clearPending} onClick={() => setClearSessionId(null)} type="button" variant="outline">Cancel</Button>
          <Button disabled={archived || mutationPending} onClick={clearQueue} type="button" variant="destructive">{clearPending ? 'Clearing…' : 'Clear all'}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
