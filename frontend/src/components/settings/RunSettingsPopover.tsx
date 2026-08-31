import { useEffect, useId, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Settings } from 'lucide-react'

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

export function RunSettingsPopover() {
  const queryClient = useQueryClient()
  const reasoningId = useId()
  const permissionId = useId()
  const followUpId = useId()
  const [narrowViewport, setNarrowViewport] = useState(() => window.matchMedia('(max-width: 767px)').matches)
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

  useEffect(() => {
    const mediaQuery = window.matchMedia('(max-width: 767px)')
    const updateViewport = () => setNarrowViewport(mediaQuery.matches)
    mediaQuery.addEventListener('change', updateViewport)
    return () => mediaQuery.removeEventListener('change', updateViewport)
  }, [])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (draft) {
      mutation.mutate(draft)
    }
  }

  return (
    <div className="relative">
      <Popover open={open} onOpenChange={handleOpenChange}>
        <PopoverTrigger asChild>
          <button
            aria-expanded={open}
            aria-haspopup="dialog"
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-muted disabled:opacity-60"
            disabled={mutation.isPending}
            type="button"
          >
            <Settings aria-hidden="true" size={16} />
            Settings
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="end"
          aria-label="Run settings"
          collisionPadding={12}
          onEscapeKeyDown={(event) => {
            if (mutation.isPending) {
              event.preventDefault()
            }
          }}
          side={narrowViewport ? 'top' : 'right'}
          sideOffset={8}
          className="z-50 max-h-[calc(100dvh-1.5rem)] w-72 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl border border-border bg-card p-4 shadow-xl md:z-30"
        >
          <h2 className="text-sm font-semibold">Run settings</h2>
          <p className="mt-1 text-xs text-muted-foreground">Changes apply to future runs.</p>

          {defaultsQuery.isPending ? (
            <p className="mt-4 text-sm text-muted-foreground" role="status">
              Loading settings…
            </p>
          ) : null}
          {defaultsQuery.isError ? (
            <div className="mt-4 text-sm" role="alert">
              <p className="text-red-700">
                {errorMessage(defaultsQuery.error, 'Run settings could not be loaded.')}
              </p>
              <button className="mt-2 underline" onClick={() => void defaultsQuery.refetch()} type="button">
                Retry
              </button>
            </div>
          ) : null}

          {draft ? (
            <form className="mt-4 space-y-3" onSubmit={submit}>
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
                    {option}
                  </option>
                ))}
              </select>

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
                    {option}
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
                    {option}
                  </option>
                ))}
              </select>

              {mutation.error ? (
                <p className="text-xs text-red-700" role="alert">
                  {errorMessage(mutation.error, 'Run settings could not be saved. Try again.')}
                </p>
              ) : null}

              <div className="flex justify-end gap-2 pt-1">
                <button
                  className="rounded-lg border border-border px-3 py-1.5 text-sm hover:bg-muted disabled:opacity-60"
                  disabled={mutation.isPending}
                  onClick={() => setOpen(false)}
                  type="button"
                >
                  Cancel
                </button>
                <button
                  className="rounded-lg bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-60"
                  disabled={mutation.isPending}
                  type="submit"
                >
                  {mutation.isPending ? 'Saving…' : 'Save'}
                </button>
              </div>
            </form>
          ) : null}
        </PopoverContent>
      </Popover>
    </div>
  )
}
