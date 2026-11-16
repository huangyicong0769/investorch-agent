import { useId, useState } from 'react'

import { Button } from '@/components/ui/button'
import { fieldClass } from './SchemaField'

export function SecretField({ label, configured, value, onChange, disabled, error }: {
  label: string
  configured: boolean
  value: string
  onChange: (value: string) => void
  disabled?: boolean
  error?: string
}) {
  const id = useId()
  return <div className="min-w-0">
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <label className="break-all text-sm font-medium" htmlFor={id}>{label}</label>
      <span className="text-xs text-muted-foreground">{configured ? 'Configured' : 'Not configured'}</span>
    </div>
    <input aria-describedby={error ? `${id}-error` : undefined} aria-invalid={Boolean(error)} autoComplete="new-password" className={fieldClass} disabled={disabled} id={id} onChange={(event) => onChange(event.target.value)} placeholder={configured ? 'Leave empty to keep current key' : 'Enter API key'} type="password" value={value} />
    {error ? <p className="mt-1 break-words text-xs text-destructive" id={`${id}-error`} role="alert">{error}</p> : null}
  </div>
}

export function SecretFields({ configured, excludedNames, values, onChange, disabled = false, errors = {} }: {
  configured: Record<string, { configured: boolean }>
  excludedNames: ReadonlySet<string>
  values: Record<string, string>
  onChange: (name: string, value: string) => void
  disabled?: boolean
  errors?: Record<string, string>
}) {
  const id = useId()
  const [newName, setNewName] = useState('')
  const name = newName.trim()
  const newNameError = name.includes('.') ? 'Secret names cannot contain a period (.).' : excludedNames.has(name) ? 'Edit this API key in its connection card.' : ''
  const [addedNames, setAddedNames] = useState<string[]>([])
  const allNames = [...new Set([...addedNames, ...Object.keys(values)])].filter((name) => !excludedNames.has(name)).sort()
  return <div className="space-y-4">
    {allNames.map((name) => <SecretField configured={configured[name]?.configured ?? false} disabled={disabled} error={errors[`secrets.${name}`]} key={name} label={name} onChange={(value) => onChange(name, value)} value={values[name] ?? ''} />)}
    <div className="flex flex-wrap items-end gap-2">
      <div className="min-w-0 flex-1"><label className="mb-2 block text-sm font-medium" htmlFor={`${id}-new`}>New secret name</label><input aria-describedby={newNameError ? `${id}-new-error` : undefined} aria-invalid={Boolean(newNameError)} className={fieldClass} disabled={disabled} id={`${id}-new`} onChange={(event) => setNewName(event.target.value)} value={newName} />{newNameError ? <p className="mt-1 text-xs text-destructive" id={`${id}-new-error`} role="alert">{newNameError}</p> : null}</div>
      <Button disabled={disabled || !name || Boolean(newNameError)} onClick={() => {
        setAddedNames((current) => current.includes(name) ? current : [...current, name])
        setNewName('')
      }} type="button" variant="outline">Add key field</Button>
    </div>
  </div>
}
