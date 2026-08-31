# WebUI design QA

## Comparison inputs

- Reference screenshot: `/Users/huangyicong/Downloads/截屏2026-08-31 10.48.30.png`
- Implementation screenshot: `/Users/huangyicong/.codex/visualizations/2026/08/30/01a05264-d2aa-7040-9ba3-12c0e03eaa3b/qmt-webui-final.png`
- Combined comparison: `/Users/huangyicong/.codex/visualizations/2026/08/30/01a05264-d2aa-7040-9ba3-12c0e03eaa3b/qmt-webui-comparison.png`

The supplied reference is a 2384 x 4004 Codex desktop capture. The implementation was verified in the available in-app browser viewport at 1280 x 720. Because the source is an annotated tall desktop capture rather than this product's layout specification, the comparison uses the analogous visible interaction state instead of asserting pixel equivalence. Both full images were placed in the combined comparison before judging the result.

## Visible comparison

- Permission and reasoning effort are in the Composer footer, beside the context meter.
- The global Settings surface contains Follow-ups only; run permission and effort are not presented as global settings.
- Activity is collapsed by default and keeps nested steps independently collapsed.
- Hovering or keyboard-focusing a session row reveals both archive and delete actions without clipping the title.
- The timeline shows a local-day marker, user and Agent timestamps, and the completed Agent work duration.
- The implementation preserves the existing QMT WebUI spacing, typography, neutral palette, icon library, and responsive sidebar/timeline structure.

## Interaction and runtime checks

- Permission and effort change process-memory defaults for future Runs, matching `/permission` and `/effort`; they do not persist per session and do not mutate an active Run.
- A real defaults-snapshot smoke set the future Run defaults to `manual` and `low`, started a Run, then immediately restored `review` and `max`. The active Run still produced the manual approval captured at start, `load_config()` remained unchanged on disk, and the temporary session was deleted afterward.
- A temporary session was archived, restored, and deleted through the real UI. A separate confirmed-delete smoke verified the destructive path without changing existing user sessions.
- A real Agent Run executed one shell tool call. The live row displayed `Working ...` and the completed row displayed `Worked 7s · 17:13–17:13`.
- A full page reload restored the completed duration from the journal.
- Browser console output after the final interaction run was empty: `[]`.

## Comparison history

1. The first sidebar implementation clipped hover actions because the Radix scroll viewport content wrapper used max-content table sizing.
2. The viewport wrapper was constrained to the sidebar width; archive and delete actions then remained visible on pointer hover and keyboard focus.
3. Activity group and child disclosure states were rechecked after reload and remained collapsed by default.
4. The final combined comparison confirmed the five requested behaviors in one representative implementation state; no additional visual mismatch blocked acceptance.

final result: passed
