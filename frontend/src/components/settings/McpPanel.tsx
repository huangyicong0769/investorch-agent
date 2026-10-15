import { useId, useState, type FormEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Pencil, Trash2 } from 'lucide-react'

import { managementKeys, mcpQueryOptions, removeMcp, saveMcp, type McpChanges, type McpServer } from '../../api/management'
import { ErrorNotice } from '../management/Feedback'
import { fieldClass } from '../management/SchemaField'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

function McpForm({ server, transports, onSaved, onCancel, onSavingChange }: { server: McpServer | null; transports: string[]; onSaved: () => void; onCancel: () => void; onSavingChange: (saving: boolean) => void }) {
  const id = useId()
  const [name, setName] = useState(server?.name ?? '')
  const [url, setUrl] = useState('')
  const [transport, setTransport] = useState(server?.transport ?? transports[0] ?? '')
  const [headers, setHeaders] = useState('')
  const [timeout, setTimeout] = useState(server?.timeout == null ? '' : String(server.timeout))
  const [enabled, setEnabled] = useState(server?.enabled ?? true)
  const [cacheTools, setCacheTools] = useState(server?.cache_tools_list ?? false)
  const [inputError, setInputError] = useState('')
  const mutation = useMutation({
    mutationFn: (changes: McpChanges) => saveMcp(name, changes, server !== null),
    onSuccess: () => { setHeaders(''); setUrl(''); onSaved() },
    onSettled: () => onSavingChange(false),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (mutation.isPending) return
    setInputError('')
    const changes: McpChanges = { enabled, cache_tools_list: cacheTools, transport }
    if (!server || url) changes.url = url
    if (timeout) changes.timeout = Number(timeout)
    else if (server?.timeout != null) changes.timeout = null
    if (headers.trim()) {
      try {
        const parsed: unknown = JSON.parse(headers)
        if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed) || !Object.values(parsed).every((value) => typeof value === 'string')) throw new Error()
        changes.headers = parsed as Record<string, string>
      } catch {
        setInputError('Enter headers as a JSON object with string values, or leave empty to keep them.')
        return
      }
    }
    onSavingChange(true)
    mutation.mutate(changes)
  }
  return <form className="space-y-4" onSubmit={submit}>
    <div><label className="mb-2 block text-sm font-medium" htmlFor={`${id}-name`}>Server name</label><input className={fieldClass} disabled={server !== null || mutation.isPending} id={`${id}-name`} onChange={(event) => setName(event.target.value)} value={name} /></div>
    <div><label className="mb-2 block text-sm font-medium" htmlFor={`${id}-transport`}>Transport</label><Select disabled={mutation.isPending} onValueChange={setTransport} value={transport}><SelectTrigger className="h-10 w-full bg-card" id={`${id}-transport`}><SelectValue /></SelectTrigger><SelectContent>{transports.map((item) => <SelectItem key={item} value={item}>{item}</SelectItem>)}</SelectContent></Select></div>
    {server ? <p className="break-all text-xs text-muted-foreground">Saved endpoint: {server.url}</p> : null}
    <div><label className="mb-2 block text-sm font-medium" htmlFor={`${id}-url`}>{server ? 'Replace endpoint URL' : 'Endpoint URL'}</label><input autoComplete="off" className={fieldClass} disabled={mutation.isPending} id={`${id}-url`} onChange={(event) => setUrl(event.target.value)} placeholder={server ? 'Leave empty to keep saved URL' : 'https://…'} value={url} /></div>
    <div><label className="mb-2 block text-sm font-medium" htmlFor={`${id}-headers`}>Replacement headers (JSON)</label><textarea autoComplete="off" className={`${fieldClass} min-h-24 font-mono text-xs`} disabled={mutation.isPending} id={`${id}-headers`} onChange={(event) => setHeaders(event.target.value)} placeholder={'{"Authorization": "${API_KEY}"}'} value={headers} /><p className="mt-1 text-xs text-muted-foreground">Leave empty to keep saved headers. Use {'{}'} to clear them. Secret placeholders are supported; existing literal values are never copied into this field.</p></div>
    {server?.headers ? <p className="break-words text-xs text-muted-foreground">Configured headers: {Object.keys(server.headers).join(', ')}</p> : null}
    <div><label className="mb-2 block text-sm font-medium" htmlFor={`${id}-timeout`}>Timeout (seconds)</label><input className={fieldClass} disabled={mutation.isPending} id={`${id}-timeout`} onChange={(event) => setTimeout(event.target.value)} placeholder="Application default" step="any" type="number" value={timeout} /></div>
    <label className="flex items-center gap-2 text-sm"><input className="size-4 accent-primary" checked={enabled} disabled={mutation.isPending} onChange={(event) => setEnabled(event.target.checked)} type="checkbox" />Enabled</label>
    <label className="flex items-center gap-2 text-sm"><input className="size-4 accent-primary" checked={cacheTools} disabled={mutation.isPending} onChange={(event) => setCacheTools(event.target.checked)} type="checkbox" />Cache tool list</label>
    {inputError ? <p className="text-sm text-destructive" role="alert">{inputError}</p> : null}
    <ErrorNotice error={mutation.error} fallback="MCP configuration could not be saved." />
    <div className="flex justify-end gap-2"><Button disabled={mutation.isPending} onClick={onCancel} type="button" variant="outline">Cancel</Button><Button disabled={mutation.isPending} type="submit">{mutation.isPending ? 'Saving…' : 'Save server'}</Button></div>
  </form>
}

