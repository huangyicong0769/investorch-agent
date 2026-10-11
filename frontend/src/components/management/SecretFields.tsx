import { useId, useState } from 'react'

import { Button } from '@/components/ui/button'
import { fieldClass } from './SchemaField'

export function SecretFields({ configured, names, values, onChange, disabled = false, allowNew = false }: {
  configured: Record<string, { configured: boolean }>
  names: string[]
  values: Record<string, string>
  onChange: (name: string, value: string) => void
  disabled?: boolean
  allowNew?: boolean
}) {
  const id = useId()
  const [newName, setNewName] = useState('')
  const [addedNames, setAddedNames] = useState<string[]>([])
  const allNames = [...new Set([...names, ...addedNames])].sort()
  return <div className="space-y-4">
    {allNames.map((name, index) => <div className="min-w-0" key={name}>
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <label className="break-all text-sm font-medium" htmlFor={`${id}-${index}`}>{name}</label>
        <span className="text-xs text-muted-foreground">{configured[name]?.configured ? 'Configured' : 'Not configured'}</span>
      </div>
      <input autoComplete="new-password" className={fieldClass} disabled={disabled} id={`${id}-${index}`} onChange={(event) => onChange(name, event.target.value)} placeholder={configured[name]?.configured ? 'Leave empty to keep current key' : 'Enter API key'} type="password" value={values[name] ?? ''} />
    </div>)}
    {!allNames.length ? <p className="text-sm text-muted-foreground">Set a key reference in the model settings to enter its API key here.</p> : null}
    {allowNew ? <div className="flex flex-wrap items-end gap-2 border-t border-border pt-4">
      <div className="min-w-0 flex-1"><label className="mb-2 block text-sm font-medium" htmlFor={`${id}-new`}>New secret name</label><input className={fieldClass} disabled={disabled} id={`${id}-new`} onChange={(event) => setNewName(event.target.value)} value={newName} /></div>
      <Button disabled={disabled || !newName} onClick={() => { setAddedNames((current) => [...current, newName]); setNewName('') }} type="button" variant="outline">Add key field</Button>
    </div> : null}
    <p className="text-xs text-muted-foreground">Keys are write-only. Leave a field empty to keep its saved value; successful saves clear the inputs.</p>
  </div>
}
