import { useCallback, useState } from 'react'

import { EMPTY_DRAFT, type ComposerDraft, type PendingDirectMessage } from './interaction'

// Owned by the app shell so navigation to management pages retains unsent input.
export function useConversationInteraction() {
  const [drafts, setDrafts] = useState<Map<string, ComposerDraft>>(() => new Map())
  const [pendingMessages, setPendingMessages] = useState<Map<string, PendingDirectMessage>>(() => new Map())
  const updateDraft = useCallback(
    (targetSessionId: string, update: (draft: ComposerDraft) => ComposerDraft) => {
      setDrafts((current) => {
        const nextDraft = update(current.get(targetSessionId) ?? EMPTY_DRAFT)
        const next = new Map(current)
        if (nextDraft.text || nextDraft.images.length) {
          next.set(targetSessionId, nextDraft)
        } else {
          next.delete(targetSessionId)
        }
        return next
      })
    },
    [],
  )
  const clearSubmittedDraft = useCallback((targetSessionId: string, submittedDraft: ComposerDraft) => {
    setDrafts((current) => {
      if (current.get(targetSessionId) !== submittedDraft) {
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

  return { drafts, pendingMessages, updateDraft, clearSubmittedDraft, setPendingMessage, clearPendingMessage }
}

export type ConversationInteraction = ReturnType<typeof useConversationInteraction>
