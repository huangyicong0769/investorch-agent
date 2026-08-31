import { useEffect, useState } from 'react'

import type { RuntimeSnapshot, SessionPresentationState, TodoStatus } from '../../api/types'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'

interface PlanCardProps {
  presentation: SessionPresentationState
  runtime: RuntimeSnapshot
}

const STATUS_ICON: Record<TodoStatus, string> = {
  pending: '○',
  in_progress: '●',
  completed: '✓',
  failed: '×',
}

function statusLabel(status: TodoStatus): string {
  return status === 'in_progress' ? 'in progress' : status
}

export function PlanCard({ presentation, runtime }: PlanCardProps) {
  const active = runtime.run_id !== null
  const todos = active ? runtime.todos : presentation.last_todos
  const completedCount = todos.filter((todo) => todo.status === 'completed').length
  const complete = completedCount === todos.length
  const [open, setOpen] = useState(() => !complete)
  useEffect(() => {
    setOpen(!complete)
  }, [complete])

  if (todos.length === 0) {
    return null
  }

  const title = complete
    ? `✓ Plan · ${completedCount}/${todos.length} completed`
    : `Plan · ${completedCount}/${todos.length}`

  return (
    <Collapsible
      className="rounded-xl border border-border bg-card/80 px-3 py-2 text-sm"
      onOpenChange={setOpen}
      open={open}
    >
      <CollapsibleTrigger asChild>
        <button className="flex w-full cursor-pointer items-center gap-2 text-left font-medium" type="button">
          <span aria-hidden="true" className="w-3 shrink-0 text-muted-foreground">
            {open ? '▾' : '▸'}
          </span>
          <span>{title}</span>
        </button>
      </CollapsibleTrigger>
      <CollapsibleContent>
        <ol className="mt-2 space-y-1.5 border-t border-border pt-2">
          {todos.map((todo, index) => (
            <li className="flex items-start gap-2 text-xs" key={`${index}:${todo.content}`}>
              <span
                aria-label={statusLabel(todo.status)}
                className="w-4 shrink-0 text-center text-muted-foreground"
                title={statusLabel(todo.status)}
              >
                {STATUS_ICON[todo.status]}
              </span>
              <span className="min-w-0 whitespace-pre-wrap break-words">{todo.content}</span>
            </li>
          ))}
        </ol>
      </CollapsibleContent>
    </Collapsible>
  )
}
