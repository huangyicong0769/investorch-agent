import { useState } from 'react'

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
    <button
      aria-label={copied ? 'Copied' : 'Copy'}
      className="rounded border border-border px-2 py-1 text-[11px] text-muted-foreground hover:bg-muted disabled:opacity-50"
      disabled={!value}
      onClick={() => void copy()}
      type="button"
    >
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
