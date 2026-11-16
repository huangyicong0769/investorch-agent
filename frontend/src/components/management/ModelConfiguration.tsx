import { useState } from 'react'
import type { JsonObject, JsonValue } from '../../api/types'
import { Button } from '@/components/ui/button'
import { fieldClass } from './SchemaField'
import { objectValue } from './schema'
import { ConnectionTest } from './ConnectionTest'
import { ChatGPTConnection } from './ChatGPTConnection'
import { AGENT_ROLES, ModelFields } from './ModelFields'
import { SecretField } from './SecretFields'

export function modelConfigValues(saved: JsonObject, changes: Record<string, JsonValue>, section: string): JsonObject {
  const result: JsonObject = structuredClone(objectValue(saved[section]))
  for (const [path, value] of Object.entries(changes)) {
    const [root, id, field] = path.split('.')
    if (root !== section || !id) continue
    if (field) result[id] = { ...objectValue(result[id]), [field]: value }
    else result[id] = value
  }
  return result
}

export function modelConfigChanges(saved: JsonObject, changes: Record<string, JsonValue>): Record<string, JsonValue> {
  const updates = Object.fromEntries(Object.entries(changes).filter(([path]) => !path.startsWith('models.') && !path.startsWith('agents.')))
  for (const section of ['models', 'agents']) {
    const values = modelConfigValues(saved, changes, section)
    const ids = new Set(Object.keys(changes).filter((path) => path.startsWith(`${section}.`)).map((path) => path.split('.')[1]))
    for (const id of ids) updates[`${section}.${id}`] = values[id]
  }
  return updates
}

