export interface PendingDirectMessage {
  text: string
  runId: string
  baseNewestSeq: number | null
  baseSequenceKnown: boolean
}
