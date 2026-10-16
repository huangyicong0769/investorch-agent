import { useMutation, useQueryClient } from '@tanstack/react-query'
import { MessageCircle, X } from 'lucide-react'
import { useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useNavigate } from 'react-router-dom'

import { askPortfolioAgent } from '../../api/client'
import { queryKeys } from '../../api/queries'
import type { ImageContent, SessionListResponse } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { sessionPath } from '../../lib/session'
import { cn } from '../../lib/utils'
import { useImageConfig, useWebConfig } from '../../config/WebConfigContext'
import type { DraftImage } from '../conversation/interaction'
import { ImageAttachments } from '../images/ImageAttachments'
import { Button } from '@/components/ui/button'
import { InputGroupTextarea } from '@/components/ui/input-group'
import { ComposerActionButton } from '../composer/ComposerActionButton'

interface AskAgentControlProps {
  portfolioId: string
  portfolioName: string
}

export function AskAgentControl({ portfolioId, portfolioName }: AskAgentControlProps) {
  const navigate = useNavigate()
  const imageConfig = useImageConfig()
  const webConfig = useWebConfig()
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

  useLayoutEffect(() => {
    if (!expanded || !textareaRef.current) return
    const textarea = textareaRef.current
    textarea.style.height = 'auto'
    textarea.style.height = `${Math.min(textarea.scrollHeight, webConfig.composer_max_height_px)}px`
    textarea.style.overflowY = textarea.scrollHeight > webConfig.composer_max_height_px ? 'auto' : 'hidden'
  }, [expanded, text, webConfig.composer_max_height_px])

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
        'shrink-0',
        expanded ? 'w-full basis-full' : 'w-full sm:w-32',
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
            className="mx-auto w-full max-w-4xl sm:px-6"
            id={composerId}
            onSubmit={handleSubmit}
          >
            <div className="mb-2 flex items-center justify-between gap-3 px-1">
              <p className="text-xs text-muted-foreground">Ask about {portfolioName}</p>
              <Button aria-label="Close Ask Agent" className="rounded-full text-muted-foreground" disabled={askMutation.isPending} onClick={collapseAndRestoreFocus} size="icon-sm" type="button" variant="ghost"><X aria-hidden="true" /></Button>
            </div>
            <ImageAttachments toolbar={<div className="flex flex-1 justify-end">
              <ComposerActionButton action="send" busy={askMutation.isPending} disabled={askMutation.isPending || readingImages || (!text.trim() && !images.length)} type="submit" />
            </div>} images={images} disabled={askMutation.isPending} onReadingChange={setReadingImages} onChange={(next) => { setImages(next); requestIdRef.current = null; askMutation.reset() }}>
              <InputGroupTextarea
                aria-label={`Message about ${portfolioName}`}
                className="max-h-40 min-h-16 resize-none overflow-hidden px-4 pb-3 pt-4 text-[15px] leading-6 sm:px-5"
                disabled={askMutation.isPending}
                onChange={(event) => {
                  setText(event.target.value)
                  if (askMutation.isError) {
                    askMutation.reset()
                    requestIdRef.current = null
                  }
                }}
                onKeyDown={handleKeyDown}
                placeholder="Message InvestOrch Agent"
                ref={textareaRef}
                rows={1}
                value={text}
              />
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
