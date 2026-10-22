# Complete worked example

All requests, file contents, and returned JSON below are teaching examples, not evidence that an installation occurred. Each call block uses transcript notation: invoke `tool` with the complete `arguments` object; the envelope itself is not a tool argument. Calls are sequential, and writes use the normal approval path. The example begins after loading this management Skill and reading this reference.

## Request and inputs

User: “Install the Skill I supplied at `uploads/note-review/SKILL.md`. Keep its identity and source; do not replace an existing Skill.”

The supplied Workspace file contains exactly:

```markdown
---
name: note-review
description: Review a supplied note when the user asks for decisions and unresolved questions.
version: 1.0.0
---

Read the supplied note. Return two lists: stated decisions and unresolved questions. Preserve owners and dates exactly when supplied; mark absent owners or dates as unspecified. Completion means every listed item is supported by the note. If no note was supplied, ask for it.
```

This is a local, single-file source. Read it before copying; no repository, archive, or download tool is implied. A URL/repository/archive request instead needs acquisition with available Workspace tools under normal approval, an archive-path check before extraction, and a candidate containing the entire inspected bundle. If acquisition is unavailable, report that blocker rather than inventing a fetching capability in `install_skill`.

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "read",
    "path": "uploads/note-review/SKILL.md",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```

The full read returns:

```json
{
  "type": "file",
  "path": "uploads/note-review/SKILL.md",
  "total_lines": 7,
  "start_line": 1,
  "end_line": 7,
  "content": "---\nname: note-review\ndescription: Review a supplied note when the user asks for decisions and unresolved questions.\nversion: 1.0.0\n---\n\nRead the supplied note. Return two lists: stated decisions and unresolved questions. Preserve owners and dates exactly when supplied; mark absent owners or dates as unspecified. Completion means every listed item is supported by the note. If no note was supplied, ask for it.\n"
}
```

Continue only when the read is complete, task fit is established, and all source resources are accounted for. Here the source has only the supplied `SKILL.md`.

## Discover identities and select staging

```json
{
  "tool": "list_skills",
  "arguments": {}
}
```

Scan the entire returned array for `note-review` and inspect relevant registered identities. In this teaching run the array contains the package built-ins and has no `note-review` entry. A representative record from that array is:

```json
{
  "name": "skill-installer",
  "version": "1.0.2",
  "description": "Acquire and install external Skills or replace custom Skills. Use when a user supplies a Skill directory, archive, URL, repository, or other source.",
  "source": {
    "type": "builtin",
    "origin": null
  },
  "enabled": true,
  "resources": [
    "references/examples.md"
  ],
  "status": "available"
}
```
```json
{
  "tool": "inspect_skill",
  "arguments": {
    "name": "skill-installer"
  }
}
```

It returns that same complete metadata record. Its `resources` field inventories resources; `inspect_skill` does not return the body.

If the requested name already exists, inspect that exact name and stop before copying over it: this request explicitly disallows replacement. For an authorized update to a custom Skill, retain `external` provenance, review the whole new candidate, and use `replace: true`. For a managed built-in collision, create a separately named fork with matching metadata. An unregistered installed directory may also collide; a later collision error requires inspection and a resolved identity, not direct registry changes.

With no collision, choose `.skill-staging/note-review` as the candidate path. It is inside Workspace and outside installed `skills/`. If it already contains a previous candidate, inspect that candidate and resolve reuse explicitly; `edit` with `create` fails rather than overwriting it.

## Prepare and independently review the candidate

```json
{
  "tool": "edit",
  "arguments": {
    "path": ".skill-staging/note-review/SKILL.md",
    "operation": "create",
    "content": "---\nname: note-review\ndescription: Review a supplied note when the user asks for decisions and unresolved questions.\nversion: 1.0.0\n---\n\nRead the supplied note. Return two lists: stated decisions and unresolved questions. Preserve owners and dates exactly when supplied; mark absent owners or dates as unspecified. Completion means every listed item is supported by the note. If no note was supplied, ask for it.\n",
    "old_text": ""
  }
}
```

After approval, the returned JSON is:

```json
{
  "path": ".skill-staging/note-review/SKILL.md",
  "operation": "create",
  "size": 413
}
```

Read the created file back:

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "read",
    "path": ".skill-staging/note-review/SKILL.md",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```

