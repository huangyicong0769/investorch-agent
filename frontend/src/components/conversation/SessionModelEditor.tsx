import { useState } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { ArrowLeft, Check, ChevronDown, ChevronRight, LoaderCircle } from 'lucide-react'
import { Link } from 'react-router-dom'
import { sessionModelQueryOptions, updateSessionModel } from '../../api/sessionModel'
import { chatGPTModelsOptions, configQueryOptions } from '../../api/management'
import { queryKeys } from '../../api/queries'
import type { AgentModelConfig, JsonObject, ReasoningEffort } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { RUN_DEFAULTS_MUTATION_KEY } from '../composer/RunControlsPopover'
import { objectValue } from '../management/schema'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const EFFORTS: { value: ReasoningEffort; label: string }[] = [
  { value: 'none', label: 'Off' },
  { value: 'minimal', label: 'Minimal' },
  { value: 'low', label: 'Low' },
  { value: 'medium', label: 'Medium' },
  { value: 'high', label: 'High' },
  { value: 'xhigh', label: 'Extra high' },
  { value: 'max', label: 'Max' },
]
const rowClass = 'h-9 w-full justify-between rounded-md px-2.5 text-sm font-normal'
const inputClass = 'h-9 w-full rounded-md border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring'

type ModelChoice = { connection: string; name: string; label: string }
type Panel =
  | { kind: 'models' }
  | { kind: 'thinking' }
  | { kind: 'window'; choice: ModelChoice; value: string; changingModel: boolean }
  | { kind: 'custom'; connection: string; name: string; window: string }

function positiveWindow(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0 ? value : undefined
}

function configuredWindow(choice: ModelChoice, selected: AgentModelConfig | undefined, agents: JsonObject) {
  if (selected?.model === choice.connection && selected.name === choice.name) return selected.context_window_tokens
  const matches = (value: JsonObject) => value.model === choice.connection && value.name === choice.name
  const main = objectValue(agents.main)
  for (const value of Object.values(agents)) {
    const agent = objectValue(value)
    if (!matches(agent)) continue
    const window = positiveWindow(agent.context_window_tokens) ?? (matches(main) ? positiveWindow(main.context_window_tokens) : undefined)
    if (window) return window
  }
}

