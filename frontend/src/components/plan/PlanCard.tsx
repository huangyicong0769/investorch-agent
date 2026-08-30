import type { RuntimeSnapshot, SessionPresentationState, TodoStatus } from '../../api/types'

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
  if (todos.length === 0) {
    return null
  }

  const completedCount = todos.filter((todo) => todo.status === 'completed').length
  const complete = completedCount === todos.length
  const title = complete
    ? `✓ Plan · ${completedCount}/${todos.length} completed`
    : `Plan · ${completedCount}/${todos.length}`

  return (
    <details className="rounded-xl border border-border bg-card/80 px-3 py-2 text-sm" open={!complete}>
      <summary className="cursor-pointer font-medium">{title}</summary>
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
    </details>
  )
}
