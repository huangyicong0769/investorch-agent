import type { InfiniteData } from '@tanstack/react-query'

import type { HistoryResponse, JournalRecord } from '../../api/types'

export type HistoryInfiniteData = InfiniteData<HistoryResponse, number | undefined>

function orderedUniqueRecords(records: readonly JournalRecord[]): JournalRecord[] {
  const bySequence = new Map<number, JournalRecord>()
  for (const record of records) {
    if (Number.isInteger(record.seq) && record.seq > 0) {
      bySequence.set(record.seq, record)
    }
  }
  return [...bySequence.values()].sort((left, right) => left.seq - right.seq)
}

function pageFromRecords(records: JournalRecord[], hasOlder: boolean): HistoryResponse {
  return {
    records,
    has_older: hasOlder,
    oldest_seq: records[0]?.seq ?? null,
    newest_seq: records[records.length - 1]?.seq ?? null,
  }
}

/**
 * Merge a freshly fetched latest page into every currently loaded page.
 * Pages stay newest-first while each page remains sequence-ascending.
 */
export function mergeHistoryPages(
  current: HistoryInfiniteData | undefined,
  latest: HistoryResponse,
  pageSize: number,
): HistoryInfiniteData {
  const loadedRecords = current?.pages.flatMap((page) => page.records) ?? []
  const records = orderedUniqueRecords([...loadedRecords, ...latest.records])
  const oldestLoadedPage = current?.pages.at(-1)
  const hasOlderBoundary = oldestLoadedPage?.has_older ?? latest.has_older
  const pages: HistoryResponse[] = []

  for (let end = records.length; end > 0; end -= pageSize) {
    const start = Math.max(0, end - pageSize)
    pages.push(pageFromRecords(records.slice(start, end), start > 0))
  }

  if (pages.length === 0) {
    pages.push(pageFromRecords([], hasOlderBoundary))
  } else {
    pages[pages.length - 1] = pageFromRecords(pages[pages.length - 1].records, hasOlderBoundary)
  }

  return {
    pages,
    pageParams: pages.map((page, index) => (index === 0 ? undefined : pages[index - 1].oldest_seq ?? undefined)),
  }
}

export function historyNewestSeq(data: HistoryInfiniteData | undefined): number | null {
  if (!data) {
    return null
  }

  return data.pages.reduce<number | null>((newest, page) => {
    if (page.newest_seq === null) {
      return newest
    }
    return newest === null ? page.newest_seq : Math.max(newest, page.newest_seq)
  }, null)
}
