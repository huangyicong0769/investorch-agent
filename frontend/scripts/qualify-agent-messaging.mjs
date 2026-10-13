/* global process, URL, navigator, console */

// Exercise the production UI with controlled HTTP/WebSocket responses, without a model or real user data.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node scripts/qualify-agent-messaging.mjs
import assert from 'node:assert/strict'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true, args: ['--no-proxy-server'] })
const page = await browser.newPage({ permissions: ['clipboard-read', 'clipboard-write'] })
page.setDefaultTimeout(10000)
const base = process.env.WEB_BASE_URL || 'http://127.0.0.1:5174'
const senderId = '25ee5a88-e64e-452d-90c7-77c99dfb001a'
const origin = { kind: 'agent', sender_session_id: senderId, sender_run_id: 'sender-run' }
const timestamp = '2026-10-13T08:00:00Z'
const image = { image_url: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', detail: 'auto', filename: 'message.png', media_type: 'image/png' }
const session = (id) => ({ session_id: id, title: `Session ${id}`, branch_from_session_id: null, archived_at: id === senderId ? timestamp : null, created_at: timestamp, updated_at: timestamp })
const runtime = (id) => ({ kind: 'runtime_state', session_id: id, run_id: null, run_phase: null, run_started_at: null, active_follow_up_behavior: null, queued_count: id === 'B' ? 1 : 0, queue_paused: false, pending_steer_count: 0, todos: [] })
const defaults = { reasoning_effort: 'medium', permission_mode: 'manual', follow_up_behavior: 'steer' }
const usage = { requests: 0, input_tokens: 0, cached_input_tokens: 0, cache_write_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, total_tokens: 0, last_request_total_tokens: 0 }
const presentation = { usage, main_context_tokens: null, last_todo_run_id: null, last_todos: [] }
const imageConfig = { max_images_per_input: 4, max_image_bytes: 1000, max_total_image_bytes: 4000, default_detail: 'auto', accepted_input_mime_types: ['image/png'], renderable_mime_types: ['image/png'] }
const records = [
  { type: 'user_message', seq: 1, timestamp, text: 'Original user input' },
  { type: 'user_message', seq: 2, timestamp, text: 'Agent message body', images: [image], origin },
  { type: 'user_steer', seq: 3, timestamp, run_id: 'target-run', text: 'Agent steer body', origin },
]
const history = new Map([['B', records]])
const queue = [{ queue_id: 'queued-agent', session_id: 'B', text: 'Queued agent body', images: [image], created_at: timestamp, origin }]
const errors = []
page.on('pageerror', (error) => errors.push(error.message))
page.on('console', (message) => { if (message.type() === 'error') console.error(message.text()) })
let socket
await page.routeWebSocket('**/ws', (connection) => { socket = connection })
await page.route('**/api/**', async (route) => {
  const path = new URL(route.request().url()).pathname
  if (!path.startsWith('/api/')) return route.continue()
  const id = decodeURIComponent(path.split('/')[3] || '')
  let value
  if (path === '/api/setup/state') value = { status: 'ready', missing: [] }
  else if (path === '/api/bootstrap') value = { version: 'test', initial_session_id: 'B', agent_name: 'Agent', context_window_tokens: 10000, defaults, image_config: imageConfig, web_config: { history_page_size: 100, websocket_reconnect_base_delay_ms: 60000, websocket_reconnect_max_delay_ms: 60000, max_notices: 10, composer_max_height_px: 200, unused_session_discard_delay_ms: 60000, run_timer_interval_ms: 1000 }, sessions: [session('B'), session('C')], runtime: runtime('B'), presentation, pending_approvals: [] }
  else if (path === '/api/sessions') value = { sessions: [session('B'), session('C')] }
  else if (path === '/api/defaults') value = defaults
  else if (path.endsWith('/state')) value = { session: session(id), runtime: runtime(id), presentation, pending_approvals: [], queue: id === 'B' ? queue : [] }
  else if (path.endsWith('/history')) {
    const items = history.get(id) || []
    value = { records: items, has_older: false, oldest_seq: items[0]?.seq ?? null, newest_seq: items.at(-1)?.seq ?? null }
  } else if (path.endsWith('/related-portfolios') || path === '/api/portfolios') value = { portfolios: [] }
  else if (path.endsWith('/discard-unused')) value = { discarded: false }
  else value = { session: session(id) }
  await route.fulfill({ json: value })
})

try {
  await page.goto(`${base}/c/B`)
  await page.getByText('Original user input', { exact: true }).waitFor()
  assert.match(await page.locator('article[data-seq="1"]').innerText(), /^You ·/)
  for (const seq of [2, 3]) {
    const row = page.locator(`article[data-seq="${seq}"]`)
    await row.getByRole('link', { name: 'Agent · 25ee5a88…001a', exact: true }).waitFor()
    assert.equal(await row.getByRole('link').getAttribute('title'), senderId)
    assert.equal(await row.getByRole('link').getAttribute('href'), `/c/${senderId}`)
  }
  assert.match(await page.locator('article[data-seq="3"]').innerText(), /Steer/)
  assert.equal(await page.locator('article[data-seq="2"]').getByText('Agent message body', { exact: true }).count(), 1)
  await page.locator('article[data-seq="2"] img').waitFor()
  assert.equal(await page.locator('article[data-seq="2"] img').getAttribute('src'), image.image_url)
  await page.locator('article[data-seq="2"]').getByRole('button', { name: 'Copy', exact: true }).click()
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), senderId)
  await page.getByRole('button', { name: 'Manage', exact: true }).click()
  const queued = page.getByRole('listitem').filter({ hasText: 'Queued agent body' })
  await queued.getByRole('link', { name: 'Agent · 25ee5a88…001a', exact: true }).waitFor()
  records.push({ type: 'user_message', seq: 4, timestamp, text: 'Committed agent input', images: [image], origin })
  socket.send(JSON.stringify({ kind: 'user_input_committed', session_id: 'B', journal_seq: 4 }))
  await page.getByText('Committed agent input', { exact: true }).waitFor()
  await page.locator('article[data-seq="4"]').getByRole('link', { name: 'Agent · 25ee5a88…001a', exact: true }).waitFor()
  socket.send(JSON.stringify({ kind: 'follow_up', event_kind: 'steer_submitted', session_id: 'B', run_id: 'target-run', source_run_id: 'target-run', follow_up_id: 'live-steer', journal_seq: 5, text: 'Live agent steer', images: [image], origin }))
  await page.getByText('Live agent steer', { exact: true }).waitFor()
  await page.locator('article[data-seq="5"]').getByRole('link', { name: 'Agent · 25ee5a88…001a', exact: true }).waitFor()
  records.push({ type: 'user_steer', seq: 5, timestamp, run_id: 'target-run', text: 'Live agent steer', images: [image], origin })
  records.push({ type: 'user_message', seq: 6, timestamp, text: 'Promoted agent queue', images: [image], origin })
  socket.send(JSON.stringify({ kind: 'follow_up', event_kind: 'queue_promoted', session_id: 'B', run_id: 'next-run', source_run_id: 'target-run', follow_up_id: 'queued-agent', journal_seq: 6, text: 'Promoted agent queue', images: [image], origin }))
  await page.getByText('Promoted agent queue', { exact: true }).waitFor()
  await page.locator('article[data-seq="6"]').getByRole('link', { name: 'Agent · 25ee5a88…001a', exact: true }).waitFor()
  await page.reload()
  await page.getByText('Live agent steer', { exact: true }).waitFor()
  assert.equal(await page.getByText('Committed agent input', { exact: true }).count(), 1)
  assert.equal(await page.getByText('Live agent steer', { exact: true }).count(), 1)
  assert.equal(await page.getByText('Promoted agent queue', { exact: true }).count(), 1, 'history reload has no duplicate live messages')
  history.set('C', [{ type: 'user_message', seq: 1, timestamp, text: 'Agent input for Session C', origin }])
  socket.send(JSON.stringify({ kind: 'user_input_committed', session_id: 'C', journal_seq: 1 }))
  await page.getByRole('button', { name: 'Session C', exact: false }).first().click()
  await page.getByText('Agent input for Session C', { exact: true }).waitFor()
  assert.equal(await page.getByText('Committed agent input', { exact: true }).count(), 0, 'inputs remain owned by their target Session')
  await page.getByRole('button', { name: 'Session B', exact: false }).first().click()
  await page.getByText('Committed agent input', { exact: true }).waitFor()
  await page.locator('article[data-seq="2"]').getByRole('link').click()
  await page.waitForURL(`**/c/${senderId}`)
  await page.getByRole('heading', { name: `Session ${senderId}`, exact: true }).waitFor()
  assert.equal(await page.getByRole('textbox', { name: 'Message InvestOrch Agent' }).isDisabled(), true, 'archived source navigation retains its read-only state')
  assert.deepEqual(errors, [], 'no browser runtime errors')
  console.log('PASS: user/agent history, committed/live Steer/Queue and reload agree without duplicates; source IDs copy/navigate to archived Sessions; selection keeps target ownership')
} catch (error) {
  console.error(errors, await page.locator('body').innerText())
  throw error
} finally {
  await browser.close()
}
