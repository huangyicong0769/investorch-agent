import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ArrowUp, LoaderCircle, MessageCircle, X } from 'lucide-react'
import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { askPortfolioAgent } from '../../api/client'
import { queryKeys } from '../../api/queries'
import type { ImageContent, SessionListResponse } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionPath } from '../../lib/session'
import { cn } from '../../lib/utils'
import { useImageConfig } from '../../config/WebConfigContext'
import type { DraftImage } from '../conversation/interaction'
import { ImageAttachments } from '../images/ImageAttachments'
import { Button } from '@/components/ui/button'
import { InputGroupTextarea } from '@/components/ui/input-group'

interface AskAgentControlProps {
  portfolioId: string
  portfolioName: string
}

export function AskAgentControl({ portfolioId, portfolioName }: AskAgentControlProps) {
  const navigate = useNavigate()
  const imageConfig = useImageConfig()
  const [images, setImages] = useState<DraftImage[]>([])
  const [readingImages, setReadingImages] = useState(false)
  const queryClient = useQueryClient()
  const composerId = useId()
  const collapsedButtonRef = useRef<HTMLButtonElement>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const requestIdRef = useRef<string | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [text, setText] = useState('')
  const askMutation = useMutation({
    mutationFn: (message: { text: string; images: ImageContent[] }) => {
      requestIdRef.current ??= crypto.randomUUID()
      return askPortfolioAgent(portfolioId, { request_id: requestIdRef.current, ...message })
    },
    onSuccess: async (response) => {
      requestIdRef.current = null
      setText('')
      setImages([])
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
    if ((!text.trim() && !images.length) || readingImages || askMutation.isPending) {
      return
    }
    askMutation.mutate({ text, images: images.map((image) => ({ image_url: image.imageUrl, media_type: image.mediaType, filename: image.filename, detail: imageConfig.default_detail })) })
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
    if (event.key === 'Escape' && !text.trim() && !images.length) {
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
        'w-full shrink-0 transition-[max-width] duration-200 ease-out motion-reduce:transition-none',
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
            className="w-full focus-visible:ring-inset"
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
        <div className={cn('min-h-0', !expanded && 'overflow-hidden')}>
          <form
            aria-label={`Ask Agent about ${portfolioName}`}
            className="w-full"
            id={composerId}
            onSubmit={handleSubmit}
          >
            <ImageAttachments toolbar={<div className="flex flex-1 justify-end">
              <Button aria-label={askMutation.isPending ? 'Sending…' : 'Send'} title="Send message" className="size-9 rounded-full" disabled={askMutation.isPending || readingImages || (!text.trim() && !images.length)} size="icon" type="submit">
                {askMutation.isPending ? <LoaderCircle aria-hidden="true" className="size-4 animate-spin" /> : <ArrowUp aria-hidden="true" className="size-5" strokeWidth={2.25} />}
              </Button>
            </div>} images={images} disabled={askMutation.isPending} onReadingChange={setReadingImages} onChange={(next) => { setImages(next); requestIdRef.current = null; askMutation.reset() }}>
              <InputGroupTextarea
                aria-label={`Message about ${portfolioName}`}
                className="max-h-40 min-h-16 px-4 pb-3 pr-12 pt-4 text-[15px] leading-6"
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
                className="absolute right-2 top-2 rounded-full text-muted-foreground"
                disabled={askMutation.isPending}
                onClick={collapseAndRestoreFocus}
                size="icon-sm"
                type="button"
                variant="ghost"
              >
                <X aria-hidden="true" />
              </Button>
            </ImageAttachments>
            {askMutation.isError ? (
              <p className="mt-2 text-xs text-destructive" role="alert">
                {errorMessage(askMutation.error, 'The message could not be sent. Your text is still here.')}
              </p>
            ) : null}
          </form>
        </div>
      </div>
    </div>
  )
}
