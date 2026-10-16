"""Opt-in real-provider qualification using synthetic images and temporary state.

Run with `uv run --locked python scripts/qualify_multimodal.py`.
Uses configured owner credentials in memory; never exports image payloads or secrets.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
import tempfile
import zlib
from contextlib import AsyncExitStack

from agents import SQLiteSession, set_tracing_disabled

from investorch.agents import (
    AgentLoop,
    ApprovalOutcome,
    TokenUsage,
    create_agent,
    create_compaction_agent,
    create_title_agent,
)
from investorch.application import PortfolioOperations
from investorch.config import AppConfig, load_config
from investorch.context import ExecutionState
from investorch.images import ImageContent
from investorch.initializer import initialize
from investorch.inputs import UserInput
from investorch.llm.clients import ModelConnectionClients
from investorch.llm.oauth import ChatGPTAuth
from investorch.output import ToolOutput
from investorch.runtime.control import RunControl
from investorch.storage import create_session, get_session_title


def red_png() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack("!2I5B", 128, 128, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 128) * 128))
        + chunk(b"IEND", b"")
    )


async def qualify() -> None:
    set_tracing_disabled(True)
    source = load_config()
    with tempfile.TemporaryDirectory(prefix="investorch-image-qualification-") as directory:
        data = source.public()
        data["secrets"] = source.secrets
        data["paths"]["root"] = directory
        data["backtest"]["use_cnequity"] = False
        if any(connection["type"] != "openai_compatible" for connection in data["models"].values()):
            raise ValueError(
                "Sign in separately in an isolated Root for ChatGPT qualification; do not copy rotating tokens"
            )
        config = AppConfig(data)
        initialize(config)
        image_bytes = red_png()
        (config.workspace_dir / "sample.png").write_bytes(image_bytes)
        url = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
        async with AsyncExitStack() as stack:
            auth = ChatGPTAuth(config)
            stack.push_async_callback(auth.close)
            clients = ModelConnectionClients(config, auth)
            stack.push_async_callback(clients.close)
            main = create_agent(*clients.for_agent("main"), config)
            title = create_title_agent(*clients.for_agent("title"))
            compact = create_compaction_agent(*clients.for_agent("compact"))
            loop = AgentLoop(
                main, title, compact, config, PortfolioOperations(config=config), model_factory=clients.resolve
            )
            execution = ExecutionState(workspace_root=config.workspace_dir)
            report = {"model": config.agent_model("main").name, "checks": {}}

            async def reject_action(*_args) -> ApprovalOutcome:
                return ApprovalOutcome(False, TokenUsage())

            cases = [
                ("text_only", UserInput("Reply exactly: structured text works"), None),
                (
                    "image_only",
                    UserInput("", (ImageContent(url),)),
                    "Describe the dominant color of the supplied image. No tools.",
                ),
                (
                    "text_image",
                    UserInput("What is the dominant color? Reply with the color only. No tools.", (ImageContent(url),)),
                    None,
                ),
                (
                    "original_detail",
                    UserInput(
                        "What is the dominant color? Reply with the color only. No tools.",
                        (ImageContent(url, "original"),),
                    ),
                    None,
                ),
                (
                    "explore",
                    UserInput(
                        "Read sample.png using explore and report its dominant color. Do not use any other tools."
                    ),
                    None,
                ),
            ]
            for name, user_input, instruction in cases:
                create_session(config.sessions_db, name)
                session = SQLiteSession(name, config.sessions_db)
                events = []

                async def output(event, collected=events):
                    collected.append(event)

                try:
                    async with asyncio.timeout(180):
                        result = await loop.run(
                            user_input,
                            session,
                            execution,
                            run_id=name,
                            session_id=name,
                            model_config=config.agent_model("main"),
                            approval_handler=reject_action,
                            output_handler=output,
                            run_control=RunControl(name, name, lambda: None),
                            application_instruction=instruction,
                        )
                    color_correct = "red" in result.output.lower() or "红" in result.output
                    passed = "structured text works" in result.output.lower() if name == "text_only" else color_correct
                    if name == "explore":
                        passed = passed and any(isinstance(event, ToolOutput) and event.images for event in events)
                    report["checks"][name] = {
                        "passed": bool(passed),
                        "title_generated": bool(get_session_title(config.sessions_db, name)),
                        "main_tokens": result.main_usage.total_tokens,
                    }
                    if name == "text_image":
                        async with asyncio.timeout(180):
                            compaction = await loop.compact(session)
                            continued = await loop.run(
                                UserInput("What color was the image? Reply with the color only. No tools."),
                                session,
                                execution,
                                run_id="continued",
                                session_id=name,
                                model_config=config.agent_model("main"),
                                approval_handler=reject_action,
                                output_handler=output,
                                run_control=RunControl(name, "continued", lambda: None),
                            )
                        report["checks"]["compact_continuation"] = {
                            "passed": compaction.changed
                            and ("red" in continued.output.lower() or "红" in continued.output)
                        }
                except Exception as exc:
                    # Provider exception messages can contain request payloads.
                    report["checks"][name] = {"passed": False, "error_type": type(exc).__name__}
                finally:
                    session.close()
                print(json.dumps({"check": name, "result": report["checks"][name]}), flush=True)
            print(json.dumps(report, indent=2), flush=True)
            if not all(result["passed"] for result in report["checks"].values()):
                raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(qualify())
