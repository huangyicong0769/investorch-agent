# Management real-Agent qualification

Executed on macOS on **2026-10-11, 10:15:01–10:15:32 Asia/Shanghai**, against feature revision `c03ec1d` with the new qualification script. The safe machine-readable result is [Management_Agent_Qualification.json](Management_Agent_Qualification.json).

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

## Production-source finding

The initial read-only `load_config(migrate=False)` rejected the owner's existing Local TOML because `observability.summary_enabled` is unknown to the strict Schema. The real root therefore still requires operator recovery before ordinary startup. The qualifier did **not** remove that key, weaken Schema validation, migrate the real root, or claim that its upgrade succeeded.

For the isolated test only, an explicitly authorized fallback read the source TOML in memory, selected only model and secret fields, and combined historical model defaults with existing per-role overrides. Setup received those fields in a fresh temporary root. No production Workspace, database, registry or MCP configuration was copied. The before/after SHA-256 of the complete source configuration was identical, as recorded in the JSON evidence.

The temporary root was retained for local inspection. Credentials were not printed or included in evidence, and application logging was disabled for this run; the isolated application log was empty. The first restricted-sandbox attempt failed its provider request. The successful run used the permitted network escalation to reach the already configured provider.

## Scope limits and reproduction

Install/Edit/Fork were qualified through their real clarification-start and idempotency behavior; their later acquisition, replacement and fork mutations were not performed. Real PASS plus the install approval chain was observed. WARN/BLOCK behavior remains covered by behavioral fixtures; this run did not induce those verdicts. This report does not claim browser acceptance or complete release acceptance.

Run the opt-in qualifier with existing usable model credentials:

```bash
uv run --locked python scripts/qualify_management_agent.py --output /tmp/management-agent-qualification.json
```

The script creates its own temporary root, makes real provider calls, uses bounded waits, rejects unexpected approvals, and returns nonzero when an expected result is absent. Its narrow legacy-field fallback is limited to the identified source-validation failure above and is not part of application startup or migration.
