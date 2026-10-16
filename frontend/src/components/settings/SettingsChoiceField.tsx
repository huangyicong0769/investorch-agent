import { useId } from 'react'
import { ArrowUpRight, Check, Hand, ListOrdered, Monitor, Moon, Scan, ScanSearch, ShieldCheck, Sparkles, Sun, type LucideIcon } from 'lucide-react'

import type { ConfigSchema } from '../../api/management'
import type { JsonValue } from '../../api/types'
import { cn } from '../../lib/utils'
import { fieldLabel } from '../management/schema'
import { Button } from '../ui/button'

// Presentation colors match the existing light/dark palettes in index.css.
const palettes: Record<string, { light: string; dark: string }> = {
  green: { light: '#24634f', dark: '#8bccb0' },
  blue: { light: '#245bbb', dark: '#9ac2ff' },
  violet: { light: '#7040b0', dark: '#cbb0f3' },
  rose: { light: '#a32d59', dark: '#f5abc5' },
  amber: { light: '#875719', dark: '#edc17c' },
  neutral: { light: '#52525b', dark: '#c4c4cc' },
}

const choices: Record<string, { label: string; icon: LucideIcon; description: string }> = {
  'web.theme_mode.system': { label: 'System', icon: Monitor, description: 'Follow your device' },
  'web.theme_mode.light': { label: 'Light', icon: Sun, description: 'A bright workspace' },
  'web.theme_mode.dark': { label: 'Dark', icon: Moon, description: 'A darker workspace' },
  'interaction.follow_up_behavior.steer': { label: 'Steer', icon: ArrowUpRight, description: 'Send guidance into the current run.' },
  'interaction.follow_up_behavior.queue': { label: 'Queue', icon: ListOrdered, description: 'Wait until the current run finishes.' },
  'permission.mode.manual': { label: 'Manual', icon: Hand, description: 'Ask you to approve actions.' },
  'permission.mode.review': { label: 'Review', icon: ShieldCheck, description: 'A reviewer checks actions and asks when needed.' },
  'images.default_detail.auto': { label: 'Auto', icon: Sparkles, description: 'Let the model service choose.' },
  'images.default_detail.low': { label: 'Low', icon: Scan, description: 'Use a lower detail level.' },
  'images.default_detail.high': { label: 'High', icon: ScanSearch, description: 'Request a higher detail level.' },
  'images.default_detail.original': { label: 'Original', icon: Scan, description: 'Request the original detail level.' },
}

const labels: Record<string, string> = {
  'web.theme_mode': 'Appearance mode',
  'web.theme_color': 'Color palette',
  'interaction.follow_up_behavior': 'Follow-up behavior',
  'permission.mode': 'Permission mode',
  'images.default_detail': 'Image detail',
}

export function isSettingsChoice(path: string) {
  return path in labels
}

interface Props {
  path: string
  schema: ConfigSchema
  value: JsonValue | undefined
  disabled: boolean
  error?: string
  overridden: boolean
  onChange: (value: JsonValue) => void
  onReset?: () => void
}

export function SettingsChoiceField({ path, schema, value, disabled, error, overridden, onChange, onReset }: Props) {
  const id = useId()
  const isPalette = path === 'web.theme_color'
  const isAppearance = path.startsWith('web.theme_')
  const immutable = schema.apply_mode === 'immutable'
  const descriptionId = `${id}-description`

  return <fieldset aria-describedby={error ? descriptionId : undefined} className="min-w-0" disabled={disabled || immutable}>
    <legend className="sr-only">{labels[path] ?? schema.title ?? fieldLabel(path)}</legend>
    {overridden ? <div className="mb-3 flex min-h-6 items-center justify-end gap-2 text-xs text-muted-foreground">
      <span>Local override</span>
      {onReset && !immutable ? <Button disabled={disabled} onClick={onReset} size="xs" type="button" variant="ghost">Reset</Button> : null}
    </div> : null}
    <div className={cn('grid gap-3', isPalette ? 'grid-cols-2 min-[360px]:grid-cols-3 xl:grid-cols-6' : path === 'web.theme_mode' ? 'grid-cols-1 min-[360px]:grid-cols-3' : 'grid-cols-1 sm:grid-cols-2')}>
      {(schema.enum ?? []).map((option, index) => {
        const metadata = choices[`${path}.${String(option)}`]
        const palette = isPalette ? palettes[String(option)] : undefined
        const Icon = metadata?.icon
        const selected = Object.is(value, option)
        return <label className="relative min-w-0 cursor-pointer" key={index}>
          <input
            aria-invalid={Boolean(error)}
            checked={selected}
            className="peer sr-only"
            name={id}
            onChange={() => onChange(option)}
            type="radio"
            value={String(option)}
          />
          <span className="flex h-full min-w-0 flex-col gap-3 rounded-xl border border-border bg-background p-3.5 transition-colors hover:border-primary/50 peer-checked:border-primary peer-checked:bg-primary/5 peer-checked:ring-1 peer-checked:ring-primary peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-card peer-disabled:cursor-not-allowed peer-disabled:opacity-50">
            <span className="flex items-center justify-between gap-2">
              {palette ? <span aria-hidden="true" className="flex size-9 shrink-0 overflow-hidden rounded-full border border-black/10"><span className="w-1/2" style={{ backgroundColor: palette.light }} /><span className="w-1/2" style={{ backgroundColor: palette.dark }} /></span> : Icon ? <Icon aria-hidden="true" className={cn('size-5', selected ? 'text-primary' : 'text-muted-foreground')} /> : null}
              <Check aria-hidden="true" className={cn('size-4 shrink-0 text-primary', !selected && 'invisible')} />
            </span>
            <span className="min-w-0">
              <span className="block break-words text-sm font-medium">{metadata?.label ?? fieldLabel(String(option))}</span>
              {metadata?.description ? <span className="mt-1 block text-xs leading-5 text-muted-foreground">{metadata.description}</span> : null}
            </span>
          </span>
        </label>
      })}
    </div>
    {error ? <p className="mt-3 text-xs text-destructive" id={descriptionId} role="alert">{error}</p> : null}
    {!isAppearance ? <p className="mt-3 text-xs text-muted-foreground">{immutable ? 'Read only' : schema.apply_mode === 'restart_required' ? 'Restart required after saving' : 'Applies to future work'}</p> : null}
  </fieldset>
}
