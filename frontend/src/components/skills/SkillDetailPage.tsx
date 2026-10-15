import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { managementKeys, removeSkill, setSkillEnabled, skillQueryOptions } from '../../api/management'
import { ErrorNotice } from '../management/Feedback'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { fieldLabel } from '../management/schema'
import { MarkdownMessage } from '../timeline/MarkdownMessage'
import { RemoveSkillDialog, SkillActions } from './SkillActions'
import { useSkillWorkflow } from './useSkillWorkflow'
import { Button } from '@/components/ui/button'

function SkillPreview({ text }: { text: string }) {
  const frontmatter = /^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/.exec(text)?.[0]
  return <>
    {frontmatter ? <details className="mb-4 overflow-hidden rounded-lg border border-border bg-muted/30">
      <summary className="cursor-pointer px-3 py-2 text-xs font-medium">Skill metadata</summary>
      <pre className="max-h-64 max-w-full overflow-auto whitespace-pre-wrap break-words px-3 pb-3 text-xs leading-5 text-muted-foreground">{frontmatter}</pre>
    </details> : null}
    <MarkdownMessage text={frontmatter ? text.slice(frontmatter.length) : text} />
  </>
}

function SkillDetail({ name }: { name: string }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [resource, setResource] = useState('SKILL.md')
  const [confirmRemoval, setConfirmRemoval] = useState(false)
  const [notice, setNotice] = useState('')
  const query = useQuery(skillQueryOptions(name))
  const preview = useQuery({ ...skillQueryOptions(name, resource), enabled: resource !== 'SKILL.md' })
  const content = resource === 'SKILL.md' ? query : preview
  const workflow = useSkillWorkflow()
  const toggle = useMutation({
    mutationFn: (enabled: boolean) => setSkillEnabled(name, enabled),
    onSuccess: async () => { setNotice('Saved. Restart InvestOrch to use the updated Skill catalog.'); await queryClient.invalidateQueries({ queryKey: managementKeys.skills() }) },
  })
  const remove = useMutation({
    mutationFn: () => removeSkill(name),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: managementKeys.skills() }); navigate('/skills') },
  })
  const skill = query.data
  return <div className="mx-auto w-full max-w-5xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
    <Link className="text-sm text-muted-foreground underline underline-offset-4" to="/skills">All Skills</Link>
    <div className="mt-5"><ErrorNotice error={query.error ?? workflow.error ?? toggle.error} fallback="The Skill could not be loaded or updated." /></div>
    {query.isPending ? <p className="mt-6 text-sm text-muted-foreground" role="status">Loading Skill…</p> : null}
    {query.isError ? <Button className="mt-4" onClick={() => void query.refetch()} type="button" variant="outline">Retry</Button> : null}
    {skill ? <>
      <header className="mt-5 rounded-xl border border-border bg-card p-5 sm:p-6">
        <div className="flex items-start justify-between gap-3"><h1 className="min-w-0 break-words text-2xl font-semibold tracking-tight">{skill.name}</h1><SkillActions disabled={workflow.isPending} onRemove={() => { remove.reset(); setConfirmRemoval(true) }} onWorkflow={(action) => workflow.mutate({ action, name })} skill={skill} /></div>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">{skill.description ?? 'Skill content is unavailable.'}</p>
        <dl className="mt-5 grid gap-3 text-sm sm:grid-cols-3"><div><dt className="text-xs text-muted-foreground">Version</dt><dd className="mt-1">{skill.version ?? 'Unavailable'}</dd></div><div><dt className="text-xs text-muted-foreground">Source</dt><dd className="mt-1 break-all">{skill.source.type === 'builtin' ? 'Built-in' : fieldLabel(skill.source.type)}{skill.source.origin ? ` · ${skill.source.origin}` : ''}</dd></div><div><dt className="text-xs text-muted-foreground">Status</dt><dd className="mt-1">{fieldLabel(skill.status)}</dd></div></dl>
        <label className="mt-5 flex items-center gap-2 text-sm"><input className="size-4 accent-primary" checked={skill.enabled} disabled={toggle.isPending} onChange={(event) => toggle.mutate(event.target.checked)} type="checkbox" />Enabled</label>
        {skill.source.type === 'builtin' ? <p className="mt-3 text-xs text-muted-foreground">Built-in Skills can be forked into a custom Skill. Update managed built-ins explicitly with <code>investorch --update</code>.</p> : null}
        {notice ? <p className="mt-4 text-sm" role="status">{notice}</p> : null}
        {workflow.isPending ? <p className="mt-4 text-sm" role="status">Starting a new Agent Session…</p> : null}
      </header>
      {skill.status === 'invalid' || skill.status === 'missing' ? <div className="mt-5 rounded-xl border border-destructive/25 bg-destructive/5 p-5 text-sm" role="alert"><h2 className="font-semibold">{fieldLabel(skill.status)} Skill</h2><p className="mt-2">{skill.validation_error ?? 'The registered Skill directory is missing. Restore its files or remove the custom registration.'}</p>{skill.source.type === 'builtin' ? <p className="mt-2">Run <code>investorch --update</code> to restore the packaged version.</p> : null}</div> : <section className="mt-5 rounded-xl border border-border bg-card p-5 sm:p-6">
        <div className="mb-5 flex flex-wrap items-end justify-between gap-3"><div><h2 className="font-semibold">Read-only preview</h2><p className="mt-1 text-xs text-muted-foreground">Viewing this content does not load it into an Agent Session.</p></div><div className="min-w-0 max-w-full"><label className="mb-1 block text-xs font-medium" htmlFor="skill-resource">File</label><Select value={resource} onValueChange={setResource}><SelectTrigger className="max-w-full bg-background" id="skill-resource"><SelectValue /></SelectTrigger><SelectContent align="end" position="popper" className="max-w-[calc(100vw-2rem)]">{['SKILL.md', ...skill.resources].map((file) => <SelectItem key={file} value={file}>{file}</SelectItem>)}</SelectContent></Select></div></div>
        <ErrorNotice error={content.error} fallback="This resource cannot be previewed as bounded UTF-8 text." />
        {content.isPending ? <p className="text-sm text-muted-foreground" role="status">Loading resource…</p> : null}
        {content.data?.truncated ? <p className="mb-3 text-xs text-muted-foreground">Preview truncated to the configured text limit.</p> : null}
        {content.data?.content != null ? resource === 'SKILL.md' ? <SkillPreview text={content.data.content} /> : <pre className="max-w-full overflow-x-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-4 text-xs leading-6">{content.data.content}</pre> : null}
      </section>}
      <RemoveSkillDialog error={remove.error} onClose={() => setConfirmRemoval(false)} onConfirm={() => remove.mutate()} pending={remove.isPending} skill={confirmRemoval ? skill : null} />
    </> : null}
  </div>
}

export function SkillDetailPage() {
  const { name = '' } = useParams()
  return <SkillDetail key={name} name={name} />
}
