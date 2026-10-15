import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { configQueryOptions, managementKeys, resetConfig, saveConfig, schemaQueryOptions, type ConfigSchema, type SavedConfig } from '../../api/management'
import { defaultsQueryOptions } from '../../api/queries'
import type { JsonValue } from '../../api/types'
import { cn } from '../../lib/utils'
import { ErrorNotice, fieldErrors } from '../management/Feedback'
import { SchemaField } from '../management/SchemaField'
import { SecretFields } from '../management/SecretFields'
import { configValue, fieldLabel, objectValue, resolveSchema, schemaAtPath } from '../management/schema'
import { UnsavedChanges } from '../management/UnsavedChanges'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { McpPanel } from './McpPanel'

const categories = ['General', 'Models', 'Safety & Approval', 'Context', 'Images', 'Backtesting', 'Integrations', 'Advanced'] as const
type Category = typeof categories[number]

function SettingsEditor({ saved, schema }: { saved: SavedConfig; schema: ConfigSchema }) {
  const queryClient = useQueryClient()
  const [category, setCategory] = useState<Category>('General')
  const [changes, setChanges] = useState<Record<string, JsonValue>>({})
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState('')
  const dirty = Object.keys(changes).length > 0 || Object.values(secrets).some(Boolean)
  const value = (path: string) => path in changes ? changes[path] : configValue(saved.values, path)
  const refresh = async (requiresRestart: boolean) => {
    setNotice(requiresRestart ? 'Saved. Restart InvestOrch to apply fields marked “Restart required”. Other saved changes apply to future work.' : 'Saved. Your preferences apply to future work.')
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: managementKeys.config() }),
      queryClient.invalidateQueries({ queryKey: defaultsQueryOptions().queryKey }),
    ])
  }
  const save = useMutation({
    mutationFn: () => {
      const updates = { ...changes }
      for (const [name, key] of Object.entries(secrets)) if (key) updates[`secrets.${name}`] = key
      return saveConfig(updates)
    },
    onSuccess: async (result) => { setChanges({}); setSecrets({}); await refresh(result.requires_restart) },
  })
  const reset = useMutation({ mutationFn: (path: string) => resetConfig([path]), onSuccess: (result) => refresh(result.requires_restart) })
  const errors = fieldErrors(save.error ?? reset.error)
  const busy = save.isPending || reset.isPending
  const change = (path: string, next: JsonValue) => {
    setChanges((current) => {
      const updated = { ...current }
      if (next === configValue(saved.values, path)) delete updated[path]
      else updated[path] = next
      return updated
    })
    setNotice('')
  }
  const renderFields = (path: string, omit: string[] = []): ReactNode => {
    const raw = schemaAtPath(schema, path)
    const field = resolveSchema(raw, schema)
    if (field.properties || field.patternProperties || typeof field.additionalProperties === 'object') {
      const entries = Object.keys(field.properties ?? objectValue(configValue(saved.values, path))).filter((key) => !omit.includes(key))
      return <div className="grid min-w-0 gap-5 sm:grid-cols-2">{entries.map((key) => {
        const child = schemaAtPath(schema, `${path}.${key}`)
        const resolved = resolveSchema(child, schema)
        return resolved.properties ? <fieldset className="min-w-0 rounded-xl border border-border p-4 sm:col-span-2" key={key}><legend className="px-2 text-sm font-semibold">{fieldLabel(key)}</legend>{renderFields(`${path}.${key}`)}</fieldset> : <div key={key}>{renderFields(`${path}.${key}`)}</div>
      })}</div>
    }
    return <SchemaField disabled={busy} error={errors[path]} onChange={(next) => change(path, next)} onReset={dirty ? undefined : () => reset.mutate(path)} overridden={saved.overrides.includes(path)} path={path} root={schema} schema={raw} value={value(path)} />
  }
  const section = (title: string, path: string, description?: string, omit?: string[]) => <section key={path}><Card className="gap-5 rounded-xl border-border py-5 shadow-none"><CardHeader className="gap-2 px-5"><CardTitle><h2>{title}</h2></CardTitle>{description ? <CardDescription className="leading-6">{description}</CardDescription> : null}</CardHeader><CardContent className="px-5">{renderFields(path, omit)}</CardContent></Card></section>
  const roleNames = Object.keys(objectValue(saved.values.models))
  const referencedSecrets = roleNames.map((role) => String(value(`models.${role}.api_key_secret`) ?? '')).filter(Boolean)
  const advanced = Object.keys(schema.properties ?? {}).filter((key) => !['models', 'secrets', 'permission', 'interaction', 'compaction', 'activity', 'images', 'backtest', 'mcp', 'cnequity'].includes(key))

  return <>
    <UnsavedChanges dirty={dirty} />
    <div className="mt-8 grid gap-6 lg:grid-cols-[11rem_minmax(0,1fr)]">
      <nav aria-label="Settings categories" className="flex gap-1 overflow-x-auto pb-2 lg:sticky lg:top-6 lg:block lg:self-start">
        {categories.map((item) => <Button aria-current={category === item ? 'page' : undefined} className={cn('shrink-0 justify-start lg:mb-1 lg:w-full', category === item && 'bg-accent text-accent-foreground')} key={item} onClick={() => setCategory(item)} type="button" variant="ghost">{item}</Button>)}
      </nav>
      <div className="min-w-0 space-y-5">
        <div><h2 className="text-lg font-semibold">{category}</h2><p className="mt-1 text-sm text-muted-foreground">Editing saved configuration. Fields marked for restart retain their current runtime values until you restart.</p></div>
        {category === 'General' ? <>
          {section('General preferences', 'runtime.default_timezone')}
          {section('Saved follow-up preference', 'interaction', 'Your persistent default, shared by Web, TUI and plain-console clients.')}
        </> : null}
        {category === 'Models' ? <>
          {section('Model roles', 'models', 'Each role has its own model, endpoint, key reference and reasoning settings. Multiple roles can share a key reference.')}
          <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">API keys</h2><p className="mt-2 text-sm text-muted-foreground">Restart InvestOrch after saving replacement keys so model clients use the new credentials.</p><div className="mt-5"><SecretFields allowNew configured={saved.secrets} disabled={busy} names={[...Object.keys(saved.secrets), ...referencedSecrets]} onChange={(name, key) => { setSecrets((current) => ({ ...current, [name]: key })); setNotice('') }} values={secrets} /></div></section>
        </> : null}
        {category === 'Safety & Approval' ? <>
          {section('Approval policy', 'permission', 'Saved defaults for future runs. Changes to the approval policy take effect after restart.')}
          <p className="text-sm text-muted-foreground">The Permission role also reviews Skills. <Button onClick={() => setCategory('Models')} size="sm" type="button" variant="link">Edit review model in Models</Button></p>
        </> : null}
        {category === 'Context' ? <>{section('Compaction', 'compaction')}{section('Activity summaries', 'activity')}</> : null}
        {category === 'Images' ? section('Image inputs', 'images') : null}
        {category === 'Backtesting' ? section('Backtesting preferences', 'backtest') : null}
        {category === 'Integrations' ? <><McpPanel />{section('MCP application policy', 'mcp')}{section('CNEquity', 'cnequity', 'Optional CNEquity data integration is enabled under Backtesting → Use cnequity.')}</> : null}
        {category === 'Advanced' ? advanced.map((path) => <details className="rounded-xl border border-border bg-card p-5" key={path}><summary className="cursor-pointer text-sm font-semibold focus-visible:outline-ring">{fieldLabel(path)}</summary><div className="mt-5">{path === 'paths' ? <section aria-label="Data directories">
          <h3 className="text-sm font-semibold">Data directories · Read-only</h3>
          <dl className="mt-4 space-y-3 text-sm">{Object.entries(objectValue(saved.values.paths)).map(([key, directory]) => <div key={key}><dt className="font-medium">{fieldLabel(key)}</dt><dd className="mt-1 break-all font-mono text-muted-foreground">{String(directory)}</dd></div>)}</dl>
          <p className="mt-4 text-sm text-muted-foreground">Relative directories are resolved under Root. Changing a path does not move existing Sessions, Portfolios or Skills.</p>
          <h3 className="mt-5 text-sm font-semibold">Offline migration</h3>
          <ol className="mt-2 list-decimal space-y-2 pl-5 text-sm text-muted-foreground">
            <li>Stop all Web, TUI and console clients and other writers. Create a backup outside the data root with <code>investorch backup create --output /safe/path/backup.zip</code>, then run <code>investorch backup verify /safe/path/backup.zip</code>.</li>
            <li>Copy the existing state and workspace directories to the intended locations, retaining the originals. Update <code>paths.state</code> and <code>paths.workspace</code> in <code>investorch.toml</code> while the application is stopped. Root is selected at startup, not through this file.</li>
            <li>Run <code>investorch doctor --root /path/to/root</code> before restarting. After startup, verify your Sessions, Portfolios and Skills before removing any original data.</li>
          </ol>
        </section> : renderFields(path, path === 'runtime' ? ['default_timezone'] : [])}</div></details>) : null}
        <div className="sticky bottom-0 z-10 space-y-3 border-t border-border bg-background/95 py-4 backdrop-blur">
          <ErrorNotice error={save.error ?? reset.error} fallback="Configuration could not be saved." />
          {notice ? <p className="text-sm" role="status">{notice}</p> : null}
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">{dirty ? 'Unsaved changes across settings categories' : 'No unsaved changes · Reset removes a local override'}</p><div className="flex gap-2"><Button disabled={!dirty || busy} onClick={() => { setChanges({}); setSecrets({}); save.reset(); setNotice('') }} type="button" variant="outline">Discard</Button><Button disabled={!dirty || busy} onClick={() => save.mutate()} type="button">{save.isPending ? 'Saving…' : 'Save changes'}</Button></div></div>
        </div>
      </div>
    </div>
  </>
}

export function SettingsPage() {
  const config = useQuery(configQueryOptions())
  const schema = useQuery(schemaQueryOptions())
  return <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
    <header><h1 className="text-3xl font-semibold tracking-tight">Settings</h1><p className="mt-2 text-sm text-muted-foreground">Models, preferences and integrations for your local workspace.</p></header>
    <div className="mt-5"><ErrorNotice error={config.error ?? schema.error} fallback="Settings could not be loaded." /></div>
    {config.isPending || schema.isPending ? <p className="mt-6 text-sm text-muted-foreground" role="status">Loading settings…</p> : null}
    {config.isError || schema.isError ? <Button onClick={() => { void config.refetch(); void schema.refetch() }} type="button" variant="outline">Retry</Button> : null}
    {config.data && schema.data ? <SettingsEditor saved={config.data} schema={schema.data} /> : null}
  </div>
}