The read has the same `content`, 7 total lines, and `path` equal to the `edit` response. Compare the entire content with the acquired source before review. This candidate has no resources or scripts; a multi-file candidate must include and verify every referenced file, and harmless script fixtures must pass before review.

```json
{
  "tool": "review_skill_candidate",
  "arguments": {
    "candidate_path": ".skill-staging/note-review",
    "source_type": "external"
  }
}
```

The teaching review returns:

```json
{
  "decision": "pass",
  "reason": "The candidate only describes factual note processing and requests no unsafe access or execution."
}
```

Branch on the actual result: `pass` allows proceeding to the authorized installation request; it does not grant installation authorization. `warn` requires explaining the material behavior and manual user approval. `block` ends this installation attempt. A structural error precedes the safety decision: repair the reported UTF-8/YAML/identity/version/resource problem and review again. A tool or environment failure is not an encoding diagnosis. Any content change requires another review.

## Install through the managed approval path

The user explicitly requested installation. Call the dedicated tool with the unchanged reviewed candidate; its approval flow reviews the current contents again. A denied approval or failed second review leaves installation incomplete. No filesystem copy into `skills/` or registry edit substitutes for this step.

```json
{
  "tool": "install_skill",
  "arguments": {
    "candidate_path": ".skill-staging/note-review",
    "source_type": "external",
    "origin": "user-upload",
    "replace": false
  }
}
```

After the independent install review and normal approval succeed, the teaching response is:

```json
{
  "name": "note-review",
  "restart_required": true,
  "message": "Restart InvestOrch to use the updated Skill catalog."
}
```

The tool returns a name and restart status, not a version, installed content, or startup-catalog success. Carry its returned `name` into inspection and derive `skills/<name>/SKILL.md` from that identity; in this run those are `note-review` and `skills/note-review/SKILL.md`.

## Verify artifacts and report the boundary

```json
{
  "tool": "inspect_skill",
  "arguments": {
    "name": "note-review"
  }
}
```

The teaching response is:

```json
{
  "name": "note-review",
  "version": "1.0.0",
  "description": "Review a supplied note when the user asks for decisions and unresolved questions.",
  "source": {
    "type": "external",
    "origin": "[configured]"
  },
  "enabled": true,
  "resources": [],
  "status": "available"
}
```

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "read",
    "path": "skills/note-review/SKILL.md",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```

The complete read must match the reviewed candidate `content` from above and have `path: "skills/note-review/SKILL.md"`. Inspection must match version `1.0.0`, source `external`, enabled state, and `status: "available"`. Here `resources: []` confirms the single-file artifact inventory. The public origin is intentionally redacted to `[configured]`; it does not echo the supplied local provenance reference. A mismatch is an incomplete verification: report it and resolve it before claiming a verified installation.

Final response before any restart:

> Installed and read back `note-review` v1.0.0 as a user-supplied external Skill. Its installed content matches the reviewed candidate. Restart InvestOrch to add it to the Main Agent startup catalog; opening a new session does not refresh that catalog. Discovery and loading after restart remain unverified. Installing this Skill does not authorize running scripts.

## Only after an observed operator restart

This example does not restart the application. Once the operator has actually restarted InvestOrch, verify the startup catalog through the newly running Agent and call:

```json
{
  "tool": "list_skills",
  "arguments": {}
}
```

Confirm that the full array includes the same available, enabled identity/version above, then call:

```json
{
  "tool": "load_skill",
  "arguments": {
    "name": "note-review"
  }
}
```

The successful teaching return is:

```json
{
  "name": "note-review",
  "description": "Review a supplied note when the user asks for decisions and unresolved questions.",
  "version": "1.0.0",
  "content": "---\nname: note-review\ndescription: Review a supplied note when the user asks for decisions and unresolved questions.\nversion: 1.0.0\n---\n\nRead the supplied note. Return two lists: stated decisions and unresolved questions. Preserve owners and dates exactly when supplied; mark absent owners or dates as unspecified. Completion means every listed item is supported by the note. If no note was supplied, ask for it.\n",
  "resources": []
}
```

Only this successful load in the restarted host supports: “After restart, `note-review` v1.0.0 is discovered and loads successfully.” A missing/disabled/invalid identity or startup-catalog error means loading is still unresolved. Keep the pre-restart status honest, and do not use candidate cleanup as a substitute for verification.
