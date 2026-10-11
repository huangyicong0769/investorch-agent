import { useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'

import { startSkillSession, type SkillAction } from '../../api/management'
import { queryKeys } from '../../api/queries'
import type { SessionListResponse } from '../../api/types'
import { sessionPath } from '../../lib/session'

export function useSkillWorkflow() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const requests = useRef(new Map<string, string>())
  return useMutation({
    mutationFn: ({ action, name }: { action: SkillAction; name?: string }) => {
      const identity = `${action}:${name ?? ''}`
      const requestId = requests.current.get(identity) ?? crypto.randomUUID()
      requests.current.set(identity, requestId)
      return startSkillSession({ request_id: requestId, action, ...(name ? { name } : {}) })
    },
    onSuccess: async (response, request) => {
      requests.current.delete(`${request.action}:${request.name ?? ''}`)
      queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) => ({
        sessions: [response.session, ...(current?.sessions.filter((item) => item.session_id !== response.session.session_id) ?? [])],
      }))
      queryClient.setQueryData(queryKeys.session(response.session.session_id), { session: response.session })
      navigate(sessionPath(response.session.session_id))
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
    },
  })
}
