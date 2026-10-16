import { useState } from 'react'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, LoaderCircle, ShieldCheck, SlidersHorizontal } from 'lucide-react'

import { updateDefaults } from '../../api/client'
import { sessionModelQueryOptions, updateSessionModel } from '../../api/sessionModel'
import { defaultsQueryOptions, queryKeys } from '../../api/queries'
import type { BootstrapResponse, ReasoningEffort, UpdateDefaultsBody } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

export const RUN_DEFAULTS_MUTATION_KEY = ['run-defaults'] as const

interface ControlOption {
  value: string
  label: string
  description?: string
  change: UpdateDefaultsBody
}

const REASONING_OPTIONS: ReasoningEffort[] = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max']
const PERMISSION_OPTIONS: ControlOption[] = [
  { value: 'manual', label: 'Manual', description: 'Ask you to approve actions.', change: { permission_mode: 'manual' } },
  { value: 'review', label: 'Review', description: 'A reviewer checks actions and asks when needed.', change: { permission_mode: 'review' } },
]

function titleCase(value: string): string {
  return value === 'xhigh' ? 'Extra high' : `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`
}

function PermissionControlMenu() {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const defaultsQuery = useQuery(defaultsQueryOptions())
  const saving = useIsMutating({ mutationKey: RUN_DEFAULTS_MUTATION_KEY }) > 0
  const permission = true
  const label = permission ? 'Permission mode' : 'Reasoning effort'
  const current = defaultsQuery.data?.permission_mode
  const options = PERMISSION_OPTIONS
  const mutation = useMutation({
    mutationKey: RUN_DEFAULTS_MUTATION_KEY,
    scope: { id: 'run-defaults' },
    mutationFn: (change: UpdateDefaultsBody) => updateDefaults(change),
    onMutate: async () => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: queryKeys.defaults() }),
        queryClient.cancelQueries({ queryKey: queryKeys.bootstrap() }),
      ])
    },
    onSuccess: (defaults) => {
      queryClient.setQueryData(queryKeys.defaults(), defaults)
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (cached) =>
        cached ? { ...cached, defaults } : cached,
      )
      setOpen(false)
    },
    onError: () => {
      // A disconnected response may still have changed the server's defaults.
      void queryClient.invalidateQueries({ queryKey: queryKeys.defaults() })
    },
  })

  return (
    <DropdownMenu open={open} onOpenChange={(nextOpen) => {
      if (nextOpen) mutation.reset()
      setOpen(nextOpen)
    }}>
      <DropdownMenuTrigger asChild>
        <Button
          aria-label={`${label}: ${current ? titleCase(current) : 'Loading'}`}
          className="h-8 max-w-full gap-1.5 rounded-full px-2 text-xs font-normal text-muted-foreground hover:text-foreground"
          disabled={saving}
          size="sm"
          title={`${label} for the next run`}
          type="button"
          variant="ghost"
        >
          {mutation.isPending ? <LoaderCircle aria-hidden="true" className="size-3.5 animate-spin" /> : permission ? <ShieldCheck aria-hidden="true" className="size-3.5" /> : <SlidersHorizontal aria-hidden="true" className="size-3.5" />}
          <span className="truncate">{current ? titleCase(current) : permission ? 'Permissions' : 'Reasoning'}</span>
          <ChevronDown aria-hidden="true" className="size-3 opacity-60" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent
        align={permission ? 'start' : 'end'}
        aria-label={label}
        className={`${permission ? 'w-64' : 'w-48'} max-w-[calc(100vw-1.5rem)] rounded-xl p-1.5 shadow-lg`}
        collisionPadding={12}
        side="top"
        sideOffset={10}
      >
        <DropdownMenuLabel className="px-2.5 pb-0.5 text-xs">{label}</DropdownMenuLabel>
        <p className="px-2.5 pb-2 text-[11px] text-muted-foreground">Applies to your next run.</p>
        <DropdownMenuSeparator />
        {defaultsQuery.isPending ? <p className="px-2.5 py-2 text-xs text-muted-foreground" role="status">Loading…</p> : null}
        {defaultsQuery.isError ? (
          <div className="px-2.5 py-2 text-xs" role="alert">
            <p className="text-destructive">{errorMessage(defaultsQuery.error, 'Controls could not be loaded.')}</p>
            <Button variant="link" size="sm" className="mt-1 h-auto p-0 text-xs" onClick={() => void defaultsQuery.refetch()}>Retry</Button>
          </div>
        ) : null}
        {current ? <DropdownMenuRadioGroup value={current}>
          {options.map((option) => (
            <DropdownMenuRadioItem
              className="items-start rounded-lg py-2 pr-2.5"
              disabled={saving}
              key={option.value}
              onSelect={(event) => {
                event.preventDefault()
                if (option.value === current) setOpen(false)
                else mutation.mutate(option.change)
              }}
              value={option.value}
            >
              <span>
                <span className="block text-sm">{option.label}</span>
                {option.description ? <span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{option.description}</span> : null}
              </span>
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup> : null}
        {mutation.error ? <p className="px-2.5 py-2 text-xs text-destructive" role="alert">{errorMessage(mutation.error, 'Could not save this setting. Try again.')}</p> : null}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function EffortControlMenu({ sessionId }: { sessionId: string }) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const model = useQuery(sessionModelQueryOptions(sessionId))
  const current = model.data?.effective.reasoning_effort
  const mutation = useMutation({
    mutationKey: RUN_DEFAULTS_MUTATION_KEY,
    mutationFn: (reasoning_effort: ReasoningEffort) => updateSessionModel(sessionId, { reasoning_effort }),
    onSuccess: (view) => { client.setQueryData(queryKeys.sessionModel(sessionId), view); setOpen(false) },
    onError: () => { void client.invalidateQueries({ queryKey: queryKeys.sessionModel(sessionId) }) },
  })
  return <DropdownMenu open={open} onOpenChange={(next) => { setOpen(next); mutation.reset() }}>
    <DropdownMenuTrigger asChild><Button aria-label={`Reasoning effort: ${current ?? 'Loading'}`} className="h-8 gap-1.5 rounded-full px-2 text-xs font-normal text-muted-foreground" disabled={mutation.isPending} size="sm" type="button" variant="ghost"><SlidersHorizontal className="size-3.5" /><span>{current ? titleCase(current) : 'Reasoning'}</span><ChevronDown className="size-3" /></Button></DropdownMenuTrigger>
    <DropdownMenuContent align="end" side="top"><DropdownMenuLabel>Reasoning effort</DropdownMenuLabel><p className="max-w-56 px-2 pb-2 text-xs text-muted-foreground">Session draft for the next Run. The current Run keeps its effort.</p><DropdownMenuRadioGroup value={current}>{REASONING_OPTIONS.map((effort) => <DropdownMenuRadioItem disabled={mutation.isPending} key={effort} onSelect={(event) => { event.preventDefault(); mutation.mutate(effort) }} value={effort}>{titleCase(effort)}</DropdownMenuRadioItem>)}</DropdownMenuRadioGroup>{mutation.error || model.error ? <p className="max-w-56 px-2 text-xs text-destructive" role="alert">{errorMessage(mutation.error ?? model.error, 'Could not update Session effort.')}</p> : null}</DropdownMenuContent>
  </DropdownMenu>
}

export function RunControlMenu({ field, sessionId }: { field: 'permission_mode' | 'reasoning_effort'; sessionId: string }) {
  return field === 'permission_mode' ? <PermissionControlMenu /> : <EffortControlMenu sessionId={sessionId} />
}
