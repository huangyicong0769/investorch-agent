import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react'
import { useIsMutating, useMutation, useQueryClient } from '@tanstack/react-query'

import { getSessionHistory, getSessionState, resumeSessionQueue, sendMessage, stopSession } from '../../api/client'
import { queryKeys } from '../../api/queries'
import { useImageConfig, useWebConfig } from '../../config/WebConfigContext'
import type {
  BootstrapResponse,
  ImageContent,
  SessionPresentationState,
  SessionStateResponse,
  StopResponse,
  UserInputSubmission,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { historyNewestSeq, type HistoryInfiniteData } from '../../lib/timeline/history'
import type { ComposerDraft, PendingDirectMessage } from '../conversation/interaction'
import { UsagePopover } from '../usage/UsagePopover'
import { InputGroupTextarea } from '@/components/ui/input-group'
import { ComposerActionButton } from './ComposerActionButton'
import { applyQueueMutation, queueMutationKey } from '../queue/mutations'
import { ImageAttachments } from '../images/ImageAttachments'
import { RUN_DEFAULTS_MUTATION_KEY, RunControlMenu } from './RunControlsPopover'

interface ComposerProps {
  sessionId: string
  state: SessionStateResponse
  archived: boolean
  contextWindowTokens: number | null
  draft: ComposerDraft
  onDraftChange: (sessionId: string, update: (draft: ComposerDraft) => ComposerDraft) => void
  onDraftSubmitted: (sessionId: string, submittedDraft: ComposerDraft) => void
  onPendingDirectMessage: (sessionId: string, message: PendingDirectMessage) => void
  presentation: SessionPresentationState
}

interface SendVariables {
  sessionId: string
  text: string
  images: ImageContent[]
  draft: ComposerDraft
  baseNewestSeq: number | null
  baseSequenceKnown: boolean
}

interface StopVariables {
  sessionId: string
  runId: string | null
}

const NATIVE_ACTIONS = new Set(['/new', '/fork', '/stop', '/archive', '/unarchive', '/clear', '/compact'])

async function refreshSessionState(
  queryClient: ReturnType<typeof useQueryClient>,
  sessionId: string,
): Promise<void> {
  const stateKey = queryKeys.sessionState(sessionId)
  const previousState = queryClient.getQueryData<SessionStateResponse>(stateKey)
  const response = await getSessionState(sessionId)
  // Live events may have advanced this Session while the REST snapshot was in flight.
  if (queryClient.getQueryData(stateKey) !== previousState) return
  queryClient.setQueryData<SessionStateResponse>(stateKey, response)
  queryClient.setQueryData(queryKeys.session(sessionId), { session: response.session })
  queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
    current?.initial_session_id === sessionId ? { ...current, runtime: response.runtime } : current,
  )
}

