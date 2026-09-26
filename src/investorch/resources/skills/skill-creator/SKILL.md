---
name: skill-creator
description: Create, revise, or fork reusable InvestOrch Skills. Use when the user wants a repeatable task workflow or customization of an installed Skill.
version: 1.0.0
---

# Create a Skill

1. Establish one reusable task and its completion criteria. Put user/project facts and preferences in Memory, task output in artifacts, and executable primitives in Tools. A Skill supplies stateless methods and resources; it grants no permissions and stores no session or execution state.
2. Inspect `list_skills` for collisions. Choose a lowercase hyphenated name matching its directory. For a fork, inspect the original and copy into `.skill-staging/<new-name>/`; give it a new identity. Built-in customization always uses this fork path.
3. Use ordinary `edit`/`exec_command` tools to create the candidate outside installed `skills/`. Write UTF-8 `SKILL.md` with YAML `name`, `description`, and a SemVer `version` (start at `1.0.0`). Description states both the task and when to use it. Keep the body focused on ordered work, branch decisions, and verifiable completion. Do not add another manifest or trigger metadata.
4. Put long conditional knowledge in `references/`, helpers in `scripts/`, and templates in `assets/`; explicitly point to each needed resource and when to read it. Keep all resources inside the candidate root. Test supplied scripts with harmless fixtures through `exec_command` and its normal approval path. Installation never preauthorizes later script execution.
5. Call `review_skill_candidate` on the candidate directory. It performs deterministic validation before independent safety review. Repair structural failures. Explain material WARN behavior to the user; WARN requires manual approval. BLOCK cannot be installed, and PASS is not installation authorization.
6. Use `install_skill` with source `created`. A replacement of an existing non-built-in needs explicit replacement intent; use a new name for a fork. Installation repeats independent review of the candidate then uses normal approval. Never edit the registry or installed built-in directly.
7. Report installed identity/version and restart requirement. The running host keeps its startup catalog and loadable names until restart; a new session does not refresh them. After restart, verify catalog discovery and `load_skill`. Clean up only the candidate if appropriate.

For revisions, use a candidate and bump SemVer according to changed compatibility: patch for compatible corrections, minor for compatible additions, major for incompatible workflow contracts. Built-ins can be enabled/disabled but not removed or replaced by Agent tools. `investorch --update` replaces managed built-ins from the package, preserving enabled state; keep customizations in a separately named fork.
