import { queryOptions } from '@tanstack/react-query'
import { request } from './client'
import { queryKeys } from './queries'
import type { JsonObject, SessionModelView } from './types'

export const sessionModelQueryOptions = (sessionId: string) => queryOptions({
  queryKey: queryKeys.sessionModel(sessionId),
  queryFn: ({ signal }) => request<SessionModelView>(`/api/sessions/${encodeURIComponent(sessionId)}/model`, { signal }),
})
export function updateSessionModel(sessionId: string, body: JsonObject): Promise<SessionModelView> {
  return request(`/api/sessions/${encodeURIComponent(sessionId)}/model`, { method: 'PATCH', body })
}