export function ModelConfiguration({ connections, agents, savedConnections, apiKeys, onChange, onDelete, disabled, errors = {} }: {
  connections: JsonObject
  agents: JsonObject
  savedConnections: JsonObject
  apiKeys: {
    configured: Record<string, { configured: boolean }>
    values: Record<string, string>
    onChange: (name: string, value: string) => void
  }
  onChange: (path: string, value: JsonValue) => void
  onDelete?: (id: string) => void
  disabled?: boolean
  errors?: Record<string, string>
}) {
  const [newId, setNewId] = useState('')
  const validId = /^[A-Za-z0-9_-]+$/.test(newId) && !(newId in connections)
  return <>
    <section className="space-y-5 rounded-xl border border-border bg-card p-5">
      <div><h2 className="font-semibold">Model Connections</h2><p className="mt-2 text-sm text-muted-foreground">Connect an API endpoint or a ChatGPT account. Multiple Agents and Sessions can share a connection.</p></div>
      {Object.entries(connections).map(([id, raw]) => {
        const connection = objectValue(raw)
        const chatgpt = connection.type === 'chatgpt'
        const keyName = String(connection.api_key_secret || `${id}_KEY`)
        const connectionField = (key: string, label: string) => {
          const path = `models.${id}.${key}`
          const error = errors[path]
          return <label className="block text-sm">{label}
            <input aria-invalid={Boolean(error)} className={`${fieldClass} mt-2`} disabled={disabled} onChange={(event) => onChange(path, event.target.value)} value={String(connection[key] ?? '')} />
            {error ? <span className="mt-1 block text-xs text-destructive" role="alert">{error}</span> : null}
          </label>
        }
        return <fieldset className="space-y-4 rounded-lg border border-border p-4" key={id}>
          <legend className="px-2 text-sm font-medium">{id}</legend>
          <label className="block text-sm">Connection type<select className={`${fieldClass} mt-2`} disabled={disabled} onChange={(e) => {
            const type = e.target.value
            if (type === 'chatgpt') onChange(`models.${id}`, { type, credentials: id })
            else if (type === 'deepseek') onChange(`models.${id}`, { type, base_url: 'https://api.deepseek.com', api_key_secret: 'DEEPSEEK_API_KEY' })
            else onChange(`models.${id}`, { type, base_url: 'https://api.openai.com/v1', api_key_secret: `${id}_KEY` })
          }} value={String(connection.type ?? 'openai_compatible')}><option value="openai_compatible">OpenAI-compatible API key</option><option value="deepseek">DeepSeek API key</option><option value="chatgpt">ChatGPT plan usage</option></select></label>
          {chatgpt ? connectionField('credentials', 'Credential reference') : <>
            {connectionField('base_url', 'API endpoint')}
            <SecretField
              configured={apiKeys.configured[keyName]?.configured ?? false}
              disabled={disabled}
              error={errors[`secrets.${keyName}`]}
              label="API key"
              onChange={(value) => {
                if (value && !connection.api_key_secret) onChange(`models.${id}.api_key_secret`, keyName)
                apiKeys.onChange(keyName, value)
              }}
              value={apiKeys.values[keyName] ?? ''}
            />
            <details open={errors[`models.${id}.api_key_secret`] ? true : undefined}>
              <summary className="cursor-pointer text-xs text-muted-foreground">Advanced key settings</summary>
              <div className="mt-3 space-y-2">
                {connectionField('api_key_secret', 'Secret name')}
                <p className="text-xs text-muted-foreground">Name used to store this key in local TOML.</p>
              </div>
            </details>
          </>}
          <p className="text-xs text-muted-foreground">Used by {Object.values(agents).filter((agent) => objectValue(agent).model === id).length} Agent defaults. Session references are checked before deletion.</p>
          {chatgpt ? objectValue(savedConnections[id]).type === 'chatgpt' && objectValue(savedConnections[id]).credentials === connection.credentials ? <ChatGPTConnection connection={id} disabled={disabled} /> : <p className="text-sm text-muted-foreground">Save this connection before signing in with ChatGPT.</p> : null}
          {id in savedConnections ? <ConnectionTest connection={objectValue(savedConnections[id])} disabled={disabled} id={id} /> : null}
          {onDelete && id in savedConnections ? <Button disabled={disabled} onClick={() => onDelete(id)} size="sm" type="button" variant="outline">Delete connection</Button> : null}
        </fieldset>
      })}
      <div className="flex flex-wrap items-end gap-2"><label className="min-w-0 flex-1 text-sm">New connection ID<input className={`${fieldClass} mt-2`} disabled={disabled} onChange={(e) => setNewId(e.target.value)} placeholder="e.g. chatgpt_personal" value={newId} /></label><Button disabled={disabled || !validId} onClick={() => { onChange(`models.${newId}`, { type: 'openai_compatible', base_url: 'https://api.openai.com/v1', api_key_secret: `${newId}_KEY` }); setNewId('') }} type="button" variant="outline">Add connection</Button></div>
      {newId && !validId ? <p className="text-xs text-destructive">Choose a unique ID using letters, numbers, underscores or hyphens.</p> : null}
    </section>
    <section className="space-y-5 rounded-xl border border-border bg-card p-5">
      <div><h2 className="font-semibold">Agent Defaults</h2><p className="mt-2 text-sm text-muted-foreground">Choose a connection and actual model for each built-in role. Existing Sessions keep their own model selection.</p></div>
      {AGENT_ROLES.map((role) => <fieldset className="rounded-lg border border-border p-4" key={role}><legend className="px-2 text-sm font-medium">{role === 'permission' ? 'Permission / Skill Review' : role === 'title' ? 'Title (optional)' : role[0].toUpperCase() + role.slice(1)}</legend><ModelFields connections={connections} disabled={disabled} errors={errors} onChange={(field, next) => onChange(`agents.${role}.${field}`, next)} prefix={`agents.${role}.`} role={role} value={objectValue(agents[role])} /></fieldset>)}
      <Button disabled={disabled} onClick={() => {
        const main = objectValue(agents.main)
        for (const role of AGENT_ROLES) {
          if (role === 'main') continue
          const auxiliary = objectValue(agents[role])
          onChange(`agents.${role}`, { ...main, web_search: auxiliary.web_search ?? false, read_url: auxiliary.read_url ?? false })
        }
      }} type="button" variant="outline">Copy Main settings to auxiliary roles</Button>
      <p className="text-xs text-muted-foreground">Changes to connections, keys and Agent defaults require restart. Enter a positive context window. Changing the model clears the previous window.</p>
    </section>
  </>
}
