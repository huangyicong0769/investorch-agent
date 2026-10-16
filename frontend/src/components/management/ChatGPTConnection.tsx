import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { cancelChatGPT, chatGPTStatusOptions, loginChatGPT, logoutChatGPT, managementKeys } from '../../api/management'
import { Button } from '@/components/ui/button'
import { ErrorNotice } from './Feedback'

export function ChatGPTConnection({ connection, disabled }: { connection: string; disabled?: boolean }) {
  const client = useQueryClient()
  const [pending, setPending] = useState<{ state: string; authorization_url: string } | null>(null)
  const [notice, setNotice] = useState('')
  const status = useQuery({ ...chatGPTStatusOptions(connection), refetchInterval: pending ? 2000 : false })
  const refresh = async () => { await client.invalidateQueries({ queryKey: managementKeys.config() }) }
  const login = useMutation({ mutationFn: () => loginChatGPT(connection, status.data?.status === 'connected'), onSuccess: (result) => { setPending(result); setNotice('Open the sign-in page to authorize ChatGPT plan usage.'); window.open(result.authorization_url, '_blank', 'noopener,noreferrer') } })
  const cancel = useMutation({ mutationFn: () => pending ? cancelChatGPT(pending.state) : Promise.resolve(), onSuccess: () => { setPending(null); setNotice('Sign-in closed. Existing authorization is retained.'); void refresh() } })
  const logout = useMutation({ mutationFn: () => logoutChatGPT(connection), onSuccess: (result) => { setPending(null); setNotice(result.message); void refresh() } })
  const busy = disabled || login.isPending || logout.isPending || cancel.isPending
  return <div className="space-y-3 rounded-lg bg-muted/50 p-3">
    <p className="text-sm">{status.data?.status === 'connected' ? `${status.data.display_name ?? 'ChatGPT account'} · ${status.data.plan_use ? 'ChatGPT plan usage enabled' : 'Identity connected; plan usage not enabled'}` : status.isPending ? 'Checking authorization…' : 'ChatGPT account disconnected'}</p>
    <div className="flex flex-wrap gap-2"><Button disabled={busy || Boolean(pending)} onClick={() => login.mutate()} size="sm" type="button" variant="outline">{status.data?.status === 'connected' ? 'Reauthorize ChatGPT plan usage' : 'Continue with ChatGPT'}</Button>{status.data?.status === 'connected' ? <Button disabled={busy} onClick={() => logout.mutate()} size="sm" type="button" variant="ghost">Sign out</Button> : null}{status.data?.manage_usage_url ? <a className="self-center text-sm underline" href={status.data.manage_usage_url} rel="noreferrer" target="_blank">Manage Usage</a> : null}</div>
    {pending ? <div className="flex flex-wrap items-center gap-3 text-sm"><a href={pending.authorization_url} rel="noreferrer" target="_blank">Open sign-in page ↗</a><Button disabled={busy} onClick={() => cancel.mutate()} size="sm" type="button" variant="ghost">{status.data?.plan_use ? 'Done' : 'Cancel sign-in'}</Button></div> : null}
    <ErrorNotice error={login.error ?? cancel.error ?? logout.error ?? status.error} fallback="ChatGPT authorization could not be updated." />
    {status.data?.error ? <p className="text-xs text-destructive" role="alert">{status.data.error.message}</p> : null}
    {notice ? <p className="text-xs text-muted-foreground" role="status">{notice}</p> : null}
  </div>
}
