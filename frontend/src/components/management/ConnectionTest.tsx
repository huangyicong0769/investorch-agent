import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { testSavedConnection } from '../../api/management'
import type { JsonObject } from '../../api/types'
import { Button } from '@/components/ui/button'
import { ModelFields } from './ModelFields'
import { ErrorNotice } from './Feedback'

export function ConnectionTest({ id, connection, disabled }: { id: string; connection: JsonObject; disabled?: boolean }) {
  const [selection, setSelection] = useState<JsonObject>({ model: id, name: '', reasoning_effort: 'high' })
  const probe = useMutation({ mutationFn: () => testSavedConnection({ connection: id, name: String(selection.name), reasoning_effort: String(selection.reasoning_effort) }) })
  return <details className="text-sm"><summary className="cursor-pointer">Test saved connection</summary><div className="mt-3 space-y-3">
    <ModelFields connections={{ [id]: connection }} disabled={disabled || probe.isPending} onChange={(key, value) => { setSelection((current) => ({ ...current, [key]: value })); probe.reset() }} probe value={selection} />
    <p className="text-xs text-muted-foreground">Sends one short request using the saved credentials and may consume API or ChatGPT plan usage.</p>
    <Button disabled={disabled || probe.isPending || !selection.name} onClick={() => probe.mutate()} size="sm" type="button" variant="outline">{probe.isPending ? 'Testing…' : 'Test connection'}</Button>
    <ErrorNotice error={probe.error} fallback="Connection test failed." />
    {probe.data ? <p role="status" className={probe.data.ok ? 'text-muted-foreground' : 'text-destructive'}>{probe.data.message}</p> : null}
  </div></details>
}
