export interface PendingDirectMessage {
  text: string
  runId: string
  submittedAt: string
  baseNewestSeq: number | null
  baseSequenceKnown: boolean
}
