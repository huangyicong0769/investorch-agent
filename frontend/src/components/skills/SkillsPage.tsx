import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, Plus, Puzzle } from 'lucide-react'
import { Link } from 'react-router-dom'

import { managementKeys, removeSkill, setSkillEnabled, skillsQueryOptions, type SkillRecord } from '../../api/management'
import { ErrorNotice } from '../management/Feedback'
import { fieldLabel } from '../management/schema'
import { SkillActions, RemoveSkillDialog } from './SkillActions'
import { useSkillWorkflow } from './useSkillWorkflow'
import { Card, CardHeader, CardContent, CardFooter } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { NativeSelect } from '@/components/ui/native-select'
import { Button } from '@/components/ui/button'

export function SkillsPage() {
  const queryClient = useQueryClient()
  const query = useQuery(skillsQueryOptions())
  const workflow = useSkillWorkflow()
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState('all')
  const [source, setSource] = useState('all')
  const [removal, setRemoval] = useState<SkillRecord | null>(null)
  const [notice, setNotice] = useState('')
  const refresh = async () => {
    setNotice('Saved. Restart InvestOrch to use the updated Skill catalog.')
    await queryClient.invalidateQueries({ queryKey: managementKeys.skills() })
  }
  const toggle = useMutation({ mutationFn: (skill: SkillRecord) => setSkillEnabled(skill.name, !skill.enabled), onSuccess: refresh })
  const remove = useMutation({ mutationFn: (name: string) => removeSkill(name), onSuccess: async () => { setRemoval(null); await refresh() } })
  const skills = query.data?.skills.filter((skill) => {
    const matchesStatus = status === 'all' || (status === 'enabled' ? skill.enabled : status === 'disabled' ? !skill.enabled : skill.status === status)
    return matchesStatus && (source === 'all' || source === skill.source.type) && `${skill.name} ${skill.description ?? ''}`.toLowerCase().includes(search.toLowerCase())
  }) ?? []
  return <div className="mx-auto w-full max-w-6xl px-4 py-16 sm:px-6 md:py-12 lg:px-8">
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div><h1 className="text-3xl font-semibold tracking-tight">Skills</h1><p className="mt-2 text-sm text-muted-foreground">Manage reusable workflows and create new ones with your Agent.</p></div>
      <div className="flex flex-wrap gap-2"><Button disabled={workflow.isPending} onClick={() => workflow.mutate({ action: 'install' })} type="button" variant="outline"><Download aria-hidden="true" />Install Skill</Button><Button disabled={workflow.isPending} onClick={() => workflow.mutate({ action: 'create' })} type="button"><Plus aria-hidden="true" />Create Skill</Button></div>
    </header>
    <div className="mt-8 grid grid-cols-2 gap-3 rounded-xl border border-border/70 bg-card/60 p-4 sm:grid-cols-[minmax(0,1fr)_10rem_10rem] [&_[data-slot=native-select-wrapper]]:w-full">
      <div className="col-span-2 sm:col-span-1"><label className="mb-2 block text-xs font-medium" htmlFor="skill-search">Search Skills</label><Input className="h-10 bg-card" id="skill-search" onChange={(event) => setSearch(event.target.value)} placeholder="Name or description" type="search" value={search} /></div>
      <div><label className="mb-2 block text-xs font-medium" htmlFor="skill-status">Status</label><NativeSelect className="h-10 bg-card" id="skill-status" onChange={(event) => setStatus(event.target.value)} value={status}><option value="all">All installed</option><option value="enabled">Enabled</option><option value="disabled">Disabled</option><option value="missing">Missing</option><option value="invalid">Invalid</option></NativeSelect></div>
      <div><label className="mb-2 block text-xs font-medium" htmlFor="skill-source">Source</label><NativeSelect className="h-10 bg-card" id="skill-source" onChange={(event) => setSource(event.target.value)} value={source}><option value="all">All sources</option><option value="builtin">Built-in</option><option value="created">Created</option><option value="external">External</option></NativeSelect></div>
    </div>
    <div className="mt-5 space-y-3"><ErrorNotice error={query.error ?? workflow.error ?? toggle.error} fallback="Skills could not be loaded or updated." />{notice ? <p className="rounded-lg border border-border bg-card p-3 text-sm" role="status">{notice}</p> : null}{workflow.isPending ? <p className="text-sm text-muted-foreground" role="status">Starting a new Agent Session…</p> : null}</div>
    {query.isPending ? <p className="mt-8 text-sm text-muted-foreground" role="status">Loading Skills…</p> : null}
    {query.isError ? <Button className="mt-4" onClick={() => void query.refetch()} type="button" variant="outline">Retry</Button> : null}
    {query.isSuccess && !skills.length ? <div className="mt-8 rounded-xl border border-dashed border-border p-10 text-center"><Puzzle aria-hidden="true" className="mx-auto mb-3 size-6 text-muted-foreground" /><h2 className="font-medium">No Skills found</h2><p className="mt-2 text-sm text-muted-foreground">Adjust the filters, or start a Create or Install conversation above.</p></div> : null}
    <div className="mt-5 grid gap-3 xl:grid-cols-2">{skills.map((skill) => <article className="min-w-0" key={skill.name}>
      <Card className="h-full gap-3 rounded-xl border-border py-4 shadow-none">
        <CardHeader className="flex flex-row items-start justify-between gap-2 px-4">
          <Link className="min-w-0 break-words text-base font-semibold text-foreground underline-offset-4 hover:text-primary hover:underline focus-visible:outline-ring" to={`/skills/${encodeURIComponent(skill.name)}`}>{skill.name}</Link>
          <SkillActions disabled={workflow.isPending} onRemove={() => { remove.reset(); setRemoval(skill) }} onWorkflow={(action) => workflow.mutate({ action, name: skill.name })} skill={skill} />
        </CardHeader>
        <CardContent className="flex-1 px-4"><p className="text-sm leading-6 text-muted-foreground">{skill.description ?? 'Installed files are unavailable. Open the Skill for repair details.'}</p></CardContent>
        <CardFooter className="flex-wrap justify-between gap-3 px-4">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"><Badge className="font-normal" variant="secondary">{skill.source.type === 'builtin' ? 'Built-in' : fieldLabel(skill.source.type)}</Badge><span>{skill.version ? `v${skill.version}` : 'Version unavailable'}</span>{skill.status === 'invalid' || skill.status === 'missing' ? <Badge variant="destructive">{fieldLabel(skill.status)}</Badge> : null}</div>
          <label className="flex min-h-8 items-center gap-2 text-xs font-medium"><input className="size-4 accent-primary" checked={skill.enabled} disabled={toggle.isPending} onChange={() => toggle.mutate(skill)} type="checkbox" />Enabled</label>
        </CardFooter>
      </Card>
    </article>)}</div>
    <RemoveSkillDialog error={remove.error} onClose={() => setRemoval(null)} onConfirm={() => { if (removal) remove.mutate(removal.name) }} pending={remove.isPending} skill={removal} />
  </div>
}
