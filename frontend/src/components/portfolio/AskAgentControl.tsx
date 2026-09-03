import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MessageCircle, Send, X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { askPortfolioAgent } from '../../api/client'
import { queryKeys } from '../../api/queries'
import type { SessionListResponse } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionPath } from '../../lib/session'
import { cn } from '../../lib/utils'
import { Button } from '@/components/ui/button'

interface AskAgentControlProps {
  portfolioId: string
  portfolioName: string
}

export function AskAgentControl({ portfolioId, portfolioName }: AskAgentControlProps) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const composerId = useId()
  const collapsedButtonRef = useRef<HTMLButtonElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const requestIdRef = useRef<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [text, setText] = useState('')
  const askMutation = useMutation({
    mutationFn: (message: string) => {
      requestIdRef.current ??= crypto.randomUUID()
      return askPortfolioAgent(portfolioId, { request_id: requestIdRef.current, text: message })
    },
    onSuccess: async (response) => {
      requestIdRef.current = null
      setText('')
      setExpanded(false)
      queryClient.setQueryData<SessionListResponse>(queryKeys.sessions(), (current) => ({
        sessions: [
          response.session,
          ...(current?.sessions.filter((session) => session.session_id !== response.session.session_id) ?? []),
        ],
      }))
      queryClient.setQueryData(queryKeys.session(response.session.session_id), { session: response.session })
      navigate(sessionPath(response.session.session_id))
      await queryClient.invalidateQueries({ queryKey: queryKeys.sessions() })
    },
  })

  useEffect(() => {
    if (expanded) {
      textareaRef.current?.focus()
    }
  }, [expanded])

  const submit = () => {
    if (!text.trim() || askMutation.isPending) {
      return
    }
    askMutation.mutate(text)
  }

  const collapseAndRestoreFocus = () => {
    setExpanded(false)
    window.requestAnimationFrame(() => collapsedButtonRef.current?.focus())
  }

  const handleSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    submit()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Escape' && !text.trim()) {
      event.preventDefault()
      collapseAndRestoreFocus()
      return
    }
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit()
    }
  }

  return (
    <div
      className={cn(
        'w-full shrink-0 overflow-hidden transition-[max-width] duration-200 ease-out motion-reduce:transition-none',
        expanded ? 'sm:max-w-96' : 'sm:max-w-32',
      )}
    >
      <div
        aria-hidden={expanded}
        className={cn(
          'grid transition-[grid-template-rows,opacity] duration-150 ease-out motion-reduce:transition-none',
          expanded ? 'grid-rows-[0fr] opacity-0' : 'grid-rows-[1fr] opacity-100',
        )}
        inert={expanded ? true : undefined}
      >
        <div className="min-h-0 overflow-hidden">
          <Button
            aria-controls={composerId}
            aria-expanded={expanded}
            className="w-full"
            onClick={() => setExpanded(true)}
            ref={collapsedButtonRef}
            size="sm"
            type="button"
          >
            <MessageCircle aria-hidden="true" size={15} />
            Ask Agent
          </Button>
        </div>
      </div>

      <div
        aria-hidden={!expanded}
        className={cn(
          'grid transition-[grid-template-rows,opacity] duration-200 ease-out motion-reduce:transition-none',
          expanded ? 'grid-rows-[1fr] opacity-100' : 'grid-rows-[0fr] opacity-0',
        )}
        inert={expanded ? undefined : true}
      >
        <div className="min-h-0 overflow-hidden">
          <form
            aria-label={`Ask Agent about ${portfolioName}`}
            className="w-full rounded-xl border border-border bg-card p-3 shadow-sm"
            id={composerId}
            onSubmit={handleSubmit}
          >
            <div className="flex items-start gap-2">
              <textarea
                aria-label={`Message about ${portfolioName}`}
                className="min-h-16 min-w-0 flex-1 resize-y bg-transparent px-1 py-1 text-sm leading-5 outline-none placeholder:text-muted-foreground disabled:opacity-60"
                disabled={askMutation.isPending}
                onChange={(event) => {
                  setText(event.target.value)
                  if (askMutation.isError) {
                    askMutation.reset()
                    requestIdRef.current = null
                  }
                }}
                onKeyDown={handleKeyDown}
                placeholder="What would you like to know?"
                ref={textareaRef}
                rows={2}
                value={text}
              />
              <Button
                aria-label="Close Ask Agent"
                disabled={askMutation.isPending}
                onClick={collapseAndRestoreFocus}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <X aria-hidden="true" />
              </Button>
            </div>
            {askMutation.isError ? (
              <p className="mt-2 text-xs text-destructive" role="alert">
                {errorMessage(askMutation.error, 'The message could not be sent. Your text is still here.')}
              </p>
            ) : null}
            <div className="mt-2 flex justify-end">
              <Button disabled={askMutation.isPending || !text.trim()} size="sm" type="submit">
                <Send aria-hidden="true" size={14} />
                {askMutation.isPending ? 'Sending…' : 'Send'}
              </Button>
            </div>
          </form>
        </div>
      </div>
    </div>
  )
}
