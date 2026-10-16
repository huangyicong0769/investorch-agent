import { useId } from 'react'

import type { ConfigSchema } from '../../api/management'
import type { JsonValue } from '../../api/types'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { fieldLabel, nullableSchema, resolveSchema } from './schema'

export const fieldClass = 'w-full min-w-0 rounded-lg border border-border bg-background px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/30 disabled:opacity-60'

interface SchemaFieldProps {
  path: string
  schema: ConfigSchema
  root: ConfigSchema
  value: JsonValue | undefined
  disabled?: boolean
  showApplyMode?: boolean
  overridden?: boolean
  error?: string
  onChange: (value: JsonValue) => void
  onReset?: () => void
}

export function SchemaField({ path, schema: raw, root, value, disabled, showApplyMode = true, overridden, error, onChange, onReset }: SchemaFieldProps) {
  const id = useId()
  const schema = resolveSchema(raw, root)
  const nullable = nullableSchema(raw)
  const label = schema.title ?? fieldLabel(path.split('.').at(-1) ?? path)
  const immutable = schema.apply_mode === 'immutable'
  const inputDisabled = disabled || immutable
  const type = schema.type
  const options = schema.enum ?? (schema.const !== undefined ? [schema.const] : undefined)
  const selectedIndex = options?.findIndex((option) => Object.is(option, value)) ?? -1
  const selectedValue = nullable && value == null ? 'unset' : selectedIndex < 0 ? '' : `option-${selectedIndex}`
  const description = [schema.description, schema.exclusiveMinimum === undefined ? '' : `Greater than ${schema.exclusiveMinimum}.`, schema.exclusiveMaximum === undefined ? '' : `Less than ${schema.exclusiveMaximum}.`].filter(Boolean).join(' ')

  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex items-start justify-between gap-2">
        <label className="text-sm font-medium" htmlFor={id}>{label}</label>
        {overridden && onReset && !immutable ? <Button disabled={disabled} onClick={onReset} size="xs" type="button" variant="ghost">Reset</Button> : null}
      </div>
      {type === 'boolean' && !options ? (
        <label className="flex min-h-10 items-center gap-2 text-sm" htmlFor={id}>
          <input aria-describedby={description || error ? `${id}-description` : undefined} checked={value === true} disabled={inputDisabled} id={id} onChange={(event) => onChange(event.target.checked)} type="checkbox" className="size-4 accent-primary" />
          <span>{value === true ? 'Enabled' : 'Disabled'}</span>
        </label>
      ) : options?.length === 1 && !nullable ? (
        <>
          <Input aria-describedby={`${id}-only-option${description || error ? ` ${id}-description` : ''}`} aria-invalid={Boolean(error)} className="bg-muted/40" disabled={inputDisabled} id={id} readOnly value={value == null ? String(options[0]) : String(value)} />
          <p className="text-xs text-muted-foreground" id={`${id}-only-option`}>Only supported option</p>
        </>
      ) : options ? (
        <Select disabled={inputDisabled} value={selectedValue} onValueChange={(next) => {
          if (nullable && next === 'unset') {
            onChange(null)
            return
          }
          const option = options.find((_, index) => next === `option-${index}`)
          if (option !== undefined) onChange(option)
        }}>
          <SelectTrigger aria-describedby={description || error ? `${id}-description` : undefined} aria-invalid={Boolean(error)} className="w-full min-w-0 rounded-lg bg-background" id={id}>
            <SelectValue placeholder="Select…" />
          </SelectTrigger>
          <SelectContent align="start" position="popper">
            {nullable ? <SelectItem value="unset">Not set</SelectItem> : null}
            {options.map((option, index) => <SelectItem key={index} value={`option-${index}`}>{option === '' ? 'Empty string' : String(option)}</SelectItem>)}
          </SelectContent>
        </Select>
      ) : (
        <Input aria-describedby={description || error ? `${id}-description` : undefined} aria-invalid={Boolean(error)} autoComplete="off" className={fieldClass} disabled={inputDisabled} id={id} max={schema.maximum} maxLength={schema.maxLength} min={schema.minimum} minLength={schema.minLength} onChange={(event) => {
          const next = event.target.value
          onChange(next === '' && nullable ? null : type === 'number' || type === 'integer' ? next === '' ? '' : Number(next) : next)
        }} placeholder={nullable ? 'Not set' : undefined} step={type === 'integer' ? 1 : 'any'} type={type === 'number' || type === 'integer' ? 'number' : 'text'} value={value == null ? '' : String(value)} />
      )}
      {description || error ? <p className={`text-xs ${error ? 'text-destructive' : 'text-muted-foreground'}`} id={`${id}-description`}>{error ?? description}</p> : null}
      {showApplyMode ? <p className="text-[11px] text-muted-foreground">{immutable ? 'Read only' : schema.apply_mode === 'restart_required' ? 'Restart required after saving' : 'Applies to future work'}{overridden ? ' · Local override' : ''}</p> : null}
    </div>
  )
}
