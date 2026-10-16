import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, LoaderCircle } from 'lucide-react'
import { sessionModelQueryOptions, updateSessionModel } from '../../api/sessionModel'
import { configQueryOptions } from '../../api/management'
import { queryKeys } from '../../api/queries'
import type { AgentModelConfig, JsonObject } from '../../api/types'
import { RUN_DEFAULTS_MUTATION_KEY } from '../composer/RunControlsPopover'
import { ModelFields } from '../management/ModelFields'
import { objectValue } from '../management/schema'
import { ErrorNotice } from '../management/Feedback'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from '@/components/ui/popover'

function describe(model: AgentModelConfig) {
  return `${model.model} / ${model.name} · ${model.reasoning_effort} · ${model.context_window_tokens.toLocaleString()} tokens`
}

function effortLabel(effort: string) {
  return effort === 'xhigh' ? 'Extra high' : `${effort.slice(0, 1).toUpperCase()}${effort.slice(1)}`
}

export function SessionModelEditor({ sessionId }: { sessionId: string }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [changes, setChanges] = useState<JsonObject>({})
  const model = useQuery({ ...sessionModelQueryOptions(sessionId), enabled: Boolean(sessionId) })
  const config = useQuery({ ...configQueryOptions(), enabled: open && Boolean(sessionId) })
  const save = useMutation({
    mutationKey: RUN_DEFAULTS_MUTATION_KEY,
    mutationFn: () => updateSessionModel(sessionId, { ...model.data?.effective, ...changes }),
    onSuccess: (view) => { client.setQueryData(queryKeys.sessionModel(sessionId), view); setChanges({}) },
    onError: () => { void client.invalidateQueries({ queryKey: queryKeys.sessionModel(sessionId) }) },
  })
  const selected = model.data?.effective
  const label = selected ? `${selected.name} · ${effortLabel(selected.reasoning_effort)}` : model.isError ? 'Model unavailable' : 'Model · Loading…'
  return <Popover open={open} onOpenChange={(next) => { setOpen(next); setChanges({}); save.reset() }}>
    <PopoverTrigger asChild>
      <Button
        aria-label={`Model and reasoning effort: ${label}`}
        className="h-8 min-w-0 max-w-64 shrink gap-1.5 rounded-full px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
        disabled={!sessionId || save.isPending}
        size="sm"
        title={selected ? `Next Run: ${describe(selected)}${model.data?.draft ? ' (Draft)' : ''}` : 'Session model settings'}
        type="button"
        variant="ghost"
      >
        <span className="truncate">{label}</span>
        {save.isPending ? <LoaderCircle aria-hidden="true" className="size-3 shrink-0 animate-spin" /> : <ChevronDown aria-hidden="true" className="size-3 shrink-0 opacity-60" />}
      </Button>
    </PopoverTrigger>
    <PopoverContent align="end" aria-label="Session model" className="max-h-[var(--radix-popover-content-available-height)] w-[30rem] max-w-[calc(100vw-1.5rem)] space-y-4 overflow-y-auto rounded-xl" collisionPadding={12} side="top" sideOffset={10}>
      <PopoverHeader><PopoverTitle>Model and reasoning effort</PopoverTitle><PopoverDescription>Changes apply to the next Run, including queued messages when they start. A draft is lost if you exit before running it.</PopoverDescription></PopoverHeader>
      <ErrorNotice error={model.error ?? config.error ?? save.error} fallback="The Session model could not be updated." />
      {model.isError || config.isError ? <Button onClick={() => { void model.refetch(); void config.refetch() }} size="sm" type="button" variant="outline">Retry</Button> : null}
      {model.data ? <>
        {model.data.active ? <p className="break-words text-sm"><strong>Current Run:</strong> {describe(model.data.active)}</p> : null}
        <p className="break-words text-xs text-muted-foreground"><strong>Saved:</strong> {model.data.persisted ? describe(model.data.persisted) : 'No Run has saved a model yet.'}</p>
        <h3 className="text-sm font-medium">Next Run{model.data.draft ? ' · Draft' : ''}</h3>
        <ModelFields connections={objectValue(config.data?.values.models)} disabled={save.isPending || config.isPending || config.isError} onChange={(field, value) => setChanges((current) => ({ ...current, [field]: value }))} value={{ ...model.data.effective, ...changes }} />
        {save.isSuccess ? <p className="text-sm" role="status">Draft updated. The current Run is unchanged.</p> : null}
        <Button disabled={save.isPending || config.isPending || config.isError || !Object.keys(changes).length} onClick={() => save.mutate()} type="button">{save.isPending ? 'Saving…' : 'Update next Run'}</Button>
      </> : model.isPending ? <p role="status">Loading model…</p> : null}
    </PopoverContent>
  </Popover>
}
