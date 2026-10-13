import { useState } from 'react'

import { Button } from '@/components/ui/button'

interface CopyButtonProps {
  value: string
}

export function CopyButton({ value }: CopyButtonProps) {
  const [copied, setCopied] = useState(false)

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <Button
      aria-label={copied ? 'Copied' : 'Copy'}
      className="rounded border border-border px-2 py-1 text-[11px] font-normal text-muted-foreground hover:bg-muted disabled:opacity-50"
      disabled={!value}
      onClick={() => void copy()}
      size={null}
      type="button"
      variant={null}
    >
      {copied ? 'Copied' : 'Copy'}
    </Button>
  )
}
