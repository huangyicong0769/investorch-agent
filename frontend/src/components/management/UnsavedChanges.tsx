import { useEffect } from 'react'
import { useBlocker } from 'react-router-dom'

import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'

export function UnsavedChanges({ dirty }: { dirty: boolean }) {
  const blocker = useBlocker(dirty)
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  return <Dialog open={blocker.state === 'blocked'} onOpenChange={(open) => { if (!open && blocker.state === 'blocked') blocker.reset() }}>
    <DialogContent className="max-w-[calc(100vw-2rem)] sm:max-w-sm">
      <DialogTitle>Discard unsaved changes?</DialogTitle>
      <DialogDescription>Your edits have not been saved. Stay here to continue editing or discard them to leave.</DialogDescription>
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={() => { if (blocker.state === 'blocked') blocker.reset() }}>Keep editing</Button>
        <Button type="button" variant="destructive" onClick={() => { if (blocker.state === 'blocked') blocker.proceed() }}>Discard and leave</Button>
      </div>
    </DialogContent>
  </Dialog>
}
