import json

from openai.types.responses import ResponseReasoningTextDeltaEvent


async def print_run_events(result) -> None:
    reasoning_started = False

    async for event in result.stream_events():

        # Agent changed
        if event.type == "agent_updated_stream_event":
            print(f"\n[agent] {event.new_agent.name}")
            continue

        # Low-level model stream:
        # only keep reasoning text, ignore final-answer token deltas.
        if event.type == "raw_response_event":
            if isinstance(event.data, ResponseReasoningTextDeltaEvent):
                if not reasoning_started:
                    print("\n[reasoning]")
                    reasoning_started = True

                print(
                    event.data.delta,
                    end="",
                    flush=True,
                )

            continue

        # From here on only semantic run items.
        if event.type != "run_item_stream_event":
            continue

        item = event.item

        # Close the reasoning line before displaying an action.
        if reasoning_started:
            print()
            reasoning_started = False

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
            print(f"\n[observation]")
            print(item.output)

        elif event.name == "reasoning_item_created":
            # Already streamed through ResponseReasoningTextDeltaEvent.
            pass

        elif event.name == "message_output_created":
            # Final answer will be printed from result.final_output.
            pass