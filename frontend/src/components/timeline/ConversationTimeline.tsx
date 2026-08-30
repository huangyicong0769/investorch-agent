import { useInfiniteQuery } from '@tanstack/react-query'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'

import { sessionHistoryInfiniteQueryOptions } from '../../api/queries'
import type {
  TimelineAssistantTurnViewModel,
  TimelineViewModel,
} from '../../lib/timeline/project'
import { projectTimeline } from '../../lib/timeline/project'
import { errorMessage } from '../../lib/errors'
import { cn } from '../../lib/utils'
import { ActivityGroup } from './ActivityGroup'
import { MarkdownMessage } from './MarkdownMessage'

interface ConversationTimelineProps {
  sessionId: string
  className?: string
}

interface ScrollMeasurement {
  height: number
  top: number
}

function AssistantTurn({ turn }: { turn: TimelineAssistantTurnViewModel }) {
  return (
    <article className="py-4" data-seq={turn.seq}>
      <div className="mb-2 text-xs font-medium text-muted-foreground">QMT Agent</div>
      <div className="min-w-0">
        {turn.content.map((content) =>
          content.type === 'activity' ? (
            <ActivityGroup group={content} key={content.id} />
          ) : (
            <MarkdownMessage key={content.id} text={content.text} />
          ),
        )}
      </div>
    </article>
  )
}

function TimelineItem({ item }: { item: TimelineViewModel }) {
  if (item.type === 'user' || item.type === 'steer') {
    return (
      <article className="flex justify-end py-3" data-seq={item.seq}>
        <div className="max-w-[85%]">
          <div className="mb-1 text-right text-xs font-medium text-muted-foreground">
            {item.type === 'steer' ? 'You · Steer' : 'You'}
          </div>
          <p className="whitespace-pre-wrap break-words rounded-2xl bg-muted px-4 py-3 text-sm leading-6">{item.text}</p>
        </div>
      </article>
    )
  }

  if (item.type === 'assistant') {
    return <AssistantTurn turn={item} />
  }

  return (
    <div className="py-2 text-center text-xs text-muted-foreground" data-seq={item.seq}>
      {item.text}
    </div>
  )
}

export function ConversationTimeline({ sessionId, className }: ConversationTimelineProps) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const sentinelRef = useRef<HTMLDivElement>(null)
  const sessionRef = useRef(sessionId)
  const initialScrollDoneRef = useRef(false)
  const prependRef = useRef<ScrollMeasurement | null>(null)
  const [observerAvailable, setObserverAvailable] = useState<boolean | null>(null)
  const historyQuery = useInfiniteQuery(sessionHistoryInfiniteQueryOptions(sessionId))
  const {
    data,
    error,
    fetchNextPage,
    hasNextPage = false,
    isError,
    isFetchingNextPage,
    isPending,
    isSuccess,
    refetch,
  } = historyQuery

  const records = useMemo(() => data?.pages.flatMap((page) => page.records) ?? [], [data])
  const timeline = useMemo(() => projectTimeline(records), [records])

  useLayoutEffect(() => {
    if (sessionRef.current === sessionId) {
      return
    }

    sessionRef.current = sessionId
    initialScrollDoneRef.current = false
    prependRef.current = null
    setObserverAvailable(null)
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [sessionId])

  const fetchOlder = useCallback(() => {
    const container = scrollRef.current
    if (!container || !hasNextPage || isFetchingNextPage) {
      return
    }

    prependRef.current = {
      height: container.scrollHeight,
      top: container.scrollTop,
    }
    void fetchNextPage().catch(() => {
      prependRef.current = null
    })
  }, [fetchNextPage, hasNextPage, isFetchingNextPage])

  useEffect(() => {
    const root = scrollRef.current
    const target = sentinelRef.current
    if (!root || !target || isPending) {
      return
    }

    if (typeof IntersectionObserver === 'undefined') {
      setObserverAvailable(false)
      return
    }

    let observer: IntersectionObserver
    try {
      observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) {
            fetchOlder()
          }
        },
        { root, rootMargin: '180px 0px 0px' },
      )
    } catch {
      setObserverAvailable(false)
      return
    }

    setObserverAvailable(true)
    observer.observe(target)
    return () => observer.disconnect()
  }, [fetchOlder, isPending, sessionId])

  useLayoutEffect(() => {
    const container = scrollRef.current
    if (!container || isPending) {
      return
    }

    const previous = prependRef.current
    if (previous) {
      container.scrollTop = previous.top + container.scrollHeight - previous.height
      prependRef.current = null
      return
    }

    if (isSuccess && !initialScrollDoneRef.current) {
      container.scrollTop = container.scrollHeight
      initialScrollDoneRef.current = true
    }
  }, [isPending, isSuccess, records.length, sessionId])

  return (
    <div
      aria-label="Conversation"
      className={cn('min-h-0 flex-1 overflow-y-auto outline-none focus-visible:ring-2 focus-visible:ring-primary', className)}
      ref={scrollRef}
      tabIndex={0}
    >
      <div className="mx-auto w-full max-w-4xl px-6 py-6">
        <div aria-hidden="true" className="h-px w-full" ref={sentinelRef} />

        {isPending ? (
          <p className="py-8 text-center text-sm text-muted-foreground" role="status">
            Loading conversation…
          </p>
        ) : null}

        {isError ? (
          <div className="py-8 text-center" role="alert">
            <p className="text-sm text-muted-foreground">
              {errorMessage(error, 'The conversation history could not be loaded.')}
            </p>
            <button
              className="mt-3 rounded-lg border border-border px-3 py-2 text-sm hover:bg-muted"
              onClick={() => void refetch()}
              type="button"
            >
              Retry
            </button>
          </div>
        ) : null}

        {!isPending && !isError && isFetchingNextPage ? (
          <p className="py-2 text-center text-xs text-muted-foreground" role="status">
            Loading older messages…
          </p>
        ) : null}

        {!isPending && !isError && observerAvailable === false && hasNextPage ? (
          <div className="flex justify-center py-2">
            <button
              className="rounded-lg border border-border px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted"
              disabled={isFetchingNextPage}
              onClick={fetchOlder}
              type="button"
            >
              Load older messages
            </button>
          </div>
        ) : null}

        {!isPending && !isError && timeline.length === 0 ? (
          <p className="py-24 text-center text-sm text-muted-foreground">Ask QMT Agent anything.</p>
        ) : null}

        {!isPending && !isError
          ? timeline.map((item) => <TimelineItem item={item} key={item.id} />)
          : null}
      </div>
    </div>
  )
}
