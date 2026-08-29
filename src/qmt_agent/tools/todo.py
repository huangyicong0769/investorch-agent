from agents import RunContextWrapper
from agents.decorators import tool

from qmt_agent.context import AgentContext, TodoItem


@tool
async def write_todos(context: RunContextWrapper[AgentContext], todos: list[TodoItem]) -> str:
    """
    Create or update the complete todo list for the current task.

    Always provide the full current todo list.

    Args:
        context (RunContextWrapper[AgentContext]): The current run context.
        todos (list[TodoItem]): The complete list of todos to set.

    Returns:
        str: A formatted string representation of the current todo list.
    """

    in_progress = sum(
        todo["status"] == "in_progress"
        for todo in todos
    )

    if in_progress > 1:
        raise ValueError(
            "Only one todo may be in progress at a time."
        )

    updated_todos = [
        dict(todo)
        for todo in todos
    ]
    context.context.turn.todos = updated_todos

    if context.context.todo_update_handler is not None:
        await context.context.todo_update_handler([
            dict(todo)
            for todo in updated_todos
        ])

    symbols = {
        "pending": "[ ]",
        "in_progress": "[>]",
        "completed": "[x]",
        "failed": "[!]",
    }

    return "\n".join(
        f"{symbols[todo['status']]} {todo['content']}"
        for todo in updated_todos
    )
