import { useState } from 'react'
import type { ImageContent } from '../../api/types'
import { useImageConfig } from '../../config/WebConfigContext'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '../ui/dialog'
import { Button } from '../ui/button'

export function MessageImage({ image }: { image: ImageContent }) {
  const config = useImageConfig()
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const source = image.image_url
  const dataMime = /^data:([^;,]+)(?:;[^,]*)?,/i.exec(source)?.[1].toLowerCase()
  let hostname: string | null = null
  try {
    const url = new URL(source)
    if (url.protocol === 'https:' && url.hostname) hostname = url.hostname
  } catch { /* Invalid sources never become img elements. */ }
  const inline = Boolean(dataMime && config.renderable_mime_types.includes(dataMime))
  if (!inline && !hostname) return <span className="text-xs text-muted-foreground">Unsupported image source</span>
  if (hostname && loadedUrl !== source) {
    return (
      <span className="inline-flex flex-col gap-2 rounded-lg border border-border p-3 text-xs">
        <span>External image · {hostname}</span>
        {image.filename ? <span>{image.filename}</span> : null}
        <Button type="button" size="sm" variant="outline" title="Loading this image contacts the remote host" onClick={() => setLoadedUrl(source)}>Load image</Button>
      </span>
    )
  }
  if (failedUrl === source) return <span className="text-xs text-muted-foreground">Image could not be loaded.</span>
  const label = image.filename || 'Conversation image'
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button className="inline-block overflow-hidden rounded-lg border border-border" type="button" aria-label={`Enlarge ${label}`}>
          <img src={source} alt={label} className="max-h-40 max-w-52 object-contain" loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailedUrl(source)} />
        </button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[90vw]" aria-describedby={undefined}>
        <DialogTitle className="pr-6 text-sm">{label}</DialogTitle>
        <img src={source} alt={label} className="mx-auto max-h-[80vh] max-w-full object-contain" decoding="async" referrerPolicy="no-referrer" />
      </DialogContent>
    </Dialog>
  )
}

export function MessageImages({ images = [] }: { images?: ImageContent[] }) {
  if (!images.length) return null
  return <div className="my-2 flex flex-wrap items-start gap-2">{images.map((image, index) => <MessageImage key={index} image={image} />)}</div>
}
