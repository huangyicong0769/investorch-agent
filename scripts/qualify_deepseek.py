"""Run the DeepSeek Session workflow with DEEPSEEK_API_KEY and synthetic inputs."""

import asyncio
import os
import tempfile
from pathlib import Path

from agents import set_tracing_disabled

from investorch.application import open_application_host
from investorch.config import REQUIRED_AGENT_ROLES, AppConfig
from investorch.initializer import initialize
from investorch.journal import read_session_journal
from investorch.llm.deepseek import DeepSeekResponseError
from investorch.runtime import RunDefaults
from investorch.storage import get_session_title


def has_completed_web_call(records: list[dict]) -> bool:
    stages: dict[str, list[str]] = {}
    for record in records:
        if record.get("hosted_call_id"):
            stages.setdefault(record["hosted_call_id"], []).append(record["hosted_status"])
    return any(
        {"in_progress", "searching"} <= set(statuses) and statuses[-1] == "completed" for statuses in stages.values()
    )


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


async def main() -> int:
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("Set DEEPSEEK_API_KEY to run the live checks.")
        return 2
    set_tracing_disabled(True)
    try:
        with tempfile.TemporaryDirectory(prefix="investorch-deepseek-") as temporary:
            config = smoke_config(Path(temporary) / "session", key)
            await check_responses(config)
    except (DeepSeekResponseError, AssertionError) as error:
        print(f"Flash Responses / Session: FAIL {error}", flush=True)
        return 1
    except Exception as error:
        print(f"Flash Responses / Session: FAIL {type(error).__name__}", flush=True)
        return 1
    print("Flash Responses / Session: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
