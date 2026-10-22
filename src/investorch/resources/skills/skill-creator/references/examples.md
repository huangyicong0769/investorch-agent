# Complete worked example

All requests, file contents, and returned JSON below are teaching examples, not evidence that an installation occurred. Each call block uses transcript notation: invoke `tool` with the complete `arguments` object; the envelope itself is not a tool argument. Calls are sequential, and writes use the normal approval path. The example begins after loading this management Skill and reading this reference.

## Request and inputs

User: “Create and install a reusable `note-summary` Skill that summarizes a supplied note in at most five factual bullets, retaining uncertainty and dates. It should ask for the note when none is supplied.”

The request supplies the complete behavior and installation intent. No input files, scripts, network access, or private facts are needed. `note-summary` is a proposed new identity, not an assumed installed name.

## Discover identities and select staging

```json
{
  "tool": "list_skills",
  "arguments": {}
}
```

Scan the entire returned array for `note-summary` and inspect relevant registered identities. In this teaching run the array contains the package built-ins and has no `note-summary` entry. A representative record from that array is:

```json
{
  "name": "skill-creator",
  "version": "1.0.2",
  "description": "Create, revise, or fork reusable InvestOrch Skills. Use when the user wants a repeatable task workflow or customization of an installed Skill.",
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
    "name": "skill-creator"
  }
}
```

It returns that same complete metadata record. Its `resources` field inventories resources; `inspect_skill` does not return the body.

If adapting this managed built-in, load its body and read relevant listed resources, then create a separately named candidate with matching new directory/frontmatter identity. `replace: true` cannot replace a built-in. For a custom revision, inspect and read the installed content, retain its intended contract, bump its SemVer, and replace only with explicit replacement intent.

With no collision, choose `.skill-staging/note-summary` as the candidate path. It is inside Workspace and outside installed `skills/`. If it already contains a previous candidate, inspect that candidate and resolve reuse explicitly; `edit` with `create` fails rather than overwriting it.

## Prepare and independently review the candidate

```json
{
  "tool": "edit",
  "arguments": {
    "path": ".skill-staging/note-summary/SKILL.md",
    "operation": "create",
    "content": "---\nname: note-summary\ndescription: Summarize a supplied note when the user requests a concise factual summary.\nversion: 1.0.0\n---\n\nRead the user-supplied note. Summarize its claims in at most five bullets. Keep uncertainty explicit and preserve stated dates. Completion means each bullet is supported by the note. If no note was supplied, ask for it.\n",
    "old_text": ""
  }
}
```

After approval, the returned JSON is:

```json
{
  "path": ".skill-staging/note-summary/SKILL.md",
  "operation": "create",
  "size": 352
}
```

Read the created file back:

```json
{
  "tool": "explore",
  "arguments": {
    "operation": "read",
    "path": ".skill-staging/note-summary/SKILL.md",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```

The read has the same `content`, 7 total lines, and `path` equal to the `edit` response. Compare the entire content with the agreed behavior before review. This candidate has no resources or scripts; a multi-file candidate must include and verify every referenced file, and harmless script fixtures must pass before review.

```json
{
  "tool": "review_skill_candidate",
  "arguments": {
    "candidate_path": ".skill-staging/note-summary",
    "source_type": "created"
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
    "candidate_path": ".skill-staging/note-summary",
    "source_type": "created",
    "origin": null,
    "replace": false
  }
}
```

After the independent install review and normal approval succeed, the teaching response is:

```json
{
  "name": "note-summary",
  "restart_required": true,
  "message": "Restart InvestOrch to use the updated Skill catalog."
}
```

The tool returns a name and restart status, not a version, installed content, or startup-catalog success. Carry its returned `name` into inspection and derive `skills/<name>/SKILL.md` from that identity; in this run those are `note-summary` and `skills/note-summary/SKILL.md`.

## Verify artifacts and report the boundary

```json
{
  "tool": "inspect_skill",
  "arguments": {
    "name": "note-summary"
  }
}
```

The teaching response is:

```json
{
  "name": "note-summary",
  "version": "1.0.0",
  "description": "Summarize a supplied note when the user requests a concise factual summary.",
  "source": {
    "type": "created",
    "origin": null
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
    "path": "skills/note-summary/SKILL.md",
    "query": "",
    "start_line": 0,
    "end_line": 0
  }
}
```

The complete read must match the reviewed candidate `content` from above and have `path: "skills/note-summary/SKILL.md"`. Inspection must match version `1.0.0`, source `created`, enabled state, and `status: "available"`. Here `resources: []` confirms the single-file artifact inventory. The created Skill has no external origin. A mismatch is an incomplete verification: report it and resolve it before claiming a verified installation.

Final response before any restart:

> Installed and read back `note-summary` v1.0.0 as a created Skill. Its installed content matches the reviewed candidate. Restart InvestOrch to add it to the Main Agent startup catalog; opening a new session does not refresh that catalog. Discovery and loading after restart remain unverified. Installing this Skill does not authorize running scripts.

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
    "name": "note-summary"
  }
}
```

The successful teaching return is:

```json
{
  "name": "note-summary",
  "description": "Summarize a supplied note when the user requests a concise factual summary.",
  "version": "1.0.0",
  "content": "---\nname: note-summary\ndescription: Summarize a supplied note when the user requests a concise factual summary.\nversion: 1.0.0\n---\n\nRead the user-supplied note. Summarize its claims in at most five bullets. Keep uncertainty explicit and preserve stated dates. Completion means each bullet is supported by the note. If no note was supplied, ask for it.\n",
  "resources": []
}
```

Only this successful load in the restarted host supports: “After restart, `note-summary` v1.0.0 is discovered and loads successfully.” A missing/disabled/invalid identity or startup-catalog error means loading is still unresolved. Keep the pre-restart status honest, and do not use candidate cleanup as a substitute for verification.
