import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { completeSetup, configQueryOptions, managementKeys, saveConfig, testModel, type SavedConfig } from '../../api/management'
import type { JsonValue } from '../../api/types'
import { ErrorNotice, fieldErrors } from '../management/Feedback'
import { ModelConfiguration, modelConfigValues } from '../management/ModelConfiguration'
import { objectValue } from '../management/schema'
import { SecretFields } from '../management/SecretFields'
import { UnsavedChanges } from '../management/UnsavedChanges'
import { Button } from '@/components/ui/button'

function SetupForm({ saved }: { saved: SavedConfig }) {
  const client = useQueryClient()
  const [changes, setChanges] = useState<Record<string, JsonValue>>({})
  const [keys, setKeys] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState('')
  const connections = modelConfigValues(saved.values, changes, 'models')
  const agents = modelConfigValues(saved.values, changes, 'agents')
  const referencedKeys = Object.values(connections).map((connection) => String(objectValue(connection).api_key_secret ?? '')).filter(Boolean)
  const dirty = Object.keys(changes).length > 0 || Object.values(keys).some(Boolean)
  const save = useMutation({
    mutationFn: async (finish: boolean) => {
      const updates = { ...changes }
      for (const [key, value] of Object.entries(keys)) if (value) updates[`secrets.${key}`] = value
      if (Object.keys(updates).length) await saveConfig(updates, true)
      setChanges({}); setKeys({})
      return finish ? completeSetup() : null
    },
    onSuccess: async (state) => {
      setNotice(state?.error ?? (state?.status === 'ready' ? 'Setup complete. Opening InvestOrch…' : 'Saved. You can authorize ChatGPT connections and select models below.'))
      if (state) client.setQueryData(managementKeys.setup(), state)
      await client.invalidateQueries({ queryKey: managementKeys.config() })
    },
    onError: async () => { await client.invalidateQueries({ queryKey: managementKeys.config() }); await client.invalidateQueries({ queryKey: managementKeys.setup() }) },
  })
  const main = objectValue(agents.main)
  const connection = objectValue(connections[String(main.model ?? '')])
  const key = keys[String(connection.api_key_secret ?? '')]
  const probe = useMutation({ mutationFn: () => testModel({ name: String(main.name ?? ''), base_url: String(connection.base_url ?? ''), api_key: key ?? '', reasoning_effort: String(main.reasoning_effort ?? 'high') }) })
  const busy = save.isPending || probe.isPending
  const change = (path: string, value: JsonValue) => {
    setChanges((current) => ({ ...Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(`${path}.`))), [path]: value }))
    setNotice(''); save.reset(); probe.reset()
  }
  return <div className="space-y-6">
    <UnsavedChanges dirty={dirty} />
    <ModelConfiguration agents={agents} connections={connections} disabled={busy} errors={fieldErrors(save.error)} onChange={change} savedConnections={objectValue(saved.values.models)} />
    {referencedKeys.length ? <section className="rounded-xl border border-border bg-card p-5"><h2 className="mb-5 font-semibold">API keys</h2><SecretFields configured={saved.secrets} disabled={busy} errors={fieldErrors(save.error)} names={referencedKeys} onChange={(name, value) => { setKeys((current) => ({ ...current, [name]: value })); save.reset(); probe.reset() }} values={keys} /></section> : null}
    <section className="space-y-4 rounded-xl border border-border bg-card p-5">
      <h2 className="font-semibold">Verify and start</h2>
      <p className="text-sm text-muted-foreground">Save connections first to authorize ChatGPT. Then choose each Agent’s model and context window. API key connection testing sends one request with the key entered above.</p>
      {saved.missing.length ? <p className="break-words text-xs text-muted-foreground">Still required: {saved.missing.join(', ')}</p> : null}
      <ErrorNotice error={save.error ?? probe.error} fallback="Setup could not finish." />
      {probe.data ? <p className="text-sm" role="status">{probe.data.message}</p> : null}
      {notice ? <p className="text-sm" role="status">{notice}</p> : null}
      <div className="flex flex-wrap gap-3">
        <Button disabled={busy || !dirty} onClick={() => { setChanges({}); setKeys({}); save.reset(); probe.reset() }} type="button" variant="ghost">Discard changes</Button>
        {connection.type === 'openai_compatible' ? <Button disabled={busy || !key || !main.name} onClick={() => probe.mutate()} type="button" variant="outline">{probe.isPending ? 'Testing…' : 'Test Main connection'}</Button> : null}
        <Button disabled={busy || !dirty} onClick={() => save.mutate(false)} type="button" variant="outline">Save configuration</Button>
        <Button disabled={busy} onClick={() => save.mutate(true)} type="button">{save.isPending ? 'Saving…' : 'Save and open InvestOrch'}</Button>
      </div>
    </section>
  </div>
}

export function SetupPage({ startupError }: { startupError?: string }) {
  const config = useQuery(configQueryOptions())
  return <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-16">
    <header className="mb-8"><p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">InvestOrch Agent</p><h1 className="mt-3 text-3xl font-semibold tracking-tight">Set up your workspace</h1><p className="mt-3 text-sm leading-6 text-muted-foreground">Connect an API endpoint or use your ChatGPT plan, then choose Agent defaults.</p></header>
    {startupError ? <p className="mb-5 text-sm text-destructive" role="alert">{startupError}</p> : null}
    <ErrorNotice error={config.error} fallback="Setup settings could not be loaded." />
    {config.isPending ? <p role="status">Loading setup…</p> : null}
    {config.data ? <SetupForm saved={config.data} /> : null}
    {config.isError ? <Button onClick={() => void config.refetch()} type="button" variant="outline">Retry</Button> : null}
    <Link className="mt-6 inline-block text-sm underline" to="/system">View local diagnostics</Link>
  </div>
}
