import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { ApiError } from '../../api/client'
import { completeSetup, configQueryOptions, managementKeys, saveConfig, schemaQueryOptions, testModel, type ConfigSchema, type SavedConfig } from '../../api/management'
import type { JsonValue } from '../../api/types'
import { ErrorNotice, fieldErrors } from '../management/Feedback'
import { SchemaField } from '../management/SchemaField'
import { configValue, fieldLabel, objectValue, resolveSchema, schemaAtPath } from '../management/schema'
import { SecretFields } from '../management/SecretFields'
import { UnsavedChanges } from '../management/UnsavedChanges'
import { Button } from '@/components/ui/button'

function setupErrors(error: unknown): Record<string, string> {
  const errors = fieldErrors(error)
  if (error instanceof ApiError && Array.isArray(error.details.missing)) {
    for (const path of error.details.missing) {
      if (typeof path === 'string') errors[path] = path.startsWith('secrets.') ? 'Enter an API key for this reference.' : 'Required to finish setup.'
    }
  }
  return errors
}

function SetupForm({ saved, schema }: { saved: SavedConfig; schema: ConfigSchema }) {
  const queryClient = useQueryClient()
  const roles = Object.keys(objectValue(saved.values.models))
  const modelSchema = resolveSchema(schemaAtPath(schema, 'models.main'), schema)
  const [changes, setChanges] = useState<Record<string, JsonValue>>({})
  const [keyValues, setKeyValues] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState('')
  const [primaryApplied, setPrimaryApplied] = useState(false)
  const [rolesExpanded, setRolesExpanded] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const probeController = useRef<AbortController | null>(null)
  useEffect(() => () => probeController.current?.abort(), [])
  const value = (path: string) => path in changes ? changes[path] : configValue(saved.values, path)
  const keyName = String(value('models.main.api_key_secret') ?? '')
  const keyValue = keyValues[keyName] ?? ''
  const referencedKeys = roles.map((role) => String(value(`models.${role}.api_key_secret`) ?? '')).filter(Boolean)
  const dirty = Object.keys(changes).length > 0 || Object.values(keyValues).some(Boolean)
  const save = useMutation({
    mutationFn: async () => {
      const updates = { ...changes }
      for (const [name, key] of Object.entries(keyValues)) if (key) updates[`secrets.${name}`] = key
      if (Object.keys(updates).length) await saveConfig(updates, true)
      setKeyValues({})
      setChanges({})
      return completeSetup()
    },
    onSuccess: async (state) => {
      setKeyValues({})
      setChanges({})
      setNotice(state.error ?? (state.status === 'ready' ? 'Setup complete. Opening InvestOrch…' : 'Configuration saved. Complete the remaining settings below.'))
      queryClient.setQueryData(managementKeys.setup(), state)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: managementKeys.config() }),
        queryClient.invalidateQueries({ queryKey: managementKeys.schema() }),
      ])
    },
    onError: async (error) => {
      if (Object.keys(setupErrors(error)).some((path) => path.startsWith('models.') && !path.startsWith('models.main.'))) setRolesExpanded(true)
      // A save can succeed before startup fails; route storage failures to recovery.
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: managementKeys.config() }),
        queryClient.invalidateQueries({ queryKey: managementKeys.setup() }),
      ])
    },
  })
  const probe = useMutation({
    mutationFn: async () => {
      probeController.current = new AbortController()
      try {
        return await testModel({ name: String(value('models.main.name') ?? ''), base_url: String(value('models.main.base_url') ?? ''), api_key: keyValue, reasoning_effort: String(value('models.main.reasoning_effort') ?? '') }, probeController.current.signal)
      } catch (error) {
        if (error instanceof DOMException && error.name === 'AbortError') return { ok: false, message: 'Connection test canceled. Your settings have not changed.' }
        throw error
      } finally { probeController.current = null }
    },
  })
  const errors = setupErrors(save.error)
  useEffect(() => {
    if (!save.isError) return
    const field = formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')
    field?.focus()
    field?.scrollIntoView({ block: 'center' })
  }, [save.isError, save.error])
  const busy = save.isPending || probe.isPending
  const set = (path: string, next: JsonValue) => {
    setChanges((current) => {
      const updated = { ...current }
      if (next === configValue(saved.values, path)) delete updated[path]
      else updated[path] = next
      return updated
    })
    setPrimaryApplied(false)
    setNotice('')
    save.reset()
    probe.reset()
  }
  const submit = (event: FormEvent) => { event.preventDefault(); if (!busy) save.mutate() }

  return <form className="space-y-6" onSubmit={submit} ref={formRef}>
    <UnsavedChanges dirty={dirty} />
    <section className="rounded-xl border border-border bg-card p-5 sm:p-6">
      <div className="flex items-center gap-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">1</span><h2 className="font-semibold">Primary model</h2></div>
      <p className="mt-2 text-sm text-muted-foreground">Enter an OpenAI-compatible endpoint and the model you want to use.</p>
      <div className="mt-5 grid gap-5 sm:grid-cols-2">
        {Object.entries(modelSchema.properties ?? {}).map(([key, field]) => <SchemaField showApplyMode={false} disabled={busy} error={errors[`models.main.${key}`]} key={key} onChange={(next) => set(`models.main.${key}`, next)} path={`models.main.${key}`} root={schema} schema={field} value={value(`models.main.${key}`)} />)}
      </div>
      {!keyName ? <Button className="mt-3 h-auto max-w-full whitespace-normal py-2" disabled={busy} onClick={() => set('models.main.api_key_secret', 'PRIMARY_API_KEY')} size="sm" type="button" variant="outline">Use suggested key reference: PRIMARY_API_KEY</Button> : null}
    </section>
    <section className="rounded-xl border border-border bg-card p-5 sm:p-6">
      <div className="flex items-center gap-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">2</span><h2 className="font-semibold">Credentials and roles</h2></div>
      <div className="mt-5"><SecretFields configured={saved.secrets} errors={errors} names={referencedKeys} values={keyValues} disabled={busy} onChange={(name, next) => { setKeyValues((current) => ({ ...current, [name]: next })); setNotice(''); save.reset(); probe.reset() }} /></div>
      <div className="mt-5 rounded-lg border border-border p-4">
        <p className="text-sm">Primary, Title, Activity, Permission / Skill Review and Compact can share the primary endpoint, model and key reference. Each role keeps its own reasoning and context settings.</p>
        <Button className="mt-3 h-auto max-w-full whitespace-normal py-2" disabled={busy} onClick={() => {
          const next = { ...changes }
          for (const role of roles) for (const field of ['name', 'base_url', 'api_key_secret']) {
            const path = `models.${role}.${field}`
            const primary = value(`models.main.${field}`) ?? null
            if (primary === configValue(saved.values, path)) delete next[path]
            else next[path] = primary
          }
          setChanges(next)
          setPrimaryApplied(true)
          setNotice('')
          save.reset()
        }} type="button" variant="outline">Apply primary settings to all roles</Button>
        {primaryApplied ? <p className="mt-2 text-xs text-muted-foreground" role="status">Applied to the draft. Review or customize the roles below before saving.</p> : null}
      </div>
      <details className="mt-4" onToggle={(event) => setRolesExpanded(event.currentTarget.open)} open={rolesExpanded}>
        <summary className="cursor-pointer rounded-md py-2 text-sm font-medium focus-visible:outline-ring">Review and customize other roles</summary>
        <div className="mt-3 space-y-5">{roles.filter((role) => role !== 'main').map((role) => <fieldset className="rounded-lg border border-border p-4" key={role}>
          <legend className="px-2 text-sm font-medium">{fieldLabel(role)}</legend>
          <div className="grid gap-4 sm:grid-cols-2">{Object.entries(modelSchema.properties ?? {}).map(([key, field]) => <SchemaField showApplyMode={false} disabled={busy} error={errors[`models.${role}.${key}`]} key={key} onChange={(next) => set(`models.${role}.${key}`, next)} path={`models.${role}.${key}`} root={schema} schema={field} value={value(`models.${role}.${key}`)} />)}</div>
        </fieldset>)}</div>
      </details>
    </section>
    <section className="space-y-4 rounded-xl border border-border bg-card p-5 sm:p-6">
      <div className="flex items-center gap-3"><span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold">3</span><h2 className="font-semibold">Check and finish</h2></div>
      <p className="text-sm text-muted-foreground">Connection testing is optional. It uses the key entered above for one request to your endpoint.</p>
      {saved.missing.length ? <p className="text-xs text-muted-foreground">Saved setup is incomplete. Saving will validate the model roles and keys together.</p> : null}
      <ErrorNotice error={probe.error} fallback="The connection test failed. Your settings have not been changed." />
      {probe.data ? <p className={`text-sm ${probe.data.ok ? 'text-muted-foreground' : 'text-destructive'}`} role="status">{probe.data.message}</p> : null}
      <ErrorNotice error={save.error} fallback="Setup could not finish. Your saved configuration has been retained." />
      {notice ? <p className="text-sm" role="status">{notice}</p> : null}
      <div className="flex flex-wrap gap-3">
        <Button disabled={busy || !dirty} onClick={() => { setChanges({}); setKeyValues({}); setPrimaryApplied(false); setNotice(''); save.reset(); probe.reset() }} type="button" variant="ghost">Discard changes</Button>
        <Button disabled={busy || !keyValue} onClick={() => probe.mutate()} type="button" variant="outline">{probe.isPending ? 'Testing…' : 'Test connection'}</Button>
        {probe.isPending ? <Button onClick={() => probeController.current?.abort()} type="button" variant="ghost">Cancel test</Button> : null}
        <Button disabled={busy} type="submit">{save.isPending ? 'Opening InvestOrch…' : 'Save and open InvestOrch'}</Button>
      </div>
    </section>
  </form>
}

