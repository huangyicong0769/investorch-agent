"""Opt-in real-provider qualification of Setup, Agent runs and Skill management.

Uses only model settings/credentials from read-only source configuration. Creates
fresh state under a temporary root; no production Workspace or databases are
copied. Run with ``uv run --locked python scripts/qualify_management_agent.py``.
Only a safe summary is written to the requested evidence file.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from agents import set_tracing_disabled

from investorch.config import REQUIRED_MODEL_AGENTS, load_config
from investorch.web.server import create_web_app

SKILL_NAME = "qualification-greeting"
CANDIDATE = f".skill-staging/{SKILL_NAME}"
SKILL_BODY = """---
name: qualification-greeting
description: Render a supplied name in uppercase for a greeting when the user requests this qualification greeting.
version: 1.0.0
---

# Qualification Greeting

1. Ask for the name when the user has not supplied one.
2. Convert the supplied name to uppercase in your response.
3. Reply with only `HELLO, NAME!`, replacing NAME with that uppercase name.

This workflow uses text only. It requires no scripts, files, credentials, network requests, or external tools.
"""


class QualificationFailure(RuntimeError):
    """A bounded qualification step failed; its message contains no credentials."""


def progress(step: str) -> None:
    print(json.dumps({"progress": step}), flush=True)


async def request(client: httpx.AsyncClient, method: str, path: str, **kwargs: Any) -> Any:
    response = await client.request(method, path, **kwargs)
    if not response.is_success:
        # Never export request or response bodies from a failed credential operation.
        raise QualificationFailure(f"{method} {path.split('?')[0]} returned HTTP {response.status_code}")
    return response.json()


def _approval_allowed(approval: dict[str, Any], root: Path) -> bool:
    try:
        arguments = json.loads(approval.get("arguments") or "{}")
    except (TypeError, ValueError):
        return False
    if approval["tool_name"] == "edit":
        return (
            arguments.get("path") == f"{CANDIDATE}/SKILL.md"
            and arguments.get("operation") == "create"
            and arguments.get("content", "").strip() == SKILL_BODY.strip()
            and not arguments.get("old_text")
        )
    if approval["tool_name"] == "install_skill":
        candidate = root / "workspace" / CANDIDATE / "SKILL.md"
        return (
            arguments.get("candidate_path") == CANDIDATE
            and arguments.get("source_type") == "created"
            and not arguments.get("replace")
            and arguments.get("origin") in (None, "")
            and candidate.is_file()
            and candidate.read_text().strip() == SKILL_BODY.strip()
        )
    return False


def summarize_history(records: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {
        "tools": [record["name"] for record in records if record["type"] == "tool_called"],
        "run_statuses": [record["status"] for record in records if record["type"] == "run_ended"],
        "review_verdicts": [],
        "approvals": [],
    }
    last_tool = None
    for record in records:
        if record["type"] == "tool_called":
            last_tool = record["name"]
        elif record["type"] == "tool_output" and last_tool == "review_skill_candidate":
            output = record.get("output")
            if isinstance(output, str):
                try:
                    output = json.loads(output)
                except ValueError:
                    try:
                        output = ast.literal_eval(output)
                    except (ValueError, SyntaxError):
                        output = None
            if isinstance(output, dict) and output.get("decision") in {"pass", "warn", "block"}:
                results["review_verdicts"].append(output["decision"])
        elif record["type"] == "approval":
            results["approvals"].append(
                {key: record.get(key) for key in ("tool_name", "approved", "source", "review_decision")}
            )
    return results


async def wait_for_run(
    client: httpx.AsyncClient, session_id: str, *, root: Path, allow_fixture_writes: bool = False, timeout: int = 240
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + timeout
    seen_approvals: set[str] = set()
    denied_approvals = 0
    while time.monotonic() < deadline:
        state = await request(client, "GET", f"/api/sessions/{session_id}/state")
        for approval in state["pending_approvals"]:
            approval_id = approval["approval_id"]
            if approval_id in seen_approvals:
                continue
            allowed = allow_fixture_writes and _approval_allowed(approval, root)
            await request(client, "POST", f"/api/approvals/{approval_id}", json={"approved": allowed})
            seen_approvals.add(approval_id)
            progress(f"approval:{approval['tool_name']}:{'approved' if allowed else 'denied'}")
            if not allowed:
                # Let the real Agent observe the denial and continue its dialogue.
                # The approved operations remain limited to the exact fixture.
                denied_approvals += 1
                if denied_approvals >= 3:
                    await request(client, "POST", f"/api/sessions/{session_id}/stop")
                    raise QualificationFailure("Agent repeatedly requested operations outside the fixture allowlist")
        if state["runtime"]["run_id"] is None:
            history = await request(client, "GET", f"/api/sessions/{session_id}/history?limit=1000")
            if history["has_older"]:
                raise QualificationFailure("Qualification history exceeded its bounded evidence window")
            records = history["records"]
            ended = [item for item in records if item["type"] == "run_ended"]
            if ended:
                if ended[-1]["status"] != "completed":
                    raise QualificationFailure(f"Agent Run ended with status {ended[-1]['status']}")
                return records
        await asyncio.sleep(0.5)
    await request(client, "POST", f"/api/sessions/{session_id}/stop")
    raise QualificationFailure("Agent Run exceeded the qualification time limit")


async def start_workflow(
    client: httpx.AsyncClient, action: str, root: Path, *, name: str | None = None
) -> tuple[str, dict[str, Any]]:
    body = {"action": action, "request_id": f"qualification-{action}"}
    if name:
        body["name"] = name
    first = await request(client, "POST", "/api/skill-sessions", json=body)
    repeated = await request(client, "POST", "/api/skill-sessions", json=body)
    session_id = first["session"]["session_id"]
    if repeated["session"]["session_id"] != session_id or repeated["started"]:
        raise QualificationFailure("Repeated workflow request was not idempotent")
    records = await wait_for_run(client, session_id, root=root)
    messages = [item["text"] for item in records if item["type"] == "assistant_message"]
    users = [item["text"] for item in records if item["type"] == "user_message"]
    if not messages or len(users) != 1:
        raise QualificationFailure("Workflow did not retain its visible starter intent and Agent response")
    result = {
        "session_id": session_id,
        "idempotent": True,
        "visible_intent": users[0],
        "agent_asked_for_requirements": any("?" in text or "\uff1f" in text for text in messages),
        "assistant_response_excerpt": messages[-1][:600],
        **summarize_history(records),
    }
    progress(f"workflow:{action}:completed")
    return session_id, result


async def qualify(report: dict[str, Any], root: Path) -> None:
    source_path = Path.home() / ".investorch/investorch.toml"
    source_bytes = source_path.read_bytes()
    before = hashlib.sha256(source_bytes).hexdigest()
    source = load_config()
    if source.readiness():
        raise QualificationFailure("Existing model configuration is not ready for real-provider qualification")
    models = {role: source.get(f"models.{role}") for role in REQUIRED_MODEL_AGENTS}
    source_secrets = source.secrets
    credentials = {}
    for model in models.values():
        secret = source_secrets.get(model["api_key_secret"])
        if not isinstance(secret, str) or not secret.strip():
            raise QualificationFailure("A configured model role has no usable credential")
        credentials[model["api_key_secret"]] = secret
    report["model_roles"] = {role: models[role]["name"] for role in REQUIRED_MODEL_AGENTS}
    report["source_configuration_sha256_before"] = before
    try:
        app = create_web_app(root=root)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://127.0.0.1:1334",
                timeout=30,
                headers={"Origin": "http://127.0.0.1:1334"},
            ) as client,
        ):
            state = await request(client, "GET", "/api/setup/state")
            if state["status"] != "setup_required":
                raise QualificationFailure("Fresh isolated instance did not enter Setup")
            changes = {}
            for role in REQUIRED_MODEL_AGENTS:
                model = models[role]
                for field, value in model.items():
                    if value is not None:
                        changes[f"models.{role}.{field}"] = value
            for name, value in credentials.items():
                changes[f"secrets.{name}"] = value
            await request(client, "PATCH", "/api/setup/config", json={"changes": changes})
            main = models["main"]
            report["setup_model_test"] = await request(
                client,
                "POST",
                "/api/setup/test-model",
                json={
                    "name": main["name"],
                    "base_url": main["base_url"],
                    "api_key": credentials[main["api_key_secret"]],
                    "reasoning_effort": "none",
                },
            )
            completed = await request(client, "POST", "/api/setup/complete", json={})
            report["setup_completed_same_process"] = completed["status"] == "ready"
            if not report["setup_completed_same_process"]:
                raise QualificationFailure("Setup did not start the real ApplicationHost")
            progress("setup:ready")
            await request(
                client, "PATCH", "/api/defaults", json={"permission_mode": "manual", "reasoning_effort": "none"}
            )
            created = await request(client, "POST", "/api/sessions", json={})
            session_id = created["session"]["session_id"]
            await request(
                client,
                "POST",
                f"/api/sessions/{session_id}/messages",
                json={"text": "Reply with exactly MANAGEMENT_AGENT_READY. Use no tools."},
            )
            records = await wait_for_run(client, session_id, root=root)
            report["text_run"] = {"session_id": session_id, **summarize_history(records)}
            if not any(
                item["type"] == "assistant_message" and item["text"].strip() == "MANAGEMENT_AGENT_READY"
                for item in records
            ):
                raise QualificationFailure("Real Agent did not produce the requested deterministic text")
            progress("text:completed")

            report["workflows"] = {}
            create_session, created = await start_workflow(client, "create", root)
            report["workflows"]["create"] = created
            await request(
                client,
                "POST",
                f"/api/sessions/{create_session}/messages",
                json={
                    "text": (
                        "Create and install the harmless text-only Skill below. Its exact name is qualification-greeting. "
                        "Use edit create at .skill-staging/qualification-greeting/SKILL.md; edit creates parent directories. "
                        "Use exactly the supplied file content without changes. Then call review_skill_candidate with source_type created, "
                        "then install_skill with source_type created, replace false, and origin set to JSON null. "
                        "The origin argument must be JSON null, never the string None or the string null. "
                        "I authorize only that candidate creation and installation after normal independent review and manual approval. "
                        "Do not run commands or scripts, use network tools, change configuration or other files, or clean up the candidate. "
                        "Stop after reporting installation and restart requirement. File content follows:\n\n"
                        + SKILL_BODY
                    )
                },
            )
            records = await wait_for_run(client, create_session, root=root, allow_fixture_writes=True)
            installed_response = await client.get(f"/api/skills/{SKILL_NAME}")
            if installed_response.status_code == 404:
                # One visible, bounded user correction is allowed for this exact
                # previously authorized candidate; domain review and approval stay intact.
                report["interventions"] = [
                    {
                        "kind": "visible_user_correction",
                        "session_id": create_session,
                        "reason": "The first continuation completed without installing the authorized candidate.",
                        "initial_continuation": summarize_history(records),
                    }
                ]
                await request(
                    client,
                    "POST",
                    f"/api/sessions/{create_session}/messages",
                    json={
                        "text": (
                            "The previously authorized Skill has not been installed. Correct any failed tool arguments "
                            "and finish that same installation, retaining the existing independent review and manual approval. "
                            "Call install_skill for candidate_path .skill-staging/qualification-greeting, "
                            "source_type created, replace false, and origin set to JSON null. "
                            "Never use the string None or the string null for origin. "
                            "Preserve the exact supplied candidate content. Do not run commands or scripts, use network "
                            "tools, change configuration or other files, or clean up the candidate. "
                            "Stop after reporting the installation result and restart requirement."
                        )
                    },
                )
                records = await wait_for_run(client, create_session, root=root, allow_fixture_writes=True)
            installed = await request(client, "GET", f"/api/skills/{SKILL_NAME}")
            report["created_skill_install"] = {
                **summarize_history(records),
                "name": SKILL_NAME,
                "inspect_status": installed.get("status"),
            }
            if "install_skill" not in report["created_skill_install"]["tools"]:
                raise QualificationFailure("The real Agent did not invoke install_skill")
            if not report["created_skill_install"]["review_verdicts"]:
                raise QualificationFailure("No real independent review verdict was observed")
            progress("skill:installed")
            for action, name in (("install", None), ("edit", SKILL_NAME), ("fork", "investorch-configuration")):
                _, result = await start_workflow(client, action, root, name=name)
                report["workflows"][action] = result
            if len({value["session_id"] for value in report["workflows"].values()}) != 4:
                raise QualificationFailure("Skill actions did not create separate Sessions")
            approvals = report["created_skill_install"]["approvals"] + [
                approval for workflow in report["workflows"].values() for approval in workflow["approvals"]
            ]
            report["unauthorized_command_approved"] = any(
                approval["tool_name"] == "exec_command" and approval["approved"] for approval in approvals
            )
            if report["unauthorized_command_approved"]:
                raise QualificationFailure("An unauthorized command was approved")
            report["status"] = "passed"
    finally:
        after = hashlib.sha256(source_path.read_bytes()).hexdigest()
        report["source_configuration_sha256_after"] = after
        report["source_configuration_unchanged"] = after == before
        serialized = json.dumps(report)
        if any(secret and secret in serialized for secret in credentials.values()):
            report.clear()
            report.update(status="failed", error="Secret content rejected from qualification evidence")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    logging.disable(logging.CRITICAL)
    set_tracing_disabled(True)
    root = Path(tempfile.mkdtemp(prefix="investorch-management-qualification-"))
    os.chmod(root, 0o700)
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "status": "running",
        "transport": "ASGI HTTP + real ApplicationHost/provider",
        "isolated_root": str(root),
        "production_workspace_copied": False,
        "mocks": False,
    }
    progress(f"isolated_root:{root}")
    try:
        asyncio.run(asyncio.wait_for(qualify(report, root), timeout=1200))
    except Exception as error:
        report["status"] = "failed"
        report["error_type"] = type(error).__name__
        if isinstance(error, QualificationFailure):
            report["error"] = str(error)
    report["finished_at"] = datetime.now(UTC).isoformat()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"status": report["status"], "evidence": str(options.output)}), flush=True)
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
