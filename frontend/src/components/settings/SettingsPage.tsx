import { useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { configQueryOptions, managementKeys, resetConfig, saveConfig, schemaQueryOptions, type ConfigSchema, type SavedConfig } from '../../api/management'
import { defaultsQueryOptions, bootstrapQueryOptions } from '../../api/queries'
import type { JsonValue, WebConfig } from '../../api/types'
import { useThemePreview } from '../../config/WebConfigContext'
import { cn } from '../../lib/utils'
import { ErrorNotice, fieldErrors } from '../management/Feedback'
import { SchemaField } from '../management/SchemaField'
import { ModelConfiguration, modelConfigValues, modelConfigChanges } from '../management/ModelConfiguration'
import { SecretFields } from '../management/SecretFields'
import { configValue, fieldLabel, objectValue, resolveSchema, schemaAtPath } from '../management/schema'
import { UnsavedChanges } from '../management/UnsavedChanges'
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { McpPanel } from './McpPanel'
import { AdvancedSettings } from './AdvancedSettings'
import { isSettingsChoice, SettingsChoiceField } from './SettingsChoiceField'

const categories = ['General', 'Appearance', 'Models', 'Safety & Approval', 'Context', 'Images', 'Backtesting', 'Integrations', 'Advanced'] as const
type Category = typeof categories[number]
const categoryDescriptions: Record<Category, string> = {
  General: 'Choose the defaults for everyday conversations.',
  Appearance: 'Make this workspace your own. Preview changes here, then save to keep them.',
  Models: 'Manage connections and choose the model used by each Agent role.',
  'Safety & Approval': 'Control how actions are reviewed and approved.',
  Context: 'Manage conversation compaction and activity summaries.',
  Images: 'Choose how images are sent to models and set attachment limits.',
  Backtesting: 'Set defaults for backtests, trading costs and market rules.',
  Integrations: 'Connect external tools and configure data integrations.',
  Advanced: 'Fine-tune run limits, client behavior and diagnostics. Inspect where your data is stored.',
}

function SettingsEditor({ saved, schema }: { saved: SavedConfig; schema: ConfigSchema }) {
  const queryClient = useQueryClient()
  const [category, setCategory] = useState<Category>('General')
  const editorRef = useRef<HTMLDivElement>(null)
  const selectCategory = (next: Category) => {
    setCategory(next)
    editorRef.current?.scrollIntoView({ block: 'start' })
  }
  const [changes, setChanges] = useState<Record<string, JsonValue>>({})
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [notice, setNotice] = useState('')
  const dirty = Object.keys(changes).length > 0 || Object.values(secrets).some(Boolean)
  const value = (path: string) => path in changes ? changes[path] : configValue(saved.values, path)
  const previewTheme = useThemePreview()
  const themeColor = value('web.theme_color') as WebConfig['theme_color']
  const themeMode = value('web.theme_mode') as WebConfig['theme_mode']
  useLayoutEffect(() => {
    previewTheme({ theme_color: themeColor, theme_mode: themeMode })
    return () => previewTheme(null)
  }, [previewTheme, themeColor, themeMode])
  const refresh = async (requiresRestart: boolean) => {
    setNotice(requiresRestart ? 'Saved. Restart InvestOrch to apply fields marked “Restart required”. Other saved changes apply to future work.' : 'Saved.')
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: managementKeys.config() }),
      queryClient.invalidateQueries({ queryKey: defaultsQueryOptions().queryKey }),
      queryClient.invalidateQueries({ queryKey: bootstrapQueryOptions().queryKey }),
    ])
  }
  const save = useMutation({
    mutationFn: () => {
      const updates = modelConfigChanges(saved.values, changes)
      for (const [name, key] of Object.entries(secrets)) if (key) updates[`secrets.${name}`] = key
      return saveConfig(updates)
    },
    onSuccess: async (result) => { await refresh(result.requires_restart); setChanges({}); setSecrets({}) },
  })
  const reset = useMutation({ mutationFn: (path: string) => resetConfig([path]), onSuccess: (result) => refresh(result.requires_restart) })
  const errors = fieldErrors(save.error ?? reset.error)
  const busy = save.isPending || reset.isPending
  const change = (path: string, next: JsonValue) => {
    setChanges((current) => {
      const updated = Object.fromEntries(Object.entries(current).filter(([key]) => !key.startsWith(`${path}.`)))
      if (next === configValue(saved.values, path)) delete updated[path]
      else updated[path] = next
      return updated
    })
    setNotice('')
    save.reset()
    reset.reset()
  }
  const renderFields = (path: string, omit: string[] = [], singleColumn = false): ReactNode => {
    const raw = schemaAtPath(schema, path)
    const field = resolveSchema(raw, schema)
    if (field.properties || field.patternProperties || typeof field.additionalProperties === 'object') {
      const entries = Object.keys(field.properties ?? objectValue(configValue(saved.values, path))).filter((key) => !omit.includes(key))
      return <div className={cn('grid min-w-0 gap-5', !singleColumn && 'sm:grid-cols-2')}>{entries.map((key) => {
        const child = schemaAtPath(schema, `${path}.${key}`)
        const resolved = resolveSchema(child, schema)
        return resolved.properties ? <fieldset className={cn('min-w-0 rounded-xl border border-border p-4', !singleColumn && 'sm:col-span-2')} key={key}><legend className="px-2 text-sm font-semibold">{fieldLabel(key)}</legend>{renderFields(`${path}.${key}`, [], singleColumn)}</fieldset> : <div className={cn(isSettingsChoice(`${path}.${key}`) && !singleColumn && 'sm:col-span-2')} key={key}>{renderFields(`${path}.${key}`)}</div>
      })}</div>
    }
    if (isSettingsChoice(path) && field.enum?.length) {
      return <SettingsChoiceField disabled={busy} error={errors[path]} onChange={(next) => change(path, next)} onReset={dirty ? undefined : () => reset.mutate(path)} overridden={saved.overrides.includes(path)} path={path} schema={field} value={value(path)} />
    }
    return <SchemaField disabled={busy} error={errors[path]} onChange={(next) => change(path, next)} onReset={dirty ? undefined : () => reset.mutate(path)} overridden={saved.overrides.includes(path)} path={path} root={schema} schema={raw} showApplyMode={!['web.theme_color', 'web.theme_mode'].includes(path)} value={value(path)} />
  }
  const section = (title: string, path: string, description?: string, omit?: string[]) => <section key={path}><Card className="gap-5 rounded-xl border-border py-5 shadow-none"><CardHeader className="gap-2 px-5"><CardTitle><h2>{title}</h2></CardTitle>{description ? <CardDescription className="leading-6">{description}</CardDescription> : null}</CardHeader><CardContent className="px-5">{renderFields(path, omit)}</CardContent></Card></section>
  const connections = modelConfigValues(saved.values, changes, 'models')
  const agents = modelConfigValues(saved.values, changes, 'agents')
  const referencedSecrets = Object.values(connections).map((connection) => String(objectValue(connection).api_key_secret ?? '')).filter(Boolean)

  return <>
    <UnsavedChanges dirty={dirty} />
    <div className="mt-8 grid scroll-mt-20 gap-6 md:scroll-mt-6 lg:grid-cols-[11rem_minmax(0,1fr)]" ref={editorRef}>
      <nav aria-label="Settings categories" className="flex gap-1 overflow-x-auto pb-2 lg:sticky lg:top-6 lg:block lg:self-start">
        {categories.map((item) => <Button aria-current={category === item ? 'page' : undefined} className={cn('shrink-0 justify-start lg:mb-1 lg:w-full', category === item && 'bg-accent text-accent-foreground')} key={item} onClick={() => selectCategory(item)} type="button" variant="ghost">{item}</Button>)}
      </nav>
      <div className="min-w-0 space-y-5">
        <div><h2 className="text-lg font-semibold">{category}</h2><p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{categoryDescriptions[category]}</p></div>
        {category === 'General' ? <>
          {section('General preferences', 'runtime.default_timezone')}
          {section('Follow-up messages', 'interaction', 'When you send another message during a run, steer the current work or queue it for later. This default is shared across clients.')}
        </> : null}
        {category === 'Appearance' ? <>
          {section('Appearance mode', 'web.theme_mode', 'Choose a light or dark workspace, or follow your device automatically.')}
          {section('Color palette', 'web.theme_color', 'Choose an accent for buttons, navigation and surfaces. Each palette includes light and dark shades.')}
        </> : null}
        {category === 'Models' ? <>
          <ModelConfiguration agents={agents} connections={connections} disabled={busy} errors={errors} onChange={change} onDelete={dirty ? undefined : (id) => reset.mutate(`models.${id}`)} savedConnections={objectValue(saved.values.models)} />
          <section className="rounded-xl border border-border bg-card p-5"><h2 className="font-semibold">API keys</h2><p className="mt-2 text-sm text-muted-foreground">Restart InvestOrch after saving replacement keys so model clients use the new credentials.</p><div className="mt-5"><SecretFields allowNew configured={saved.secrets} disabled={busy} errors={errors} names={referencedSecrets} onChange={(name, key) => { setSecrets((current) => ({ ...current, [name]: key })); setNotice(''); save.reset(); reset.reset() }} values={secrets} /></div></section>
        </> : null}
        {category === 'Safety & Approval' ? <>
          {section('Approval policy', 'permission', 'Saved defaults for future runs. Changes to the approval policy take effect after restart.')}
          <p className="text-sm text-muted-foreground">The Permission role also reviews Skills. <Button onClick={() => selectCategory('Models')} size="sm" type="button" variant="link">Edit review model in Models</Button></p>
        </> : null}
        {category === 'Context' ? <>{section('Compaction', 'compaction')}{section('Activity summaries', 'activity')}</> : null}
        {category === 'Images' ? <>{section('Image detail', 'images.default_detail', 'The default detail level requested when sending images to a model.')}{section('Attachment limits', 'images', 'Limit the number and size of images accepted with a message.', ['default_detail'])}</> : null}
        {category === 'Backtesting' ? section('Backtesting preferences', 'backtest') : null}
        {category === 'Integrations' ? <><McpPanel />{section('MCP application policy', 'mcp')}{section('CNEquity', 'cnequity', 'Optional CNEquity data integration is enabled under Backtesting → Use cnequity.')}</> : null}
        {category === 'Advanced' ? <AdvancedSettings renderFields={renderFields} savedValues={saved.values} schema={schema} value={value} /> : null}
        <div className="sticky bottom-0 z-10 space-y-3 border-t border-border bg-background/95 py-4 backdrop-blur">
          <ErrorNotice error={save.error ?? reset.error} fallback="Configuration could not be saved." />
          {notice ? <p className="text-sm" role="status">{notice}</p> : null}
          <div className="flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-muted-foreground">{dirty ? 'Unsaved changes across settings categories' : 'No unsaved changes · Reset removes a local override'}</p><div className="flex gap-2"><Button disabled={!dirty || busy} onClick={() => { setChanges({}); setSecrets({}); save.reset(); reset.reset(); setNotice('') }} type="button" variant="outline">Discard</Button><Button disabled={!dirty || busy} onClick={() => save.mutate()} type="button">{save.isPending ? 'Saving…' : 'Save changes'}</Button></div></div>
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