export function SetupPage({ startupError }: { startupError?: string }) {
  const config = useQuery(configQueryOptions())
  const schema = useQuery(schemaQueryOptions())
  return <div className="mx-auto max-w-3xl px-4 py-10 sm:px-6 sm:py-16">
    <header className="mb-8">
      <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">InvestOrch Agent</p>
      <h1 className="mt-3 text-3xl font-semibold tracking-tight">Set up your workspace</h1>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">Connect your model service to start. You can change individual roles and other preferences in Settings later.</p>
    </header>
    {startupError ? <p className="mb-5 break-words rounded-lg border border-destructive/25 bg-destructive/5 p-4 text-sm text-destructive" role="alert">{startupError}</p> : null}
    <ErrorNotice error={config.error ?? schema.error} fallback="Setup settings could not be loaded." />
    {config.isPending || schema.isPending ? <p role="status" className="text-sm text-muted-foreground">Loading setup…</p> : null}
    {config.data && schema.data ? <SetupForm saved={config.data} schema={schema.data} /> : null}
    {config.isError || schema.isError ? <Button className="mt-4" onClick={() => { void config.refetch(); void schema.refetch() }} type="button" variant="outline">Retry</Button> : null}
    <Link className="mt-6 inline-block text-sm underline" to="/system">View local diagnostics</Link>
  </div>
}
