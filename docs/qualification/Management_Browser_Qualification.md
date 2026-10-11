# Management browser qualification

The production WebUI passed a real-browser smoke on 2026-10-11 at 10:28 Asia/Shanghai. Microsoft Edge (Chromium) loaded the bundled assets from an isolated Uvicorn instance at `127.0.0.1:18844`, with a fresh temporary InvestOrch root. The tested frontend bundle is `index-CKNnrqbO.js`, committed in `1ba65f8`.

The browser used actual application HTTP routes and disk persistence. Provider credentials and MCP endpoints were disposable fixtures. This run demonstrates UI, configuration, startup, and local-management behavior; actual model inference and Agent-led Skill workflows are qualified separately in the main acceptance report.

## Observed results

| Area | Real-browser observation |
| --- | --- |
| Setup | A fresh installation displayed Setup. Applying the primary model to all roles and saving started the Host in the same server process. The application then created a normal Session. |
| Connection test (W09) | A refused loopback endpoint produced a safe failure. A subsequent request reached a deliberately waiting local provider, after which Cancel returned the UI to an editable state. Retrying a refused endpoint produced another safe failure; the typed key and draft remained available. Cancellation here means canceling the browser's wait; the fixture connection was then closed explicitly. |
| Settings | Saving the timezone persisted `UTC`; Reset removed its override and restored `Asia/Shanghai`. Leaving with unsaved edits opened a dialog; Keep editing retained the draft and Discard and leave navigated away. |
| Models and credentials | A role-specific reasoning change required restart. Successful saves cleared replacement-key inputs and subsequent configuration reads did not contain the submitted key. A key-only save independently returned `requires_restart=true`. |
| Skills | Six built-ins were listed. A built-in action menu offered Fork and omitted Edit/Remove. A disabled Skill retained its content and resource previews. Re-enabling persisted and displayed restart guidance. |
| MCP | Creating, disabling, editing, and confirmed removal persisted through actual APIs. URL/header secrets were masked in responses. Replacement fields opened empty; saving an empty endpoint replacement preserved the endpoint. |
| System Status | Refresh retrieved the current local diagnostics. The WAL database check displayed UNKNOWN with repair guidance rather than claiming a check had run. |
| Responsive and keyboard | The six captured pages fit a 390-pixel viewport without document-level horizontal overflow. Settings categories scroll horizontally. On mobile, Escape closed the sidebar and focus returned automatically to its opener. Escape also closed the Skill action menu. |
| Browser runtime | No `pageerror` events occurred. |

## Screenshots

All captures were visually inspected at desktop 1440 × 1000 and mobile 390 × 844. They are viewport captures; content below the viewport remains scrollable. The capture waits for responsive layout paint after resizing.

| Page | Desktop | Mobile |
| --- | --- | --- |
| Setup | [Desktop](management-browser/setup-desktop.png) | [Mobile](management-browser/setup-mobile.png) |
| Skills | [Desktop](management-browser/skills-desktop.png) | [Mobile](management-browser/skills-mobile.png) |
| Disabled Skill detail | [Desktop](management-browser/skill-detail-disabled-desktop.png) | [Mobile](management-browser/skill-detail-disabled-mobile.png) |
| Settings: Models | [Desktop](management-browser/settings-models-desktop.png) | [Mobile](management-browser/settings-models-mobile.png) |
| Settings: Integrations | [Desktop](management-browser/settings-integrations-desktop.png) | [Mobile](management-browser/settings-integrations-mobile.png) |
| System Status | [Desktop](management-browser/system-desktop.png) | [Mobile](management-browser/system-mobile.png) |

The machine-readable result is [evidence.json](management-browser/evidence.json).

## Reproduction and build checks

Run [qualify-management.mjs](../../frontend/scripts/qualify-management.mjs) against a disposable fresh root. Set `WEB_BASE_URL` to its loopback URL, `OUTPUT_DIR` to an evidence directory, and `PLAYWRIGHT_MODULE` to an installed Playwright module if it is outside Node's normal module path. `BROWSER_CHANNEL` defaults to `msedge`. Start the backend with `NO_PROXY=127.0.0.1,localhost` and `no_proxy=127.0.0.1,localhost` so its cancellation probe reaches the local fixture. Do not target a user's working installation; the script intentionally modifies fixture configuration and creates a Session.

`npm ci` installed all 309 locked packages. `npm run typecheck`, `npm run lint`, and `npm run build` passed. npm skipped the optional `fsevents` install script under its lifecycle policy; production compilation succeeded. Vite reported the existing large-chunk advisory for the approximately 833 KB JavaScript bundle.

This browser run does not claim a successful external provider probe, MCP connectivity, exhaustive keyboard accessibility, or Windows/Linux native execution. Those outcomes require their own evidence.
