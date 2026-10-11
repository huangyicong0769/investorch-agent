import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { updateDefaults } from '../../api/client'
import type { ConfigSchema } from '../../api/management'
import { defaultsQueryOptions, queryKeys } from '../../api/queries'
import type { BootstrapResponse, FollowUpBehavior, JsonValue } from '../../api/types'
import { ErrorNotice } from '../management/Feedback'
import { SchemaField } from '../management/SchemaField'
import { schemaAtPath } from '../management/schema'
import { Button } from '@/components/ui/button'

export function RunDefaults({ schema }: { schema: ConfigSchema }) {
  const queryClient = useQueryClient()
  const query = useQuery(defaultsQueryOptions())
  const [draft, setDraft] = useState<JsonValue | undefined>(undefined)
  const mutation = useMutation({
    mutationFn: () => updateDefaults({ follow_up_behavior: draft as FollowUpBehavior }),
    onSuccess: (defaults) => {
      queryClient.setQueryData(queryKeys.defaults(), defaults)
      queryClient.setQueryData<BootstrapResponse>(queryKeys.bootstrap(), (current) => current ? { ...current, defaults } : current)
      setDraft(undefined)
    },
  })
  return <section className="rounded-xl border border-dashed border-border bg-card p-5">
    <h2 className="font-semibold">For this running instance</h2>
    <p className="mt-2 text-sm text-muted-foreground">Temporarily override follow-ups for future runs until InvestOrch restarts. This does not change your saved settings above.</p>
    <div className="mt-4 max-w-sm"><SchemaField disabled={query.isPending || mutation.isPending} onChange={setDraft} path="interaction.follow_up_behavior" root={schema} schema={schemaAtPath(schema, 'interaction.follow_up_behavior')} value={draft ?? query.data?.follow_up_behavior} /></div>
    <ErrorNotice error={query.error ?? mutation.error} fallback="Temporary defaults could not be loaded or updated." />
    <Button className="mt-4" disabled={draft === undefined || mutation.isPending} onClick={() => mutation.mutate()} type="button" variant="outline">{mutation.isPending ? 'Applying…' : 'Apply temporarily'}</Button>
    {mutation.isSuccess ? <p className="mt-2 text-xs text-muted-foreground" role="status">Temporary follow-up preference applied.</p> : null}
  </section>
}
