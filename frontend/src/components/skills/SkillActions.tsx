import { MoreHorizontal } from 'lucide-react'

import type { SkillAction, SkillRecord } from '../../api/management'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { ErrorNotice } from '../management/Feedback'

export function SkillActions({ skill, disabled, onWorkflow, onRemove }: { skill: SkillRecord; disabled: boolean; onWorkflow: (action: SkillAction) => void; onRemove: () => void }) {
  const unavailable = skill.status === 'invalid' || skill.status === 'missing'
  return <DropdownMenu>
    <DropdownMenuTrigger asChild><Button aria-label={`Actions for ${skill.name}`} disabled={disabled} size="icon-sm" type="button" variant="ghost"><MoreHorizontal aria-hidden="true" /></Button></DropdownMenuTrigger>
    <DropdownMenuContent align="end">
      {skill.source.type !== 'builtin' ? <DropdownMenuItem disabled={unavailable} onSelect={() => onWorkflow('edit')}>Edit with Agent</DropdownMenuItem> : null}
      <DropdownMenuItem disabled={unavailable} onSelect={() => onWorkflow('fork')}>Fork with Agent</DropdownMenuItem>
      {skill.source.type !== 'builtin' ? <><DropdownMenuSeparator /><DropdownMenuItem onSelect={onRemove} variant="destructive">Remove Skill</DropdownMenuItem></> : null}
    </DropdownMenuContent>
  </DropdownMenu>
}

export function RemoveSkillDialog({ skill, pending, error, onClose, onConfirm }: { skill: SkillRecord | null; pending: boolean; error: unknown; onClose: () => void; onConfirm: () => void }) {
  return <Dialog open={skill !== null} onOpenChange={(open) => { if (!open && !pending) onClose() }}>
    <DialogContent className="max-w-[calc(100vw-2rem)] sm:max-w-sm" onEscapeKeyDown={(event) => { if (pending) event.preventDefault() }} onPointerDownOutside={(event) => { if (pending) event.preventDefault() }} showCloseButton={!pending}>
      <DialogTitle>Remove this Skill?</DialogTitle>
      <DialogDescription><span className="break-all">{skill?.name}</span> and its installed files will be permanently deleted. This cannot be undone.</DialogDescription>
      <ErrorNotice error={error} fallback="The Skill could not be removed." />
      <div className="flex justify-end gap-2"><Button disabled={pending} onClick={onClose} type="button" variant="outline">Cancel</Button><Button disabled={pending} onClick={onConfirm} type="button" variant="destructive">{pending ? 'Removing…' : 'Remove Skill'}</Button></div>
    </DialogContent>
  </Dialog>
}
