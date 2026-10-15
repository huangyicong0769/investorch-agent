import { useEffect, useState } from 'react'
import { ChevronRight, ListTodo } from 'lucide-react'

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

  return (
    <Collapsible
      className="border-t border-border/50 py-4 text-sm"
      onOpenChange={setOpen}
      open={open}
    >
      <CollapsibleTrigger className="group flex w-full items-center gap-2.5 rounded-md py-1.5 text-left hover:text-primary focus-visible:outline-ring">
        <ListTodo className="size-4 shrink-0" aria-hidden="true" />
        <span className="flex-1">Plan</span>
        <span className="text-xs tabular-nums text-muted-foreground">{completedCount}/{todos.length} completed</span>
        <ChevronRight className="size-3.5 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" aria-hidden="true" />
      </CollapsibleTrigger>
      <CollapsibleContent className="summary-section-content">
        <ol className="space-y-2.5 pt-3">
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