export function McpPanel() {
  const queryClient = useQueryClient()
  const query = useQuery(mcpQueryOptions())
  const [editor, setEditor] = useState<McpServer | 'new' | null>(null)
  const [editorSaving, setEditorSaving] = useState(false)
  const [removal, setRemoval] = useState<McpServer | null>(null)
  const [notice, setNotice] = useState('')
  const refresh = async () => {
    setNotice('Saved. Restart InvestOrch to apply MCP changes. Current connections have not been reloaded.')
    await queryClient.invalidateQueries({ queryKey: managementKeys.mcp() })
  }
  const toggle = useMutation({ mutationFn: (server: McpServer) => saveMcp(server.name, { enabled: !server.enabled }, true), onSuccess: refresh })
  const remove = useMutation({ mutationFn: (name: string) => removeMcp(name), onSuccess: async () => { setRemoval(null); await refresh() } })
  return <section className="space-y-4 rounded-xl border border-border bg-card p-5">
    <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold">MCP servers</h2><p className="mt-1 text-xs text-muted-foreground">Changes take effect after restart. Connections are not tested here.</p></div><Button disabled={!query.data} onClick={() => setEditor('new')} size="sm" type="button" variant="outline"><Plus aria-hidden="true" />Add server</Button></div>
    <ErrorNotice error={query.error ?? toggle.error} fallback="MCP servers could not be loaded." />
    {query.isPending ? <p className="text-sm text-muted-foreground" role="status">Loading integrations…</p> : null}
    {query.isError ? <Button onClick={() => void query.refetch()} type="button" variant="outline">Retry</Button> : null}
    {query.data?.servers.length === 0 ? <p className="rounded-lg border border-dashed border-border px-4 py-6 text-sm text-muted-foreground">No MCP servers configured. Add a server when you need an external integration.</p> : null}
    {query.data?.servers.map((server) => <div className="rounded-lg border border-border p-4" key={server.name}>
      <div className="flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><h3 className="break-all text-sm font-semibold">{server.name}</h3><p className="mt-1 text-xs text-muted-foreground">{server.transport} · {server.enabled ? 'Enabled' : 'Disabled'} · Connection {server.runtime_status}</p></div><div className="flex gap-1"><Button aria-label={`Edit ${server.name}`} onClick={() => setEditor(server)} size="icon-sm" type="button" variant="ghost"><Pencil aria-hidden="true" /></Button><Button aria-label={`Remove ${server.name}`} onClick={() => { remove.reset(); setRemoval(server) }} size="icon-sm" type="button" variant="ghost"><Trash2 aria-hidden="true" /></Button></div></div>
      <p className="mt-3 break-all text-xs text-muted-foreground">{server.url}</p>
      <label className="mt-3 flex items-center gap-2 text-sm"><input className="size-4 accent-primary" checked={server.enabled} disabled={toggle.isPending} onChange={() => toggle.mutate(server)} type="checkbox" />Enable server</label>
    </div>)}
    {notice ? <p className="text-sm" role="status">{notice}</p> : null}
    <Dialog open={editor !== null} onOpenChange={(open) => { if (!open && !editorSaving) setEditor(null) }}><DialogContent className="max-h-[90dvh] max-w-[calc(100vw-2rem)] overflow-y-auto sm:max-w-xl" onEscapeKeyDown={(event) => { if (editorSaving) event.preventDefault() }} onPointerDownOutside={(event) => { if (editorSaving) event.preventDefault() }} showCloseButton={!editorSaving}><DialogTitle>{editor === 'new' ? 'Add MCP server' : 'Edit MCP server'}</DialogTitle><DialogDescription>Use an endpoint you trust. Existing credentials stay unchanged unless you replace them.</DialogDescription>{editor !== null ? <McpForm key={typeof editor === 'string' ? editor : editor.name} onCancel={() => setEditor(null)} onSaved={() => { setEditor(null); void refresh() }} onSavingChange={setEditorSaving} server={editor === 'new' ? null : editor} transports={query.data?.supported_transports ?? []} /> : null}</DialogContent></Dialog>
    <Dialog open={removal !== null} onOpenChange={(open) => { if (!open && !remove.isPending) setRemoval(null) }}><DialogContent className="max-w-[calc(100vw-2rem)] sm:max-w-sm"><DialogTitle>Remove MCP server?</DialogTitle><DialogDescription>Remove {removal?.name} from the saved configuration. You will need to add its URL and credentials again to restore it.</DialogDescription><ErrorNotice error={remove.error} /><div className="flex justify-end gap-2"><Button disabled={remove.isPending} onClick={() => setRemoval(null)} type="button" variant="outline">Cancel</Button><Button disabled={remove.isPending} onClick={() => { if (removal) remove.mutate(removal.name) }} type="button" variant="destructive">{remove.isPending ? 'Removing…' : 'Remove'}</Button></div></DialogContent></Dialog>
  </section>
}
