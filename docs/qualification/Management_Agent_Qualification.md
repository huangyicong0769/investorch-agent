# Management real-Agent qualification

Executed on macOS on **2026-10-11, 10:30:01–10:30:38 Asia/Shanghai**, against revision `91bef660cb412f1cb6405502adf9fabfb7944814` after the final credential lifecycle, Recovery and Doctor fixes. The safe machine-readable result is [Management_Agent_Qualification.json](Management_Agent_Qualification.json), including the qualification script hash and preceding attempts.

## Result

The real-provider run passed. The harness used HTTP requests through the actual FastAPI ASGI application, its real lifespan and `open_application_host`, the Agents SDK, and the configured `deepseek-flash` models. It used no model, Agent, runtime or approval mocks. This evidence covers the backend/application path; browser rendering is qualified separately.

| Check | Observed result |
| --- | --- |
| Fresh root | Entered `setup_required` with no Agent Host. |
| Setup save and explicit connection test | Five role settings and credentials saved through the Setup API; a real model request returned `ok: true`. |
| Setup completion | Started the real ApplicationHost in the same process. |
| Text-only Run | Completed and returned exactly `MANAGEMENT_AGENT_READY`, with no tools. |
| Create entry | New Session retained visible user intent; Agent asked for requirements; repeated request reused the same Session. |
| Create continuation | Real Agent loaded `skill-creator`, checked the catalog, wrote the supplied candidate using `edit`, requested independent review, and called `install_skill`. |
| Independent safety review | `review_skill_candidate` returned **PASS** from the real Skill Review Agent. Installation separately repeated its required review before manual approval became available. |
| Approval and installation | Only the exact harmless candidate write and installation were approved through `/api/approvals`; the installed Skill was `available`, version `1.0.0`, containing only `SKILL.md`. |
| Install entry | A separate real Agent Session asked for its source before acquisition. |
| Edit entry | A separate real Agent Session inspected the created Skill and asked for changes. |
| Fork entry | A separate real Agent Session inspected the built-in configuration Skill and asked for a new identity and customization. |
| Workflow identity | All four actions produced distinct Sessions; duplicate request IDs did not start duplicates. |

The test Skill, `qualification-greeting`, formats a supplied name as an uppercase greeting. It contains no scripts, external resources or network work. Main Run defaults were temporarily set to manual approval and `none` reasoning; persisted role configuration was retained. Independent review used the configured Permission role.

## Harness refinement and preceding attempts

Three preceding final-backend attempts at **10:25:36–10:27:35** did not complete the full qualification, and are retained in the JSON evidence:

1. Setup, text, Create, independent PASS review, installation and the Install entry passed. The Edit workflow requested `exec_command`; the exact fixture allowlist denied it. The original harness immediately stopped the Run instead of letting the Agent observe the denial and continue its dialogue.
2. The model supplied the string `origin="None"` for a created Skill. Existing domain rules rejected the nonempty origin before installation; the installed-Skill check then returned HTTP 404.
3. The model repeated that incorrect origin argument and was rejected again.

Only the qualification script was refined: instructions explicitly require JSON `null`, never a string; rejected operations remain denied while the Agent can continue dialogue, with a three-denial cap; and one visible, recorded user correction may finish the already authorized exact installation. Production rules and the approval allowlist were unchanged. The successful run needed **zero corrections**, requested no rejected approvals, and approved no command execution. It completed all four distinct, idempotent workflow entries and the real Create installation chain.

An earlier complete pass at revision `c03ec1d` remains historical evidence; the result above independently qualifies the final backend. The failed attempts show model-dependent behavior and are not presented as successful first-shot runs.

## Production-source finding

The initial read-only `load_config(migrate=False)` rejected the owner's existing Local TOML because `observability.summary_enabled` is unknown to the strict Schema. The real root therefore still requires operator recovery before ordinary startup. The qualifier did **not** remove that key, weaken Schema validation, migrate the real root, or claim that its upgrade succeeded.

For the isolated test only, an explicitly authorized fallback read the source TOML in memory, selected only model and secret fields, and combined historical model defaults with existing per-role overrides. Setup received those fields in a fresh temporary root. No production Workspace, database, registry or MCP configuration was copied. The before/after SHA-256 of the complete source configuration was identical, as recorded in the JSON evidence.

The temporary roots were retained for local inspection. Credentials were not printed or included in evidence; the report was checked against source secret values. Application logging and SDK tracing were disabled. The final run and all three preceding attempts used the permitted network escalation and left the source configuration hash unchanged: `30d2b15d2e357ad85ad3f02da002c9a72b4e2e1657743ff4143fbe49776c8dc0`.

## Scope limits and reproduction

Install/Edit/Fork were qualified through their real clarification-start and idempotency behavior; their later acquisition, replacement and fork mutations were not performed. Real PASS plus the install approval chain was observed. WARN/BLOCK behavior remains covered by behavioral fixtures; this run did not induce those verdicts. This report does not claim browser acceptance or complete release acceptance.

Run the opt-in qualifier with existing usable model credentials:

```bash
uv run --locked python scripts/qualify_management_agent.py --output /tmp/management-agent-qualification.json
```

The script creates its own temporary root, makes real provider calls, uses bounded waits, rejects unexpected approvals, records any bounded user correction and returns nonzero when an expected result is absent. Its narrow legacy-field fallback is limited to the identified source-validation failure above and is not part of application startup or migration. The successful run did not exercise the new correction or denied-dialogue continuation branches.
