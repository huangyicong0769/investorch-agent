import { useEffect, useId, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Settings } from 'lucide-react'

import { updateDefaults } from '../../api/client'
import { defaultsQueryOptions, queryKeys } from '../../api/queries'
import type { BootstrapResponse, Defaults, FollowUpBehavior } from '../../api/types'
import { errorMessage } from '../../lib/errors'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

const FOLLOW_UP_OPTIONS: FollowUpBehavior[] = ['steer', 'queue']

function titleCase(value: string): string {
  return `${value.slice(0, 1).toUpperCase()}${value.slice(1)}`
}

export function GlobalSettingsPopover() {
  const queryClient = useQueryClient()
  const followUpId = useId()
  const [narrowViewport, setNarrowViewport] = useState(() => window.matchMedia('(max-width: 767px)').matches)
  const [open, setOpen] = useState(false)
  const [followUpBehavior, setFollowUpBehavior] = useState<FollowUpBehavior | null>(null)
  const defaultsQuery = useQuery(defaultsQueryOptions())
  const mutation = useMutation({
    mutationFn: (value: FollowUpBehavior) => updateDefaults({ follow_up_behavior: value }),
    onSuccess: (defaults: Defaults) => {
      queryClient.setQueryData(queryKeys.defaults(), defaults)
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) =>
        current ? { ...current, defaults } : current,
      )
      setFollowUpBehavior(defaults.follow_up_behavior)
      setOpen(false)
    },
  })

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setOpen(false)
      return
    }

    mutation.reset()
    setFollowUpBehavior(defaultsQuery.data?.follow_up_behavior ?? null)
    setOpen(true)
  }

  useEffect(() => {
    if (defaultsQuery.data) {
      setFollowUpBehavior(defaultsQuery.data.follow_up_behavior)
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
    if (followUpBehavior) {
      mutation.mutate(followUpBehavior)
    }
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          aria-expanded={open}
          aria-haspopup="dialog"
          className="flex w-full items-center justify-start gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-muted disabled:opacity-60"
          disabled={mutation.isPending}
          size={null}
          type="button"
          variant={null}
        >
          <Settings aria-hidden="true" size={16} />
          Settings
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        aria-label="Global settings"
        className="z-50 max-h-[calc(100dvh-1.5rem)] w-72 max-w-[calc(100vw-1.5rem)] overflow-y-auto rounded-xl border border-border bg-card p-4 shadow-xl md:z-30"
        collisionPadding={12}
        onEscapeKeyDown={(event) => {
          if (mutation.isPending) {
            event.preventDefault()
          }
        }}
        side={narrowViewport ? 'top' : 'right'}
        sideOffset={8}
      >
        <h2 className="text-sm font-semibold">Settings</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Global future-run behavior until QMT Agent restarts.
        </p>

        {defaultsQuery.isPending ? (
          <p className="mt-4 text-sm text-muted-foreground" role="status">
            Loading settings…
          </p>
        ) : null}
        {defaultsQuery.isError ? (
          <div className="mt-4 text-sm" role="alert">
            <p className="text-red-700">
              {errorMessage(defaultsQuery.error, 'Settings could not be loaded.')}
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

        {followUpBehavior ? (
          <form className="mt-4 space-y-3" onSubmit={submit}>
            <label className="block text-xs font-medium" htmlFor={followUpId}>
              Follow-ups
            </label>
            <select
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm"
              disabled={mutation.isPending}
              id={followUpId}
              onChange={(event) => setFollowUpBehavior(event.target.value as FollowUpBehavior)}
              value={followUpBehavior}
            >
              {FOLLOW_UP_OPTIONS.map((option) => (
                <option key={option} value={option}>
                  {titleCase(option)}
                </option>
              ))}
            </select>

            {mutation.error ? (
              <p className="text-xs text-red-700" role="alert">
                {errorMessage(mutation.error, 'Settings could not be saved. Try again.')}
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
