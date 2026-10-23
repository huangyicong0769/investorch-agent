import type { QueryClient } from '@tanstack/react-query'
import type { SessionPresentationState } from './types'

const presentationQueryKey = (sessionId: string) => ['investorch', 'session', sessionId, 'presentation'] as const
interface PresentationCache {
  snapshot: SessionPresentationState
  retiredEpochs: string[]
}

export function cachedPresentation(client: QueryClient, sessionId: string) {
  return client.getQueryData<PresentationCache>(presentationQueryKey(sessionId))?.snapshot ?? null
}

// HTTP reads capture their starting snapshot. A late response from an old server
// must not replace a new process epoch received while that request was in flight.
export function reconcilePresentation(
  client: QueryClient,
  sessionId: string,
  incoming: SessionPresentationState,
  before: SessionPresentationState | null = cachedPresentation(client, sessionId),
): SessionPresentationState {
  const key = presentationQueryKey(sessionId)
  const cached = client.getQueryData<PresentationCache>(key)
  const current = cached?.snapshot
  if (current && (current.epoch === incoming.epoch
    ? current.revision >= incoming.revision
    : cached.retiredEpochs.includes(incoming.epoch) || current !== before)) return current
  const retiredEpochs = cached?.retiredEpochs ?? []
  client.setQueryData<PresentationCache>(key, {
    snapshot: incoming,
    retiredEpochs: current && current.epoch !== incoming.epoch ? [...retiredEpochs, current.epoch] : retiredEpochs,
  })
  return incoming
}
