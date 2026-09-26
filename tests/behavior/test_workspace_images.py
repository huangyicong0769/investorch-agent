from __future__ import annotations

import base64
from pathlib import Path

import pytest
from agents import Agent, RunConfig, Runner, ToolOutputImage, ToolOutputText
from agents.testing import ModelStep, ScriptedModel, assistant_message, function_call

from investorch.tools.base import explore
from tests.behavior.test_workspace_capabilities import invoke, make_tool_context


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload,mime",
    [
        (b"\x89PNG\r\n\x1a\n" + b"x" * 20, "image/png"),
        (b"\xff\xd8\xff" + b"x" * 20, "image/jpeg"),
        (b"GIF89a" + b"x" * 20, "image/gif"),
        (b"RIFF\x10\x00\x00\x00WEBP" + b"x" * 20, "image/webp"),
    ],
)
async def test_explore_reads_raster_by_content_with_separate_image_limit(tmp_path: Path, payload: bytes, mime: str):
    context = make_tool_context(tmp_path, {"explore": {"max_full_read_bytes": 10}})
    (context.context.config.workspace_dir / "picture.txt").write_bytes(payload)
    result = await invoke(explore, context, operation="read", path="picture.txt")
    assert isinstance(result, list)
    assert isinstance(result[0], ToolOutputText)
    assert isinstance(result[1], ToolOutputImage)
    assert result[1].image_url == f"data:{mime};base64," + base64.b64encode(payload).decode()


@pytest.mark.asyncio
async def test_explore_keeps_svg_and_misnamed_text_as_text_and_rejects_large_or_escaped_images(tmp_path: Path):
    context = make_tool_context(tmp_path, {"images": {"max_image_bytes": 16}})
    workspace = context.context.config.workspace_dir
    for name in ("drawing.svg", "text.png"):
        (workspace / name).write_text("<svg/>")
        result = await invoke(explore, context, operation="read", path=name)
        assert result["content"] == "<svg/>"
    payload = b"\x89PNG\r\n\x1a\n" + b"x" * 9
    (workspace / "large.png").write_bytes(payload)
    result = await invoke(explore, context, operation="read", path="large.png")
    assert isinstance(result, str) and "image" in result.lower() and "16" in result
    outside = tmp_path / "outside.png"
    outside.write_bytes(payload)
    (workspace / "escaped.png").symlink_to(outside)
    result = await invoke(explore, context, operation="read", path="escaped.png")
    assert isinstance(result, str) and "escapes workspace" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("detail", ["auto", "original"])
async def test_explore_image_reaches_the_next_sdk_model_turn(tmp_path: Path, detail: str):
    context = make_tool_context(tmp_path, {"images": {"default_detail": detail}})
    payload = b"GIF89a" + b"x" * 20
    (context.context.config.workspace_dir / "picture.gif").write_bytes(payload)

    def inspect_observation(call):
        observation = next(item for item in call.input if item.get("type") == "function_call_output")
        assert isinstance(observation["output"], list)
        image = next(part for part in observation["output"] if part["type"] == "input_image")
        assert image["image_url"] == "data:image/gif;base64," + base64.b64encode(payload).decode()
        assert image["detail"] == detail
        return (assistant_message("Image received"),)

    model = ScriptedModel(
        (
            (function_call("explore", {"operation": "read", "path": "picture.gif"}, call_id="read-image"),),
            ModelStep.respond(inspect_observation),
        )
    )
    result = await Runner.run(
        Agent(name="Workspace reader", model=model, tools=[explore]),
        "Read picture.gif",
        context=context.context,
        run_config=RunConfig(tracing_disabled=True),
    )
    assert result.final_output == "Image received"
