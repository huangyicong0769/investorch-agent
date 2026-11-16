"""Run the DeepSeek integration checks with DEEPSEEK_API_KEY and synthetic inputs."""

import asyncio
import os
import tempfile
import wave
from contextlib import aclosing
from dataclasses import replace
from io import BytesIO
from pathlib import Path

from agents import Agent, ModelSettings, Runner, WebSearchTool, set_tracing_disabled
from openai import AsyncOpenAI

from investorch.application import open_application_host
from investorch.config import REQUIRED_AGENT_ROLES, AgentModelConfig, AppConfig
from investorch.config_schema import DEEPSEEK_BASE_URL, DEEPSEEK_DEFAULT_MODEL
from investorch.initializer import initialize
from investorch.journal import read_session_journal
from investorch.llm.deepseek import DeepSeekResponseError, DeepSeekResponsesModel
from investorch.llm.deepseek_services import DeepSeekServiceClient, DeepSeekServiceError, input_file
from investorch.output import OutputEvent, consume_run_events, serialize_output_event
from investorch.runtime import RunDefaults
from investorch.storage import get_session_title


def make_pdf(path: Path) -> None:
    text = (
        b"BT /F1 24 Tf 48 320 Td 36 TL (InvestOrch document check) Tj "
        b"T* (The fixture answer is forty two.) Tj T* (This page has three lines.) Tj ET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 480 380] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(text)} >>\nstream\n".encode() + text + b"\nendstream",
    ]
    document = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, entry in enumerate(objects, 1):
        offsets.append(len(document))
        document.extend(f"{index} 0 obj\n".encode() + entry + b"\nendobj\n")
    xref = len(document)
    document.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        document.extend(f"{offset:010d} 00000 n \n".encode())
    document.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(document)


def has_completed_web_call(records: list[dict]) -> bool:
    stages: dict[str, list[str]] = {}
    for record in records:
        if record.get("hosted_call_id"):
            stages.setdefault(record["hosted_call_id"], []).append(record["hosted_status"])
    return any(
        {"in_progress", "searching"} <= set(statuses) and statuses[-1] == "completed" for statuses in stages.values()
    )


async def collect_web_events(agent: Agent, prompt: str) -> list[dict[str, object]]:
    records = []

    async def receive(event: OutputEvent) -> None:
        records.append(serialize_output_event(event))

    run = Runner.run_streamed(agent, prompt, max_turns=2)
    await consume_run_events(run, receive, agent.name, deepseek=True)
    return records


async def check_pro_events(key: str) -> None:
    async with AsyncOpenAI(base_url=DEEPSEEK_BASE_URL, api_key=key, timeout=90, max_retries=0) as client:
        model = DeepSeekResponsesModel(AgentModelConfig("test", "deepseek-v4-pro", "low", 1048576), client)
        agent = Agent(
            name="Pro web search",
            model=model,
            tools=[WebSearchTool()],
            model_settings=ModelSettings(reasoning={"effort": "low"}),
        )
        records = await collect_web_events(
            agent,
            "Use the web search tool to check the latest news on python.org. Cite the most recent release in one line.",
        )
        assert has_completed_web_call(records), "missing standard web_search lifecycle"


async def check_flash_events(config: AppConfig, key: str) -> None:
    async with AsyncOpenAI(base_url=DEEPSEEK_BASE_URL, api_key=key, timeout=90, max_retries=0) as client:
        model = DeepSeekResponsesModel(replace(config.agent_model("main"), read_url=False), client)
        agent = Agent(name="Flash web search", model=model, model_settings=ModelSettings(reasoning={"effort": "none"}))
        records = await collect_web_events(
            agent,
            "Use native web search for the latest Python release on python.org. Give the version and cite the source.",
        )
        assert has_completed_web_call(records), "missing Flash web lifecycle"
        assert any(record.get("citations") for record in records), "missing source citation"


def smoke_config(root: Path, key: str) -> AppConfig:
    return AppConfig(
        {
            "paths": {"root": str(root)},
            "models": {"test": {"type": "deepseek"}},
            "agents": {role: {"model": "test", "reasoning_effort": "low"} for role in REQUIRED_AGENT_ROLES},
            "secrets": {"DEEPSEEK_API_KEY": key},
            "runtime": {"max_turns": 8},
            "permission": {"mode": "manual"},
        }
    )


