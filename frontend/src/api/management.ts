import { queryOptions } from '@tanstack/react-query'

import { request } from './client'
import { queryKeys } from './queries'
import type { JsonObject, JsonValue, PortfolioSessionResponse } from './types'

export interface ConfigSchema {
  $ref?: string
  $defs?: Record<string, ConfigSchema>
  title?: string
  description?: string
  type?: string
  properties?: Record<string, ConfigSchema>
  patternProperties?: Record<string, ConfigSchema>
  additionalProperties?: ConfigSchema | boolean
  anyOf?: ConfigSchema[]
  enum?: JsonValue[]
  const?: JsonValue
  default?: JsonValue
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
  exclusiveMaximum?: number
  minLength?: number
  maxLength?: number
  pattern?: string
  apply_mode?: 'hot' | 'restart_required' | 'immutable'
  sensitive?: boolean
}

export interface SetupState {
  status: 'setup_required' | 'ready' | 'recovery_required'
  missing: string[]
  error?: string
}

export interface SavedConfig {
  values: JsonObject
  secrets: Record<string, { configured: boolean }>
  overrides: string[]
  missing: string[]
}

export interface ConfigUpdate {
  updated: boolean
  persisted: boolean
  applied: boolean
  requires_restart: boolean
  fields: Record<string, { applied: boolean; requires_restart: boolean }>
}

export interface DiagnosticCheck {
  code: string
  status: 'ok' | 'warning' | 'error' | 'disabled' | 'unknown'
  summary: string
  detail: string
  remediation: string
  checked_at: string
}

export interface SkillRecord {
  name: string
  description: string | null
  version: string | null
  source: { type: 'builtin' | 'created' | 'external'; origin: string | null }
  enabled: boolean
  status: 'available' | 'disabled' | 'missing' | 'invalid'
  resources: string[]
  validation_error?: string
}

export interface SkillDetail extends SkillRecord {
  content: string | null
  resource: string
  truncated: boolean
}

export type SkillAction = 'create' | 'install' | 'edit' | 'fork'

export interface McpServer {
  name: string
  transport: string
  url: string
  enabled: boolean
  cache_tools_list: boolean
  headers?: Record<string, string>
  timeout?: number
  valid: boolean
  runtime_status: string
}

export interface McpChanges {
  url?: string
  enabled?: boolean
  cache_tools_list?: boolean
  headers?: Record<string, string>
  timeout?: number
  transport?: string
}

export const managementKeys = {
  setup: () => [...queryKeys.all, 'setup'] as const,
  schema: () => [...queryKeys.all, 'config-schema'] as const,
  config: () => [...queryKeys.all, 'config'] as const,
  system: () => [...queryKeys.all, 'system'] as const,
  skills: () => [...queryKeys.all, 'skills'] as const,
  skill: (name: string, resource?: string) => [...managementKeys.skills(), name, resource ?? 'SKILL.md'] as const,
  mcp: () => [...queryKeys.all, 'mcp'] as const,
}

export const setupQueryOptions = () => queryOptions({
  queryKey: managementKeys.setup(),
  queryFn: ({ signal }) => request<SetupState>('/api/setup/state', { signal }),
  retry: false,
})
export const schemaQueryOptions = () => queryOptions({
  queryKey: managementKeys.schema(),
  queryFn: ({ signal }) => request<ConfigSchema>('/api/config/schema', { signal }),
})
export const configQueryOptions = () => queryOptions({
  queryKey: managementKeys.config(),
  queryFn: ({ signal }) => request<SavedConfig>('/api/config', { signal }),
})
export const systemQueryOptions = () => queryOptions({
  queryKey: managementKeys.system(),
  queryFn: ({ signal }) => request<{ checks: DiagnosticCheck[]; exit_code: number }>('/api/system', { signal }),
  retry: false,
  refetchOnWindowFocus: false,
})
export const skillsQueryOptions = () => queryOptions({
  queryKey: managementKeys.skills(),
  queryFn: ({ signal }) => request<{ skills: SkillRecord[] }>('/api/skills', { signal }),
})
export const skillQueryOptions = (name: string, resource?: string) => queryOptions({
  queryKey: managementKeys.skill(name, resource),
  queryFn: ({ signal }) => request<SkillDetail>(`/api/skills/${encodeURIComponent(name)}${resource ? `?resource=${encodeURIComponent(resource)}` : ''}`, { signal }),
})
export const mcpQueryOptions = () => queryOptions({
  queryKey: managementKeys.mcp(),
  queryFn: ({ signal }) => request<{ servers: McpServer[]; supported_transports: string[] }>('/api/integrations/mcp', { signal }),
})

export function saveConfig(changes: Record<string, JsonValue>, setup = false): Promise<ConfigUpdate> {
  return request(setup ? '/api/setup/config' : '/api/config', { method: 'PATCH', body: setup ? { changes } : { changes, persist: true } })
}
export function resetConfig(keys: string[]): Promise<ConfigUpdate> {
  return request('/api/config/reset', { method: 'POST', body: { keys } })
}
export function completeSetup(): Promise<SetupState> {
  return request('/api/setup/complete', { method: 'POST', body: {} })
}
export function testModel(body: { name: string; base_url: string; api_key: string; reasoning_effort?: string }, signal?: AbortSignal): Promise<{ ok: boolean; message: string }> {
  return request('/api/setup/test-model', { method: 'POST', body, signal })
}
export function startSkillSession(body: { request_id: string; action: SkillAction; name?: string }): Promise<PortfolioSessionResponse> {
  return request('/api/skill-sessions', { method: 'POST', body })
}
export function setSkillEnabled(name: string, enabled: boolean): Promise<{ name: string; restart_required: boolean; message: string }> {
  return request(`/api/skills/${encodeURIComponent(name)}`, { method: 'PATCH', body: { enabled } })
}
export function removeSkill(name: string): Promise<{ name: string; restart_required: boolean; message: string }> {
  return request(`/api/skills/${encodeURIComponent(name)}`, { method: 'DELETE', body: { confirm: true } })
}
export function saveMcp(name: string, changes: McpChanges, existing: boolean): Promise<{ server: McpServer; requires_restart: boolean }> {
  return request(`/api/integrations/mcp${existing ? `/${encodeURIComponent(name)}` : ''}`, {
    method: existing ? 'PATCH' : 'POST', body: existing ? changes : { ...changes, name },
  })
}
export function removeMcp(name: string): Promise<{ removed: boolean; requires_restart: boolean }> {
  return request(`/api/integrations/mcp/${encodeURIComponent(name)}`, { method: 'DELETE', body: { confirm: true } })
}
