import type { ImageContent } from '../../api/types'

export interface PendingDirectMessage {
  text: string
  images: ImageContent[]
  runId: string
  submittedAt: string
  baseNewestSeq: number | null
  baseSequenceKnown: boolean
}

export interface DraftImage {
  id: string
  imageUrl: string
  mediaType: string
  filename: string | null
  decodedBytes: number
}

export interface ComposerDraft {
  text: string
  images: DraftImage[]
}

export const EMPTY_DRAFT: ComposerDraft = { text: '', images: [] }
