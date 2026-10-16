import type { MouseEventHandler } from 'react'
import { ArrowUp, LoaderCircle, Play, Square } from 'lucide-react'

import { Button } from '@/components/ui/button'

interface ComposerActionButtonProps {
  action: 'send' | 'stop' | 'resume'
  busy?: boolean
  disabled?: boolean
  label?: string
  onClick?: MouseEventHandler<HTMLButtonElement>
  type?: 'button' | 'submit'
}

export function ComposerActionButton({ action, busy = false, disabled, label, onClick, type = 'button' }: ComposerActionButtonProps) {
  const accessibleLabel = label ?? (action === 'stop'
    ? busy ? 'Stopping…' : 'Stop'
    : action === 'resume'
      ? busy ? 'Resuming queued follow-ups…' : 'Resume queued follow-ups'
      : busy ? 'Sending…' : 'Send')

  return (
    <Button
      aria-label={accessibleLabel}
      title={accessibleLabel}
      className="ml-1 size-9 rounded-full"
      disabled={disabled || busy}
      onClick={onClick}
      size="icon"
      type={type}
    >
      {busy ? <LoaderCircle aria-hidden="true" className="size-4 animate-spin" />
        : action === 'stop' ? <Square aria-hidden="true" className="size-3.5 fill-current" />
          : action === 'resume' ? <Play aria-hidden="true" className="size-4 fill-current" />
            : <ArrowUp aria-hidden="true" className="size-5" strokeWidth={2.25} />}
    </Button>
  )
}
