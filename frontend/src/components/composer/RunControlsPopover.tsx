import { useEffect, useId, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { SlidersHorizontal } from 'lucide-react'

import { updateDefaults } from '../../api/client'
import { defaultsQueryOptions, queryKeys } from '../../api/queries'
import type {
  BootstrapResponse,
  Defaults,
  FollowUpBehavior,
  PermissionMode,
  ReasoningEffort,
} from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const REASONING_OPTIONS: ReasoningEffort[] = [
  'none',
  'minimal',
  'low',
  'medium',
  'high',
  'xhigh',
  'max',
]
const PERMISSION_OPTIONS: PermissionMode[] = ['manual', 'review']
const FOLLOW_UP_OPTIONS: FollowUpBehavior[] = ['steer', 'queue']

interface RunControlsPopoverProps {
  activeFollowUpBehavior: FollowUpBehavior | null
}

function titleCase(value: string): string {
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`
}

function defaultsSummary(defaults: Defaults | null | undefined): string {
  if (!defaults) {
    return 'Run controls'
  }
  return [defaults.permission_mode, defaults.reasoning_effort, defaults.follow_up_behavior]
    .map(titleCase)
    .join(' · ')
}

export function RunControlsPopover({ activeFollowUpBehavior }: RunControlsPopoverProps) {
  const queryClient = useQueryClient()
  const reasoningId = useId()
  const permissionId = useId()
  const followUpId = useId()
  const [open, setOpen] = useState(false)
  const [draft, setDraft] = useState<Defaults | null>(null)
  const defaultsQuery = useQuery(defaultsQueryOptions())
  const mutation = useMutation({
    mutationFn: (defaults: Defaults) => updateDefaults(defaults),
    onSuccess: (defaults) => {
      queryClient.setQueryData(queryKeys.defaults(), defaults)
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
        current ? { ...current, defaults } : current,
      )
      setDraft(defaults)
      setOpen(false)
    },
  })

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setOpen(false)
      return
    }

    mutation.reset()
    setDraft(defaultsQuery.data ?? null)
    setOpen(true)
  }

  useEffect(() => {
    if (defaultsQuery.data) {
      setDraft(defaultsQuery.data)
    }
  }, [defaultsQuery.data])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (draft) {
      mutation.mutate(draft)
    }
  }

  const summary = defaultsSummary(defaultsQuery.data)

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          aria-expanded={open}
          aria-haspopup="dialog"
          aria-label={`Next run controls: ${summary}`}
          className="flex max-w-full items-center gap-1.5 rounded-lg px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground disabled:opacity-60"
          disabled={mutation.isPending}
          size={null}
          title="Permission, reasoning effort, and follow-up behavior for the next run"
          type="button"
          variant={null}
        >
          <SlidersHorizontal aria-hidden="true" size={14} />
          <span className="truncate">{summary}</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        aria-label="Next run controls"
        className="z-30 max-h-[calc(100dvh-1.5rem)] w-72 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl border border-border bg-card p-4 shadow-xl"
        collisionPadding={12}
        onEscapeKeyDown={(event) => {
          if (mutation.isPending) {
            event.preventDefault()
          }
        }}
        side="top"
        sideOffset={8}
      >
        <h2 className="text-sm font-semibold">Next run</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Changes apply to future runs. The current run is unchanged.
        </p>
        {activeFollowUpBehavior ? (
          <p className="mt-1 text-xs text-muted-foreground">
            Current follow-ups: {titleCase(activeFollowUpBehavior)}
          </p>
        ) : null}

        {defaultsQuery.isPending ? (
          <p className="mt-4 text-sm text-muted-foreground" role="status">
            Loading controls…
          </p>
        ) : null}
        {defaultsQuery.isError ? (
          <div className="mt-4 text-sm" role="alert">
            <p className="text-red-700">
              {errorMessage(defaultsQuery.error, 'Run controls could not be loaded.')}
            </p>
            <Button
              className="mt-2 underline"
              onClick={() => void defaultsQuery.refetch()}
              size={null}
              type="button"
              variant={null}
            >
              Retry
            </Button>
          </div>
        ) : null}

        {draft ? (
          <form className="mt-4 space-y-3" onSubmit={submit}>
            <label className="block text-xs font-medium" htmlFor={permissionId}>
              Permission mode
            </label>
            <select
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              disabled={mutation.isPending}
              id={permissionId}
              onChange={(event) =>
                setDraft((current) =>
                  current ? { ...current, permission_mode: event.target.value as PermissionMode } : current,
                )
              }
              value={draft.permission_mode}
            >
              {PERMISSION_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {titleCase(option)}
                </option>
              ))}
            </select>

            <label className="block text-xs font-medium" htmlFor={reasoningId}>
              Reasoning effort
            </label>
            <select
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              disabled={mutation.isPending}
              id={reasoningId}
              onChange={(event) =>
                setDraft((current) =>
                  current ? { ...current, reasoning_effort: event.target.value as ReasoningEffort } : current,
                )
              }
              value={draft.reasoning_effort}
            >
              {REASONING_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {titleCase(option)}
                </option>
              ))}
            </select>

            <label className="block text-xs font-medium" htmlFor={followUpId}>
              Follow-ups
            </label>
            <select
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              disabled={mutation.isPending}
              id={followUpId}
              onChange={(event) =>
                setDraft((current) =>
                  current ? { ...current, follow_up_behavior: event.target.value as FollowUpBehavior } : current,
                )
              }
              value={draft.follow_up_behavior}
            >
              {FOLLOW_UP_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {titleCase(option)}
                </option>
              ))}
            </select>

            {mutation.error ? (
              <p className="text-xs text-red-700" role="alert">
                {errorMessage(mutation.error, 'Run controls could not be saved. Try again.')}
              </p>
            ) : null}

            <div className="flex justify-end gap-2 pt-1">
              <Button
                className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
                disabled={mutation.isPending}
                onClick={() => setOpen(false)}
                size={null}
                type="button"
                variant={null}
              >
                Cancel
              </Button>
              <Button
                className="rounded-lg bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
                disabled={mutation.isPending}
                size={null}
                type="submit"
                variant={null}
              >
                {mutation.isPending ? 'Saving…' : 'Save'}
              </Button>
            </div>
          </form>
        ) : null}
      </PopoverContent>
    </Popover>
  )
}