async def check_responses(config: AppConfig) -> None:
    initialize(config)

    async def approve(request, _reason):
        return request.tool_name == "calculate"

    settings = RunDefaults(permission_mode="manual", follow_up_behavior="steer")
    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        session_id = await host.sessions.create()
        named = await host.runtime.start_run(session_id, "Reply with just the word Ready.", settings).task
        assert get_session_title(config.sessions_db, session_id), "missing native title on a plain answer"
        assert named.auxiliary_usage.requests == 0, "native title should skip the auxiliary request"
        await host.sessions.set_title(session_id, "Manual smoke title")

        host.session_models.update(session_id, web_search=False, read_url=True)
        read = await host.runtime.start_run(
            session_id,
            "Read https://www.iana.org/help/example-domains with native URL reading. Use the retrieved page to give "
            "its heading and quote the sentence describing the web service for those domains.",
            settings,
        ).task
        assert read.main_usage.tool_fetch > 0, "missing billed URL read"
        assert get_session_title(config.sessions_db, session_id) == "Manual smoke title"
        assert has_completed_web_call(read_session_journal(config.session_journal_dir, session_id)), (
            "missing URL reading lifecycle"
        )
        assert (await host.sessions.compact(session_id)).changed, "Compact did not change the history"
        fork = await host.sessions.fork(session_id)

    async with open_application_host(config, manual_approval_handler=approve, enable_activity=False) as host:
        host.session_models.update(fork, web_search=False, read_url=False)
        follow_up = await host.runtime.start_run(
            fork, "From the page we already read, give its title in one phrase.", settings
        ).task
        assert "example domains" in follow_up.output.casefold(), "missing replayed page context"
        assert follow_up.main_usage.tool_search == follow_up.main_usage.tool_fetch == 0, "web tools were disabled"
        await host.runtime.start_run(
            fork, "Use the local calculate function to add 23 + 19. Reply with the result.", settings
        ).task
        records = read_session_journal(config.session_journal_dir, fork)
        assert any(record["type"] == "tool_called" and record.get("name") == "calculate" for record in records), (
            "local calculator was not called with web tools disabled"
        )


async def check_files(client: DeepSeekServiceClient, key: str, directory: Path, pdf: Path) -> None:
    text = directory / "investorch-smoke.txt"
    text.write_text("The fixture answer is 42.\n")
    uploaded = await client.upload_file(text)
    try:
        assert (await client.get_file(uploaded.id)).filename == text.name
        assert any(item.id == uploaded.id for item in (await client.list_files(order="desc", limit=20)).data)
        async with AsyncOpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL, max_retries=0, timeout=60) as model:
            result = await model.responses.create(
                model=DEEPSEEK_DEFAULT_MODEL,
                reasoning={"effort": "none"},
                input=[
                    {
                        "role": "user",
                        "content": [
                            input_file(uploaded.id),
                            {"type": "input_text", "text": "What is the fixture answer?"},
                        ],
                    }
                ],
            )
            assert result.status == "completed" and "42" in result.output_text, "file reference not read"
    finally:
        await client.delete_file(uploaded.id)
    for purpose in ("text_recognition", "rich_layout_recognition"):
        parsed = await client.upload_file(pdf, purpose=purpose)
        await client.delete_file(parsed.id)


async def main() -> int:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("Set DEEPSEEK_API_KEY to run the live checks.")
        return 2
    set_tracing_disabled(True)
    failures = []

    async def check(name, action):
        try:
            await action()
        except (DeepSeekServiceError, DeepSeekResponseError, AssertionError) as error:
            failures.append(name)
            print(f"{name}: FAIL {error}", flush=True)
        except Exception as error:
            failures.append(name)
            print(f"{name}: FAIL {type(error).__name__}", flush=True)
        else:
            print(f"{name}: PASS", flush=True)

    with tempfile.TemporaryDirectory(prefix="investorch-deepseek-") as temporary:
        directory = Path(temporary)
        pdf = directory / "investorch-smoke.pdf"
        make_pdf(pdf)
        config = smoke_config(directory / "session", key)
        async with DeepSeekServiceClient(DEEPSEEK_BASE_URL, key) as service:

            async def ocr():
                result = await service.ocr(pdf)
                assert result.page_count == len(result.pages) == 1, "missing OCR page"
                assert "fixture answer" in result.pages[0].text.casefold(), "missing recognized text"

            async def speech():
                result = await service.synthesize("The answer is forty two.")
                sample = directory / "smoke.wav"
                with wave.open(BytesIO(result.data), "rb") as source, wave.open(str(sample), "wb") as target:
                    target.setparams(source.getparams()._replace(nframes=0))
                    target.writeframes(source.readframes(source.getframerate() * 10))
                transcript = await service.transcribe(sample)
                assert transcript.text.strip() and transcript.duration_ms > 0, "missing transcription"

            async def stream_speech():
                async def chunks():
                    yield "Hello "
                    yield "from InvestOrch."

                async with aclosing(service.stream_speech(chunks())) as audio:
                    sizes = [len(frame) async for frame in audio]
                assert sum(sizes) > 0, "missing audio frames"

            await asyncio.gather(
                check("Flash Responses / Session", lambda: check_responses(config)),
                check("Flash web search", lambda: check_flash_events(config, key)),
                check("Pro web search", lambda: check_pro_events(key)),
                check("Files / parsing", lambda: check_files(service, key, directory, pdf)),
                check("OCR", ocr),
                check("HTTP TTS / ASR", speech),
                check("WebSocket TTS", stream_speech),
            )
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