export function Composer({
  sessionId,
  state,
  archived,
  contextWindowTokens,
  draft,
  onDraftChange,
  onDraftSubmitted,
  onPendingDirectMessage,
  presentation,
}: ComposerProps) {
  const webConfig = useWebConfig()
  const imageConfig = useImageConfig()
  const [readingImages, setReadingImages] = useState(false)
  const queryClient = useQueryClient()
  const queueMutationPending = useIsMutating({ mutationKey: queueMutationKey(sessionId) }) > 0
  const savingRunControls = useIsMutating({ mutationKey: RUN_DEFAULTS_MUTATION_KEY }) > 0
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
    mutationFn: ({ sessionId: targetSessionId, text, images }) => sendMessage(targetSessionId, { text, images }),
    onSuccess: (response, variables) => {
      onDraftSubmitted(variables.sessionId, variables.draft)
      if (variables.sessionId === activeSessionIdRef.current) {
        setNativeActionNotice(null)
        setRefreshError(null)
      }

      if (response.disposition === 'run_started') {
        onPendingDirectMessage(variables.sessionId, {
          text: variables.text,
          images: variables.images,
          runId: response.run_id,
          submittedAt: new Date().toISOString(),
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

  const resumeMutation = useMutation({
    mutationKey: queueMutationKey(sessionId),
    mutationFn: ({ sessionId: targetSessionId }: { sessionId: string }) => resumeSessionQueue(targetSessionId),
    onSuccess: (response, variables) => applyQueueMutation(queryClient, variables.sessionId, response),
  })

  const sendPendingForSession =
    sendMutation.isPending && sendMutation.variables?.sessionId === sessionId
  const preparingSendForSession = preparingSendSessionId === sessionId
  const stopPendingForSession =
    stopMutation.isPending && stopMutation.variables?.sessionId === sessionId
  const resumePendingForSession =
    resumeMutation.isPending && resumeMutation.variables?.sessionId === sessionId
  const resumeError = resumeMutation.variables?.sessionId === sessionId ? resumeMutation.error : null
  const sendError = sendMutation.variables?.sessionId === sessionId ? sendMutation.error : null
  const stopError = stopMutation.variables?.sessionId === sessionId ? stopMutation.error : null
  const stopping =
    (stopRequested && stopSessionIdRef.current === sessionId) || runtime.run_phase === 'stopping'

  const hasDraft = Boolean(draft.text.trim() || draft.images.length)
  const canResumeQueue = !active && runtime.queue_paused && runtime.queued_count > 0
  const action = stopping || stopPendingForSession ? 'stop'
    : sendPendingForSession || preparingSendForSession ? 'send'
      : resumePendingForSession ? 'resume'
        : hasDraft ? 'send' : active ? 'stop' : canResumeQueue ? 'resume' : 'send'
  const actionBusy = stopping || stopPendingForSession || sendPendingForSession || preparingSendForSession
    || resumePendingForSession || readingImages || (action !== 'stop' && savingRunControls)
    || (action === 'resume' && queueMutationPending)
  const actionLabel = stopping || stopPendingForSession ? 'Stopping…'
    : sendPendingForSession || preparingSendForSession ? 'Sending…'
      : resumePendingForSession ? 'Resuming queued follow-ups…'
        : readingImages ? 'Reading images…'
          : action !== 'stop' && savingRunControls ? 'Saving run controls…'
            : action === 'resume' && queueMutationPending ? 'Updating queued follow-ups…' : undefined

  useEffect(() => {
    setNativeActionNotice(null)
    setRefreshError(null)
    textareaRef.current?.focus()
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
    textarea.style.height = `${Math.min(textarea.scrollHeight, webConfig.composer_max_height_px)}px`
    textarea.style.overflowY = textarea.scrollHeight > webConfig.composer_max_height_px ? 'auto' : 'hidden'
  }, [draft, webConfig.composer_max_height_px])

  const submit = async () => {
    const targetSessionId = sessionId
    if (archived || stopping || stopPendingForSession || resumePendingForSession || sendPendingForSession || preparingSendForSession || readingImages || savingRunControls) {
      return
    }

    const text = draft.text.trim()
    if (!text && !draft.images.length) {
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
          const latest = await getSessionHistory(targetSessionId, { limit: webConfig.history_page_size })
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
        draft,
        images: draft.images.map((image) => ({ image_url: image.imageUrl, media_type: image.mediaType, filename: image.filename, detail: imageConfig.default_detail })),
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
    onDraftChange(sessionId, (current) => ({ ...current, text: event.target.value }))
    setNativeActionNotice(null)
    setRefreshError(null)
    if (sendMutation.isError) {
      sendMutation.reset()
    }
  }

  return (
    <form onSubmit={handleSubmit}>
      <ImageAttachments toolbar={
        <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <RunControlMenu field="permission_mode" />
          <div className="ml-auto flex items-center gap-1">
            <RunControlMenu field="reasoning_effort" />
            <UsagePopover contextWindowTokens={contextWindowTokens} presentation={presentation} />
            <ComposerActionButton
              action={action}
              busy={actionBusy}
              label={actionLabel}
              disabled={archived || (action === 'send' && !hasDraft)}
              type={action === 'send' ? 'submit' : 'button'}
              onClick={action === 'stop' ? () => {
                if (!archived && !actionBusy) stopMutation.mutate({ sessionId, runId: runtime.run_id })
              } : action === 'resume' ? () => {
                if (!archived && !actionBusy && canResumeQueue) resumeMutation.mutate({ sessionId })
              } : undefined}
            />
          </div>
        </div>
      } images={draft.images} disabled={archived || sendPendingForSession || preparingSendForSession} onReadingChange={setReadingImages} onChange={(images) => onDraftChange(sessionId, (current) => ({ ...current, images }))}>
        <InputGroupTextarea
          aria-label="Message InvestOrch Agent"
          className="max-h-40 min-h-16 resize-none overflow-hidden px-4 pb-3 pt-4 text-[15px] leading-6 sm:px-5"
          disabled={archived}
          onChange={handleDraftChange}
          onKeyDown={handleKeyDown}
          placeholder={archived ? 'Archived sessions are read-only.' : 'Message InvestOrch Agent'}
          ref={textareaRef}
          rows={1}
          value={draft.text}
        />
      </ImageAttachments>
      {nativeActionNotice ? (
        <p className="mt-1 text-xs text-muted-foreground" role="status">
          {nativeActionNotice}
        </p>
      ) : null}
      {sendError ? (
        <p className="mt-1 text-xs text-destructive" role="alert">
          {errorMessage(sendError, 'The message could not be sent. Your draft is still here.')}
        </p>
      ) : null}
      {stopError ? (
        <p className="mt-1 text-xs text-destructive" role="alert">
          {errorMessage(stopError, 'The run could not be stopped. Try again.')}
        </p>
      ) : null}
      {resumeError ? (
        <p className="mt-1 text-xs text-destructive" role="alert">
          {errorMessage(resumeError, 'Queued follow-ups could not be resumed. Try again.')}
        </p>
      ) : null}
      {refreshError ? (
        <p className="mt-1 text-xs text-destructive" role="alert">
          {errorMessage(refreshError, 'Session state could not be refreshed. Live updates will retry.')}
        </p>
      ) : null}
    </form>
  )
}
