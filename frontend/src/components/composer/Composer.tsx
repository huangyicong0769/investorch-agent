import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { getSessionHistory, getSessionState, sendMessage, stopSession } from '../../api/client'
import { HISTORY_PAGE_SIZE, queryKeys } from '../../api/queries'
import type {
  BootstrapResponse,
  FollowUpBehavior,
  SessionStateResponse,
  StopResponse,
  UserInputSubmission,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { historyNewestSeq, type HistoryInfiniteData } from '../../lib/timeline/history'
import type { PendingDirectMessage } from '../conversation/interaction'

interface ComposerProps {
  sessionId: string
  state: SessionStateResponse
  futureFollowUpBehavior: FollowUpBehavior | null
  archived: boolean
  draft: string
  onDraftChange: (sessionId: string, draft: string) => void
  onDraftSubmitted: (sessionId: string, submittedText: string) => void
  onPendingDirectMessage: (sessionId: string, message: PendingDirectMessage) => void
}

interface SendVariables {
  sessionId: string
  text: string
  baseNewestSeq: number | null
  baseSequenceKnown: boolean
}

interface StopVariables {
  sessionId: string
  runId: string | null
}

const MAX_TEXTAREA_HEIGHT = 160
const NATIVE_ACTIONS = new Set(['/new', '/fork', '/stop', '/archive', '/unarchive', '/clear', '/compact'])

function displayBehavior(behavior: FollowUpBehavior | null): string {
  if (behavior === null) {
    return '—'
  }
  return behavior === 'steer' ? 'Steer' : 'Queue'
}

async function refreshSessionState(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string,
): Promise<void> {
  const response = await getSessionState(sessionId)
  queryClient.setQueryData<SessionStateResponse>(queryKeys.sessionState(sessionId), response)
  queryClient.setQueryData(queryKeys.session(sessionId), { session: response.session })
  queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
    current?.initial_session_id === sessionId ? { ...current, runtime: response.runtime } : current,
  )
}

