import type { QueryClient } from '@tanstack/react-query'

import { queryKeys } from '../../api/queries'
import type { BootstrapResponse, QueueMutationResponse, SessionStateResponse } from '../../api/types'

export const queueMutationKey = (sessionId: string) => ['session-queue', sessionId] as const

export function applyQueueMutation(queryClient: QueryClient, sessionId: string, response: QueueMutationResponse): void {
  queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(sessionId), (current) =>
    current ? { ...current, runtime: response.runtime, queue: response.queue } : current,
  )
  queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
    current?.initial_session_id === sessionId ? { ...current, runtime: response.runtime } : current,
  )
}
