import { useRef, useState, type PropsWithChildren } from 'react'
import { useImageConfig } from '../../config/WebConfigContext'
import type { DraftImage } from '../conversation/interaction'
import { MessageImage } from './MessageImages'
import { Button } from '../ui/button'

interface Props {
  images: DraftImage[]
  onChange: (images: DraftImage[]) => void
  onReadingChange?: (reading: boolean) => void
  disabled?: boolean
}

export function ImageAttachments({ images, onChange, onReadingChange, disabled = false, children }: PropsWithChildren<Props>) {
  const config = useImageConfig()
  const input = useRef<HTMLInputElement>(null)
  const currentImages = useRef(images)
  currentImages.current = images
  const reading = useRef(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const addFiles = async (files: File[]) => {
    if (disabled || reading.current || !files.length) return
    const existing = currentImages.current
    const mib = (bytes: number) => `${bytes / 1048576} MiB`
    try {
      if (existing.length + files.length > config.max_images_per_input) throw new Error(`You can attach at most ${config.max_images_per_input} images.`)
      for (const file of files) {
        if (!config.accepted_input_mime_types.includes(file.type)) throw new Error(`${file.name || 'Image'}: this format cannot be sent to the model. Use JPEG, PNG, GIF or WebP.`)
        if (file.size > config.max_image_bytes) throw new Error(`${file.name}: exceeds the ${mib(config.max_image_bytes)} per-image limit.`)
      }
      if (existing.reduce((sum, image) => sum + image.decodedBytes, 0) + files.reduce((sum, file) => sum + file.size, 0) > config.max_total_image_bytes) throw new Error(`Attached images exceed the total ${mib(config.max_total_image_bytes)} limit.`)
      reading.current = true
      setBusy(true)
      onReadingChange?.(true)
      const additions = await Promise.all(files.map(async (file): Promise<DraftImage> => ({
        id: crypto.randomUUID(), filename: file.name || null, mediaType: file.type, decodedBytes: file.size,
        imageUrl: await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(reader.result as string)
          reader.onerror = () => reject(new Error(`Could not read ${file.name}.`))
          reader.readAsDataURL(file)
        }),
      })))
      onChange([...currentImages.current, ...additions])
      setError(null)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not attach images.')
    } finally {
      reading.current = false
      setBusy(false)
      onReadingChange?.(false)
    }
  }
  return (
    <div onPaste={(event) => {
      const files = Array.from(event.clipboardData.items).filter((item) => item.kind === 'file' && item.type.startsWith('image/')).map((item) => item.getAsFile()).filter((file): file is File => file !== null)
      if (files.length) { event.preventDefault(); void addFiles(files) }
    }} onDragOver={(event) => { if (event.dataTransfer.types.includes('Files')) event.preventDefault() }} onDrop={(event) => {
      if (event.dataTransfer.files.length) { event.preventDefault(); void addFiles(Array.from(event.dataTransfer.files)) }
    }}>
      {children}
      <div className="my-2 flex flex-wrap items-start gap-2">
        {images.map((image) => <div className="flex flex-col gap-1" key={image.id}>
          <MessageImage image={{ image_url: image.imageUrl, media_type: image.mediaType, filename: image.filename, detail: config.default_detail }} />
          <Button type="button" size="sm" variant="ghost" disabled={disabled || busy} aria-label={`Remove ${image.filename || 'image'}`} onClick={() => onChange(images.filter((item) => item.id !== image.id))}>Remove</Button>
        </div>)}
      </div>
      <input type="file" aria-label="Select images" hidden ref={input} multiple accept={config.accepted_input_mime_types.join(',')} disabled={disabled || busy} onChange={(event) => { void addFiles(Array.from(event.target.files ?? [])); event.target.value = '' }} />
      <Button type="button" size="sm" variant="ghost" disabled={disabled || busy} onClick={() => input.current?.click()}>{busy ? 'Reading images…' : 'Attach images'}</Button>
      {error ? <p className="mt-1 text-xs text-destructive" role="alert">{error}</p> : null}
    </div>
  )
}
