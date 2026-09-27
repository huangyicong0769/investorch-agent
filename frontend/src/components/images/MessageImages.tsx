import { useState } from 'react'
import type { ImageContent } from '../../api/types'
import { useImageConfig } from '../../config/WebConfigContext'
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from '../ui/dialog'
import { Button } from '../ui/button'

export function MessageImage({ image, fallbackLabel = 'Attached image', workspacePath }: { image: ImageContent; fallbackLabel?: string; workspacePath?: string }) {
  const config = useImageConfig()
  const [loadedUrl, setLoadedUrl] = useState<string | null>(null)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  const source = workspacePath === undefined ? image.image_url : `/api/workspace/image?path=${encodeURIComponent(workspacePath)}`
  const dataMime = /^data:([^;,]+)(?:;[^,]*)?,/i.exec(source)?.[1].toLowerCase()
  let hostname: string | null = null
  try {
    const url = new URL(source)
    if (url.protocol === 'https:' && url.hostname) hostname = url.hostname
  } catch { /* Invalid sources never become img elements. */ }
  const inline = Boolean(dataMime && config.renderable_mime_types.includes(dataMime))
  if (!inline && !hostname && workspacePath === undefined) return <span className="text-xs text-muted-foreground">Unsupported image source</span>
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
  const label = image.filename || fallbackLabel
  return (
    <Dialog>
      <DialogTrigger asChild>
        <button className="inline-block max-w-full overflow-hidden rounded-lg border border-border" type="button" aria-label={`Enlarge ${label}`}>
          <img src={source} alt={label} className={`${workspacePath === undefined ? 'max-h-40 max-w-52' : 'max-h-[32rem] max-w-full'} object-contain`} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailedUrl(source)} />
        </button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-[90vw]" aria-describedby={undefined}>
        <DialogTitle className="pr-6 text-sm">{label}</DialogTitle>
        <img src={source} alt={label} className="mx-auto max-h-[80vh] max-w-full object-contain" decoding="async" referrerPolicy="no-referrer" />
      </DialogContent>
    </Dialog>
  )
}

export function MessageImages({ images = [], fallbackLabel }: { images?: ImageContent[]; fallbackLabel?: string }) {
  if (!images.length) return null
  return <div className="my-2 flex flex-wrap items-start gap-2">{images.map((image, index) => <MessageImage key={index} image={image} fallbackLabel={fallbackLabel} />)}</div>
}
