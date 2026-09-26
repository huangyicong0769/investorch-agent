# InvestOrch Agent Product Roadmap

[简体中文](InvestOrch_Agent_Product_Roadmap.zh-CN.md)

## Purpose

This document records completed work, confirmed directions, open decisions, and candidate themes. Versions, schedules, and detailed APIs are decided separately.

The roadmap uses four states:

- **Completed**: implemented and supported by code or tests.
- **Confirmed direction**: a chosen product direction whose version, sequence, and implementation remain open.
- **Open decision**: a question to resolve when its surrounding work begins.
- **Candidate theme**: an idea to revisit when real usage provides a trigger.

## Product outcome

InvestOrch Agent is intended to become a local-first, human-in-the-loop investment orchestration product for an individual investor. Its long-term goal covers:

```text
research
  -> strategy development
  -> backtesting
  -> portfolio decision
  -> trading
  -> position monitoring and review
```

Version 0.1.0 is an early preview of this goal.

## Completed in 0.1.0

Version 0.1.0 includes:

- first-class Web and TUI clients, plus a diagnostic plain console;
- local configuration, workspace, journals, logs, session metadata, and artifacts;
- persistent session lifecycle, concurrent cross-session runs, Steer/Queue follow-ups, approval, compaction, and usage presentation;
- workspace, command-execution, configuration, MCP-registry, and Todo tools;
- RQAlpha daily stock backtesting with reproducibility metadata and artifact output;
- optional CNEquity integration for the existing overlay and read-only MCP path.

## 0.2.0 delivery sequence

| Stage | Status | Scope |
| --- | --- | --- |
| 0.2.0.A | Complete | Portfolio/User domain |
| Transition | Complete on feature base | Portfolio schema v6 and legacy live schema retirement |
| 0.2.0.B | Current | Skill Component and six built-in workflows |
| 0.2.0.C | Next | QMT strategy authoring quality and human Big QMT deployment |
| Old direct QMT live execution | Blocked / archived | Feasibility work; not a supported execution route |

The current QMT route is Agent-generated strategy artifact → human review → human deployment/run in Big QMT. Skill infrastructure does not restore a gateway, remote execution, order bridge, live broker, account mirror, or automatic settlement reconciliation.

## Confirmed direction: unified investment data

A unified, traceable investment data capability is a long-term direction for research and backtesting. No schema, source model, storage engine, update lifecycle, point-in-time policy, or query interface is confirmed yet.

CNEquity remains an optional backend. Broader reliance on it is deferred until upstream behavior is sufficiently stable, while the core data contract remains backend-neutral.

## Confirmed direction: Multi-Agent

Multi-Agent is a long-term direction. No specialist roles, routing model, handoff topology, or implementation schedule is confirmed. Agent boundaries will be designed only after real responsibilities prove that separation reduces complexity.

## Confirmed direction: Agent as MCP

InvestOrch Agent will expose selected Agent capabilities through an MCP server so other MCP clients can invoke them. The exposed capability set, session model, authentication, transport, and approval behavior remain open.

## Open decisions

The following questions will be resolved with the corresponding product work:

- whether real trading uses per-action approval, approved batches, or bounded autonomy;
- how the QMT gateway communicates, authenticates, deploys, and recovers;
- what the unified data layer owns and how it models provenance and time;
- which responsibilities, if any, become separate agents;
- which capabilities Agent as MCP exposes and how it handles sessions, authentication, and approval;
- how confirmed directions map to later versions or milestones.

## Candidate themes

These themes can be evaluated when their usage trigger appears:

- **Structured files and tables**: reconsider when repeated investment workflows cannot be handled reliably through the workspace and existing command execution.
- **Deterministic analysis helpers**: reconsider when recurring calculations need a stable domain contract rather than ordinary Python or mature libraries.
- **Rich artifacts**: reconsider when users repeatedly need governed spreadsheet, chart, PDF, or report outputs beyond normal files.
- **External research integrations**: reconsider when the current MCP-based integration is insufficient for a concrete workflow.

## Maintenance rule

Update this document only when one of the following occurs:

1. code and tests change the completed feature set;
2. a product direction or open decision is resolved;
3. real usage supplies the trigger for a candidate theme.
