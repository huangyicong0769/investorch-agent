import { ApiError } from '../../api/client'
import { errorMessage } from '../../lib/errors'

export function ErrorNotice({ error, fallback = 'The action could not be completed. Try again.' }: { error: unknown; fallback?: string }) {
  if (!error) return null
  return <p className="rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2 text-sm text-destructive" role="alert">{errorMessage(error, fallback)}</p>
}

export function fieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {}
  const result: Record<string, string> = {}
  for (const item of error.details.errors ?? []) {
    if (item === null || typeof item !== 'object' || Array.isArray(item)) continue
    const path = typeof item.path === 'string' ? item.path : Array.isArray(item.loc) ? item.loc.join('.') : ''
    const message = typeof item.message === 'string' ? item.message : typeof item.msg === 'string' ? item.msg : ''
    if (path && message) result[path] = message
  }
  return result
}
