import { useId } from 'react'
import { useQuery } from '@tanstack/react-query'
import { chatGPTModelsOptions } from '../../api/management'
import type { JsonObject, JsonValue } from '../../api/types'
import { fieldClass } from './SchemaField'
import { objectValue } from './schema'

export const EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'] as const
export const AGENT_ROLES = ['main', 'title', 'activity', 'permission', 'compact'] as const

export function ModelFields({ value, connections, onChange, disabled, errors = {}, prefix = '', probe = false }: {
  value: JsonObject
  connections: JsonObject
  onChange: (field: string, value: JsonValue) => void
  disabled?: boolean
  errors?: Record<string, string>
  prefix?: string
  probe?: boolean
}) {
  const id = useId()
  const connectionId = String(value.model ?? '')
  const chatgpt = objectValue(connections[connectionId]).type === 'chatgpt'
  const catalog = useQuery({ ...chatGPTModelsOptions(connectionId), enabled: chatgpt && Boolean(connectionId) })
  const field = (name: string, label: string, control: React.ReactNode) => <div className="min-w-0">
    <label className="mb-2 block text-sm font-medium" htmlFor={`${id}-${name}`}>{label}</label>
    {control}
    {errors[`${prefix}${name}`] ? <p className="mt-1 text-xs text-destructive" role="alert">{errors[`${prefix}${name}`]}</p> : null}
  </div>
  return <div className="grid gap-4 sm:grid-cols-2">
    {field('model', 'Connection', <select className={fieldClass} disabled={disabled} id={`${id}-model`} onChange={(e) => { onChange('model', e.target.value); onChange('name', ''); onChange('context_window_tokens', null) }} value={String(value.model ?? '')}>
      <option value="">Choose a connection</option>{Object.keys(connections).map((name) => <option key={name} value={name}>{name} · {String(objectValue(connections[name]).type)}</option>)}
    </select>)}
    {field('name', 'Model ID', chatgpt ? <div><select className={fieldClass} disabled={disabled || catalog.isPending} id={`${id}-name`} onChange={(e) => { onChange('name', e.target.value); onChange('context_window_tokens', null) }} value={String(value.name ?? '')}><option value="">Choose an account model</option>{value.name && !catalog.data?.models.some((model) => model.slug === value.name) ? <option value={String(value.name)}>{String(value.name)} (saved selection)</option> : null}{catalog.data?.models.map((model) => <option key={model.slug} value={model.slug}>{model.display_name}</option>)}</select>{catalog.isError ? <p className="mt-1 text-xs text-destructive" role="alert">Model catalog unavailable. Authorize this connection and <button className="underline" onClick={() => void catalog.refetch()} type="button">retry</button>.</p> : null}</div> : <input className={fieldClass} disabled={disabled} id={`${id}-name`} onChange={(e) => { onChange('name', e.target.value); onChange('context_window_tokens', null) }} value={String(value.name ?? '')} />)}
    {field('reasoning_effort', 'Reasoning effort', <select className={fieldClass} disabled={disabled} id={`${id}-reasoning_effort`} onChange={(e) => onChange('reasoning_effort', e.target.value)} value={String(value.reasoning_effort ?? 'high')}>
      {EFFORTS.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
    </select>)}
    {!probe && field('context_window_tokens', 'Context window (tokens)', <input className={fieldClass} disabled={disabled} id={`${id}-context_window_tokens`} min={1} onChange={(e) => onChange('context_window_tokens', e.target.value ? Number(e.target.value) : null)} placeholder="Enter the model’s documented window" step={1} type="number" value={typeof value.context_window_tokens === 'number' ? value.context_window_tokens : ''} />)}
  </div>
}