export function Composer({
  sessionId,
  state,
  futureFollowUpBehavior,
  archived,
  draft,
  onDraftChange,
  onDraftSubmitted,
  onPendingDirectMessage,
}: ComposerProps) {
  const queryClient = useQueryClient()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const stopRunIdRef = useRef<string | null>(null)
  const stopSessionIdRef = useRef(sessionId)
  const activeSessionIdRef = useRef(sessionId)
  activeSessionIdRef.current = sessionId
  const [nativeActionNotice, setNativeActionNotice] = useState<string | null>(null)
  const [refreshError, setRefreshError] = useState<unknown>(null)
  const [stopRequested, setStopRequested] = useState(false)
  const [preparingSendSessionId, setPreparingSendSessionId] = useState<string | null>(null)

  const runtime = state.runtime
  const active = runtime.run_phase !== null

  const sendMutation = useMutation<UserInputSubmission, Error, SendVariables>({
    mutationFn: ({ sessionId: targetSessionId, text }) => sendMessage(targetSessionId, { text }),
    onSuccess: (response, variables) => {
      onDraftSubmitted(variables.sessionId, variables.text)
      if (variables.sessionId === activeSessionIdRef.current) {
        setNativeActionNotice(null)
        setRefreshError(null)
      }

      if (response.disposition === 'run_started') {
        onPendingDirectMessage(variables.sessionId, {
          text: variables.text,
          runId: response.run_id,
          baseNewestSeq: variables.baseNewestSeq,
          baseSequenceKnown: variables.baseSequenceKnown,
        })
      }

      void refreshSessionState(queryClient, variables.sessionId).catch((error: unknown) => {
        if (variables.sessionId === activeSessionIdRef.current) {
          setRefreshError(error)
        }
      })
    },
  })

  const stopMutation = useMutation<StopResponse, Error, StopVariables>({
    mutationFn: ({ sessionId: targetSessionId }) => stopSession(targetSessionId),
    onMutate: (variables) => {
      if (variables.sessionId !== activeSessionIdRef.current) {
        return
      }
      stopSessionIdRef.current = variables.sessionId
      stopRunIdRef.current = variables.runId
      setStopRequested(true)
    },
    onError: (_error, variables) => {
      if (variables.sessionId === activeSessionIdRef.current) {
        stopRunIdRef.current = null
        setStopRequested(false)
      }
    },
  })

  const sendPendingForSession =
    sendMutation.isPending && sendMutation.variables?.sessionId === sessionId
  const preparingSendForSession = preparingSendSessionId === sessionId
  const stopPendingForSession =
    stopMutation.isPending && stopMutation.variables?.sessionId === sessionId
  const sendError = sendMutation.variables?.sessionId === sessionId ? sendMutation.error : null
  const stopError = stopMutation.variables?.sessionId === sessionId ? stopMutation.error : null
  const stopping =
    (stopRequested && stopSessionIdRef.current === sessionId) || runtime.run_phase === 'stopping'

  useEffect(() => {
    setNativeActionNotice(null)
    setRefreshError(null)
    if (stopSessionIdRef.current !== sessionId) {
      stopSessionIdRef.current = sessionId
      stopRunIdRef.current = null
      setStopRequested(false)
    }
  }, [sessionId])

  useEffect(() => {
    if (
      stopSessionIdRef.current === sessionId &&
      stopRunIdRef.current !== null &&
      (runtime.run_phase === null || runtime.run_id !== stopRunIdRef.current)
    ) {
      stopRunIdRef.current = null
      setStopRequested(false)
    }
  }, [runtime.run_id, runtime.run_phase, sessionId])

  useLayoutEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) {
      return
    }
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, MAX_TEXTAREA_HEIGHT)}px`
    textarea.style.overflowY = textarea.scrollHeight > MAX_TEXTAREA_HEIGHT ? 'auto' : 'hidden'
  }, [draft])

  const submit = async () => {
    const targetSessionId = sessionId
    if (archived || sendPendingForSession || preparingSendForSession) {
      return
    }

    const text = draft.trim()
    if (!text) {
      return
    }

    const command = text.split(/\s+/, 1)[0].toLowerCase()
    if (NATIVE_ACTIONS.has(command)) {
      setNativeActionNotice('This action is available in the Web interface.')
      return
    }

    setPreparingSendSessionId(targetSessionId)
    try {
      const history = queryClient.getQueryData<HistoryInfiniteData>(
        queryKeys.sessionHistoryPages(targetSessionId),
      )
      let baseNewestSeq = historyNewestSeq(history)
      let baseSequenceKnown = history !== undefined

      if (!baseSequenceKnown) {
        try {
          const latest = await getSessionHistory(targetSessionId, { limit: HISTORY_PAGE_SIZE })
          baseNewestSeq = latest.newest_seq
          baseSequenceKnown = true
        } catch {
          // Sending can still succeed when history is temporarily unavailable.
        }
      }

      if (activeSessionIdRef.current === targetSessionId) {
        setNativeActionNotice(null)
        setRefreshError(null)
      }
      sendMutation.mutate({
        sessionId: targetSessionId,
        text,
        baseNewestSeq,
        baseSequenceKnown,
      })
    } finally {
      setPreparingSendSessionId((current) => (current === targetSessionId ? null : current))
    }
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    void submit()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      void submit()
    }
  }

  const handleDraftChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    onDraftChange(sessionId, event.target.value)
    setNativeActionNotice(null)
    setRefreshError(null)
    if (sendMutation.isError) {
      sendMutation.reset()
    }
  }

  const followUpBehavior = active ? runtime.active_follow_up_behavior : futureFollowUpBehavior

  return (
    <form className="rounded-2xl border border-border bg-card p-3 shadow-sm" onSubmit={handleSubmit}>
      <textarea
        aria-label="Message QMT Agent"
        className="block max-h-40 min-h-10 w-full resize-none overflow-hidden bg-transparent px-1 py-1.5 text-sm leading-6 outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-60"
        disabled={archived}
        onChange={handleDraftChange}
        onKeyDown={handleKeyDown}
        placeholder={archived ? 'Archived sessions are read-only.' : 'Message QMT Agent'}
        ref={textareaRef}
        rows={1}
        value={draft}
      />
      {nativeActionNotice ? (
        <p className="mt-1 text-xs text-muted-foreground" role="status">
          {nativeActionNotice}
        </p>
      ) : null}
      {sendError ? (
        <p className="mt-1 text-xs text-red-700" role="alert">
          {errorMessage(sendError, 'The message could not be sent. Your draft is still here.')}
        </p>
      ) : null}
      {stopError ? (
        <p className="mt-1 text-xs text-red-700" role="alert">
          {errorMessage(stopError, 'The run could not be stopped. Try again.')}
        </p>
      ) : null}
      {refreshError ? (
        <p className="mt-1 text-xs text-red-700" role="alert">
          {errorMessage(refreshError, 'Session state could not be refreshed. Live updates will retry.')}
        </p>
      ) : null}
      <div className="mt-2 flex items-center justify-between gap-3 border-t border-border pt-2">
        <span className="text-xs text-muted-foreground">Follow-ups: {displayBehavior(followUpBehavior)}</span>
        <div className="flex items-center gap-2">
          {active ? (
            <button
              className="rounded-lg border border-border px-3 py-1.5 text-xs font-medium hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
              disabled={archived || stopping || stopPendingForSession}
              onClick={() => stopMutation.mutate({ sessionId, runId: runtime.run_id })}
              type="button"
            >
              {stopping || stopPendingForSession ? 'Stopping…' : 'Stop'}
            </button>
          ) : null}
          <button
            className="rounded-lg bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-60"
            disabled={archived || sendPendingForSession || preparingSendForSession || !draft.trim()}
            type="submit"
          >
            {sendPendingForSession || preparingSendForSession ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </form>
  )
}
