---
name: skill-installer
description: Acquire and install external Skills or replace custom Skills. Use when a user supplies a Skill directory, archive, URL, repository, or other source.
version: 1.0.0
---

# Install a Skill

1. Identify the requested source and intended identity. Acquire content with currently available Workspace tools into `.skill-staging/<name>/`. The manager accepts a local Workspace candidate directory; it does not fetch repositories, URLs, or archives. Acquisition and extraction use existing approval rules; inspect archive paths before extraction and keep resources within the candidate root.
2. Inspect `list_skills` and the candidate. `SKILL.md` needs matching directory/frontmatter name, a meaningful description, nonempty body, and UTF-8 YAML. External version is optional; any supplied version must be SemVer. Keep original source provenance as `external` and supply an origin when known. Do not claim external content is built-in.
3. Resolve name collisions explicitly: replace/update an existing non-built-in only when requested, or fork with a new directory/name. Built-ins cannot be Agent-replaced; customization requires a separately named fork. Never silently shadow another Skill.
4. Call `review_skill_candidate`. Structural validation precedes safety review. PASS reports no materially unsafe behavior, not approval. Summarize important WARN behavior and require manual approval; BLOCK prohibits installation without a bypass. If content changes, review it again.
5. Call `install_skill` with the candidate, source, and explicit replacement choice. Installation independently reviews current candidate contents and goes through approval; an earlier review is not a reusable permission grant. Use dedicated `remove_skill` and `set_skill_enabled` for authorized lifecycle changes rather than direct directory/registry mutation.
6. Report the result and `restart_required`. Restart InvestOrch before expecting newly installed/enabled Skills in the Main Agent catalog; opening another session does not reload it. After restart, verify discovery and `load_skill`. Remove only temporary acquisition files when cleanup is appropriate.

Installing a Skill grants no execution capability. Scripts remain subject to `exec_command`, sandbox, and approval. `investorch --update` updates only package-owned built-ins, preserving enabled flags; external/created updates use this candidate-review-install workflow. Skills contain reusable knowledge, not user secrets, memory, tool implementations, or execution results.
