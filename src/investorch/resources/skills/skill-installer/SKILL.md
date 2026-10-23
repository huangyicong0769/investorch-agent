---
name: skill-installer
description: Acquire and install external Skills or replace custom Skills. Use when a user supplies a Skill directory, archive, URL, repository, or other source.
version: 1.0.2
---

# Install a Skill

Before acquiring or installing a candidate, read [the complete worked example](references/examples.md). It shows a supplied Workspace source, legal tool arguments, collision checks, review, installation verification, and the separate restart boundary.

1. Identify the requested source and intended identity. Acquire content with currently available Workspace tools into `.skill-staging/<name>/`. The manager accepts a local Workspace candidate directory; it does not fetch repositories, URLs, or archives. Acquisition and extraction use existing approval rules; inspect archive paths before extraction and keep resources within the candidate root.
2. Call `list_skills` for identity collisions; use `inspect_skill(name)` for relevant registered metadata and resource inventory, and `explore` to read the candidate for task fit. Inspection does not load a Skill body. Keep original source provenance as `external` and supply an origin when known. Do not claim external content is built-in.
3. Resolve name collisions explicitly: replace/update an existing non-built-in only when requested, or fork with a new directory/name. Built-ins cannot be Agent-replaced; customization requires a separately named fork. Never silently shadow another Skill.
4. Call `review_skill_candidate` as the first structural check: it validates UTF-8, YAML, matching directory/frontmatter identity, description/body, optional SemVer version, and resource boundaries before safety review. Repair the specific validation error and retry; investigate encoding separately only when validation reports a decoding failure. A command/environment failure is not evidence of invalid encoding. PASS reports no materially unsafe behavior, not approval. Summarize important WARN behavior and require manual approval; BLOCK ends the installation path; report the reason and revise or decline the candidate. If content changes, review it again.
5. Call `install_skill` with the candidate, source, and explicit replacement choice. Installation independently reviews current candidate contents and goes through approval; an earlier review is not a reusable permission grant. Use dedicated `remove_skill` and `set_skill_enabled` for authorized lifecycle changes rather than direct directory/registry mutation.
6. Read back the returned name with `inspect_skill`, then use `explore` to compare installed `skills/<returned-name>/SKILL.md` and resources with the reviewed candidate. Verify identity, version, provenance, enabled state, and `status`; an install response alone is not content verification. Report the result and `restart_required`. Restart InvestOrch before expecting newly installed/enabled Skills in the Main Agent catalog; opening another session does not reload it. After restart, verify discovery and `load_skill`. Remove only temporary acquisition files when cleanup is appropriate.

Installing a Skill grants no execution capability. Scripts remain subject to `exec_command`, sandbox, and approval. `investorch --update` updates only package-owned built-ins, preserving enabled flags; external/created updates use this candidate-review-install workflow. Skills contain reusable knowledge, not user secrets, memory, tool implementations, or execution results.