export function SessionModelEditor({ sessionId }: { sessionId: string }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [panel, setPanel] = useState<Panel>({ kind: 'models' })
  const model = useQuery({ ...sessionModelQueryOptions(sessionId), enabled: Boolean(sessionId) })
  const config = useQuery({ ...configQueryOptions(), enabled: Boolean(sessionId) })
  const selected = model.data?.effective
  const connections = Object.entries(objectValue(config.data?.values.models))
  const agents = objectValue(config.data?.values.agents)
  const chatGPTConnections = connections.filter(([, value]) => objectValue(value).type === 'chatgpt')
  const catalogs = useQueries({ queries: chatGPTConnections.map(([id]) => ({
    ...chatGPTModelsOptions(id),
    enabled: open || selected?.model === id,
  })) })
  const catalogFor = (connection: string) => catalogs[chatGPTConnections.findIndex(([id]) => id === connection)]
  const displayName = (connection: string, name: string) => catalogFor(connection)?.data?.models.find((item) => item.slug === name)?.display_name ?? name
  const save = useMutation({
    mutationKey: RUN_DEFAULTS_MUTATION_KEY,
    mutationFn: (change: JsonObject) => updateSessionModel(sessionId, change),
    onSuccess: (view) => {
      client.setQueryData(queryKeys.sessionModel(sessionId), view)
      setOpen(false)
      setPanel({ kind: 'models' })
    },
    onError: () => { void client.invalidateQueries({ queryKey: queryKeys.sessionModel(sessionId) }) },
  })
  const busy = save.isPending
  const ready = Boolean(selected) && !model.isError && !config.isPending && !config.isError
  const effort = EFFORTS.find((item) => item.value === selected?.reasoning_effort)?.label
  const label = selected ? `${displayName(selected.model, selected.name)} · ${effort}` : model.isError ? 'Model unavailable' : 'Model · Loading…'

  const applyModel = (choice: ModelChoice, window: number) => {
    if (!selected || busy) return
    save.mutate({ model: choice.connection, name: choice.name, reasoning_effort: selected.reasoning_effort, context_window_tokens: window })
  }
  const chooseModel = (choice: ModelChoice) => {
    save.reset()
    if (selected?.model === choice.connection && selected.name === choice.name) {
      setOpen(false)
      return
    }
    const window = configuredWindow(choice, selected, agents)
    if (window) applyModel(choice, window)
    else setPanel({ kind: 'window', choice, value: '', changingModel: true })
  }
  const back = () => { save.reset(); setPanel({ kind: 'models' }) }
  const failure = save.error ?? model.error ?? config.error
  const customChoice = panel.kind === 'custom' ? { connection: panel.connection, name: panel.name.trim(), label: panel.name.trim() } : null
  const customWindow = customChoice ? configuredWindow(customChoice, selected, agents) : undefined

  return <Popover open={open} onOpenChange={(next) => {
    if (busy) return
    setOpen(next)
    setPanel({ kind: 'models' })
    save.reset()
  }}>
    <PopoverTrigger asChild>
      <Button
        aria-label={`Model and thinking: ${label}`}
        className="h-8 min-w-0 max-w-64 shrink gap-1.5 rounded-full px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
        disabled={!sessionId || busy}
        size="sm"
        type="button"
        variant="ghost"
      >
        <span className="truncate">{label}</span>
        {busy ? <LoaderCircle aria-hidden="true" className="size-3 shrink-0 animate-spin" /> : <ChevronDown aria-hidden="true" className="size-3 shrink-0 opacity-60" />}
      </Button>
    </PopoverTrigger>
    <PopoverContent align="end" aria-label="Choose a model" className="max-h-[var(--radix-popover-content-available-height)] w-80 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl p-1.5" collisionPadding={12} side="top" sideOffset={10}>
      {panel.kind !== 'models' ? <div className="mb-1 flex items-center gap-1 px-1">
        <Button aria-label="Back to models" disabled={busy} onClick={back} size="icon-sm" type="button" variant="ghost"><ArrowLeft className="size-4" /></Button>
        <span className="text-sm font-medium">{panel.kind === 'thinking' ? 'Thinking' : panel.kind === 'custom' ? 'Custom model' : 'Context window'}</span>
      </div> : null}
      {failure ? <div className="space-y-1 px-2.5 py-2 text-xs" role="alert">
        <p className="text-destructive">{errorMessage(failure, 'Could not update the model.')}</p>
        <Button className="h-auto p-0 text-xs" disabled={busy} onClick={() => {
          if (save.error && save.variables) save.mutate(save.variables)
          else { void model.refetch(); void config.refetch() }
        }} size="sm" type="button" variant="link">Retry</Button>
      </div> : null}
      {panel.kind === 'models' ? <>
        <div className="max-h-72 overflow-y-auto" aria-label="Models">
          {config.isPending || model.isPending ? <p className="px-2.5 py-2 text-xs text-muted-foreground" role="status">Loading models…</p> : null}
          {connections.map(([connection, raw]) => {
            const chatGPT = objectValue(raw).type === 'chatgpt'
            const catalog = catalogFor(connection)
            const choices = new Map<string, ModelChoice>()
            if (selected?.model === connection) choices.set(selected.name, { connection, name: selected.name, label: displayName(connection, selected.name) })
            if (chatGPT) {
              for (const item of catalog?.data?.models ?? []) choices.set(item.slug, { connection, name: item.slug, label: item.display_name })
            } else {
              for (const rawAgent of Object.values(agents)) {
                const agent = objectValue(rawAgent)
                if (agent.model === connection && typeof agent.name === 'string' && agent.name) choices.set(agent.name, { connection, name: agent.name, label: agent.name })
              }
            }
            return <div className="py-0.5" key={connection}>
              {connections.length > 1 ? <p className="px-2.5 pb-1 pt-1.5 text-[11px] text-muted-foreground">{connection}</p> : null}
              {Array.from(choices.values()).map((choice) => <Button className={rowClass} disabled={!ready || busy} key={choice.name} onClick={() => chooseModel(choice)} title={choice.label} type="button" variant="ghost">
                <span className="truncate">{choice.label}</span>
                {selected?.model === connection && selected.name === choice.name ? <Check aria-hidden="true" className="size-3.5" /> : null}
              </Button>)}
              {chatGPT ? catalog?.isError ? <div className="px-2.5 py-1.5 text-xs text-muted-foreground" role="alert">Couldn’t load models. <button className="underline" onClick={() => void catalog.refetch()} type="button">Retry</button></div> : catalog?.isPending ? <p className="px-2.5 py-1.5 text-xs text-muted-foreground" role="status">Loading models…</p> : !choices.size ? <p className="px-2.5 py-1.5 text-xs text-muted-foreground">No models available.</p> : null : <Button className={`${rowClass} text-muted-foreground`} disabled={!ready || busy} onClick={() => { save.reset(); setPanel({ kind: 'custom', connection, name: '', window: '' }) }} type="button" variant="ghost">Custom model…<ChevronRight aria-hidden="true" className="size-3.5" /></Button>}
            </div>
          })}
        </div>
        <div className="my-1 border-t" />
        <Button className={rowClass} disabled={!ready || busy} onClick={() => { save.reset(); setPanel({ kind: 'thinking' }) }} type="button" variant="ghost">Thinking<span className="flex items-center gap-1 text-muted-foreground">{effort}<ChevronRight aria-hidden="true" className="size-3.5" /></span></Button>
        <Button className={rowClass} disabled={!ready || busy} onClick={() => {
          if (selected) { save.reset(); setPanel({ kind: 'window', choice: { connection: selected.model, name: selected.name, label: displayName(selected.model, selected.name) }, value: String(selected.context_window_tokens), changingModel: false }) }
        }} type="button" variant="ghost">Context window<ChevronRight aria-hidden="true" className="size-3.5 text-muted-foreground" /></Button>
        <div className="my-1 border-t" />
        {model.data?.active ? <p className="px-2.5 py-1 text-[11px] text-muted-foreground">Applies to your next message</p> : null}
        <Link className="block rounded-md px-2.5 py-2 text-xs text-muted-foreground hover:bg-accent hover:text-foreground" onClick={(event) => { if (busy) event.preventDefault(); else setOpen(false) }} to="/settings?section=models">Manage models</Link>
      </> : panel.kind === 'thinking' ? <div aria-label="Thinking level">
        {EFFORTS.map((item) => <Button className={rowClass} disabled={!ready || busy} key={item.value} onClick={() => {
          if (item.value === selected?.reasoning_effort) setOpen(false)
          else save.mutate({ reasoning_effort: item.value })
        }} type="button" variant="ghost">{item.label}{item.value === selected?.reasoning_effort ? <Check aria-hidden="true" className="size-3.5" /> : null}</Button>)}
      </div> : panel.kind === 'window' ? <div className="space-y-3 px-2.5 pb-2 pt-1">
        <p className="truncate text-xs text-muted-foreground" title={panel.choice.label}>{panel.choice.label}</p>
        <label className="block space-y-1.5 text-xs"><span>Context window (tokens)</span><input aria-label="Context window (tokens)" autoFocus className={inputClass} disabled={busy} min={1} onChange={(event) => { save.reset(); setPanel({ ...panel, value: event.target.value }) }} onKeyDown={(event) => {
          if (event.key === 'Enter') { event.preventDefault(); const value = positiveWindow(Number(panel.value)); if (value && ready && !busy) applyModel(panel.choice, value) }
        }} placeholder="Enter the model’s supported limit" step={1} type="number" value={panel.value} /></label>
        <Button className="w-full" disabled={!ready || busy || !positiveWindow(Number(panel.value))} onClick={() => { const value = positiveWindow(Number(panel.value)); if (value) applyModel(panel.choice, value) }} size="sm" type="button">{busy ? 'Applying…' : panel.changingModel ? 'Use model' : 'Apply'}</Button>
      </div> : <div className="space-y-3 px-2.5 pb-2 pt-1">
        {connections.length > 1 ? <p className="text-xs text-muted-foreground">{panel.connection}</p> : null}
        <label className="block space-y-1.5 text-xs"><span>Model ID</span><input autoFocus className={inputClass} disabled={busy} onChange={(event) => { save.reset(); setPanel({ ...panel, name: event.target.value, window: '' }) }} placeholder="Provider model ID" value={panel.name} /></label>
        {!customWindow ? <label className="block space-y-1.5 text-xs"><span>Context window (tokens)</span><input className={inputClass} disabled={busy} min={1} onChange={(event) => { save.reset(); setPanel({ ...panel, window: event.target.value }) }} placeholder="Enter the model’s supported limit" step={1} type="number" value={panel.window} /></label> : null}
        <Button className="w-full" disabled={!ready || busy || !customChoice?.name || !(customWindow ?? positiveWindow(Number(panel.window)))} onClick={() => {
          const window = customWindow ?? positiveWindow(Number(panel.window))
          if (customChoice?.name && window) applyModel(customChoice, window)
        }} size="sm" type="button">{busy ? 'Applying…' : 'Use model'}</Button>
      </div>}
    </PopoverContent>
  </Popover>
}
