import json

from agents import Agent, Runner
from openai.types.responses import ResponseReasoningTextDeltaEvent


async def _summarize_trace(summary_agent: Agent, kind: str, text: str,) -> str:
    result = await Runner.run(
        summary_agent,
        f"""Content type: {kind}.\nSummarize the following execution content:\n{text}""",
    )

    summary = str(result.final_output).strip()

    if not summary:
        raise ValueError("Summary agent returned an empty summary.")

    return summary


async def _print_trace_content(
    summary_agent: Agent,
    kind: str,
    text: str,
    summary_enabled: bool,
    summary_threshold: int,
) -> None:
    if not summary_enabled or len(text) <= summary_threshold:
        print(f"\n[{kind}]")
        print(text)
        return

    try:
        summary = await _summarize_trace(
            summary_agent,
            kind,
            text,
        )
    except Exception as e:
        # Observability should never break the main agent run.
        print(f"\n[{kind}]")
        print(text)
        print(f"[summary failed: {e}]")
        return

    print(f"\n[{kind} summary]")
    print(summary)
    print(f"[original: {len(text)} chars]")


async def _flush_reasoning(
    summary_agent: Agent,
    reasoning_parts: list[str],
    summary_enabled: bool,
    summary_threshold: int,
) -> None:
    if not reasoning_parts:
        return

    reasoning = "".join(reasoning_parts)
    reasoning_parts.clear()

    await _print_trace_content(
        summary_agent,
        "reasoning",
        reasoning,
        summary_enabled,
        summary_threshold,
    )


async def print_run_events(
    result,
    summary_agent: Agent,
    summary_enabled: bool,
    summary_threshold: int,
) -> None:
    reasoning_parts: list[str] = []

    async for event in result.stream_events():

        if event.type == "agent_updated_stream_event":
            await _flush_reasoning(
                summary_agent,
                reasoning_parts,
                summary_enabled,
                summary_threshold,
            )

            print(f"\n[agent] {event.new_agent.name}")
            continue

        if event.type == "raw_response_event":
            if isinstance(
                event.data,
                ResponseReasoningTextDeltaEvent,
            ):
                reasoning_parts.append(event.data.delta)

            continue

        if event.type != "run_item_stream_event":
            continue

        if event.name == "reasoning_item_created":
            await _flush_reasoning(
                summary_agent,
                reasoning_parts,
                summary_enabled,
                summary_threshold,
            )
            continue

        # Be defensive in case a provider does not emit
        # reasoning_item_created exactly as expected.
        await _flush_reasoning(
            summary_agent,
            reasoning_parts,
            summary_enabled,
            summary_threshold,
        )

        item = event.item

        if event.name == "tool_called":
            print(f"\n[action] {item.tool_name}")

            raw_item = item.raw_item
            arguments = (
                raw_item.get("arguments")
                if isinstance(raw_item, dict)
                else getattr(raw_item, "arguments", None)
            )

            if arguments:
                try:
                    parsed = json.loads(arguments)
                    print(json.dumps(
                        parsed,
                        indent=2,
                        ensure_ascii=False,
                    ))
                except (json.JSONDecodeError, TypeError):
                    print(arguments)

        elif event.name == "tool_output":
            output = str(item.output)

            await _print_trace_content(
                summary_agent,
                "observation",
                output,
                summary_enabled,
                summary_threshold,
            )

        elif event.name == "message_output_created":
            pass

    await _flush_reasoning(
        summary_agent,
        reasoning_parts,
        summary_enabled,
        summary_threshold,
    )
