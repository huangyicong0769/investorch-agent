import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { sessionModelQueryOptions, updateSessionModel } from '../../api/sessionModel'
import { configQueryOptions } from '../../api/management'
import { queryKeys } from '../../api/queries'
import type { AgentModelConfig, JsonObject } from '../../api/types'
import { ModelFields } from '../management/ModelFields'
import { objectValue } from '../management/schema'
import { ErrorNotice } from '../management/Feedback'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog'

function describe(model: AgentModelConfig) {
  return `${model.model} / ${model.name} · ${model.reasoning_effort} · ${model.context_window_tokens.toLocaleString()} tokens`
}

export function SessionModelEditor({ sessionId }: { sessionId: string }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [changes, setChanges] = useState<JsonObject>({})
  const model = useQuery(sessionModelQueryOptions(sessionId))
  const config = useQuery({ ...configQueryOptions(), enabled: open })
  const save = useMutation({
    mutationFn: () => updateSessionModel(sessionId, { ...model.data?.effective, ...changes }),
    onSuccess: (view) => { client.setQueryData(queryKeys.sessionModel(sessionId), view); setChanges({}) },
    onError: () => { void client.invalidateQueries({ queryKey: queryKeys.sessionModel(sessionId) }) },
  })
  const selected = model.data?.active ?? model.data?.effective
  return <Dialog open={open} onOpenChange={(next) => { setOpen(next); setChanges({}); save.reset() }}>
    <DialogTrigger asChild><Button aria-label="Session model settings" className="max-w-40 truncate" size="sm" type="button" variant="ghost">{selected ? selected.name : 'Model'}{model.data?.draft ? ' · Draft' : ''}</Button></DialogTrigger>
    <DialogContent><DialogHeader><DialogTitle>Session model</DialogTitle><DialogDescription>Changes apply to the next Run, including queued messages when they start. A draft is lost if you exit before running it.</DialogDescription></DialogHeader>
      <ErrorNotice error={model.error ?? config.error ?? save.error} fallback="The Session model could not be updated." />
      {model.data ? <>
        {model.data.active ? <p className="break-words text-sm"><strong>Current Run:</strong> {describe(model.data.active)}</p> : null}
        <p className="break-words text-sm text-muted-foreground"><strong>Saved:</strong> {model.data.persisted ? describe(model.data.persisted) : 'No Run has saved a model yet.'}</p>
        <h3 className="text-sm font-medium">Next Run{model.data.draft ? ' · Draft' : ''}</h3>
        <ModelFields connections={objectValue(config.data?.values.models)} disabled={save.isPending} onChange={(field, value) => setChanges((current) => ({ ...current, [field]: value }))} value={{ ...model.data.effective, ...changes }} />
        {save.isSuccess ? <p className="text-sm" role="status">Draft updated. The current Run is unchanged.</p> : null}
        <Button disabled={save.isPending || !Object.keys(changes).length} onClick={() => save.mutate()} type="button">{save.isPending ? 'Saving…' : 'Update next Run'}</Button>
      </> : <p role="status">Loading model…</p>}
    </DialogContent>
  </Dialog>
}
