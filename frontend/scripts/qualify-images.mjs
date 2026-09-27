/* global process, Buffer, URL, document, DataTransfer, File, ClipboardEvent, DragEvent, console */

// Run against Vite with an installed Playwright module; no production dependency.
// PLAYWRIGHT_MODULE=/absolute/path/playwright/index.mjs node scripts/qualify-images.mjs
import assert from 'node:assert/strict'
import { mkdir } from 'node:fs/promises'
const outputDir = process.env.OUTPUT_DIR || '/tmp/investorch-multimodal-browser'
await mkdir(outputDir, { recursive: true })
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true, args: ['--no-proxy-server'] })
const page = await browser.newPage()
const base = process.env.WEB_BASE_URL || 'http://127.0.0.1:5174'
const png = Buffer.from(await page.evaluate(() => {
  const canvas = document.createElement('canvas')
  canvas.width = 160
  canvas.height = 96
  const context = canvas.getContext('2d')
  context.fillStyle = '#e8efff'
  context.fillRect(0, 0, 160, 96)
  context.fillStyle = '#3264bd'
  context.fillRect(20, 45, 25, 35)
  context.fillRect(60, 25, 25, 55)
  context.fillRect(100, 10, 25, 70)
  return canvas.toDataURL('image/png').split(',')[1]
}), 'base64')
const image = { image_url: `data:image/png;base64,${png.toString('base64')}`, detail: 'auto', filename: 'chart.png', media_type: 'image/png' }
const now = '2026-09-26T10:00:00Z'
const session = (id) => ({ session_id: id, title: `Session ${id}`, branch_from_session_id: null, archived_at: null, created_at: now, updated_at: now })
const runtime = (id) => ({ kind: 'runtime_state', session_id: id, run_id: null, run_phase: null, run_started_at: null, active_follow_up_behavior: null, queued_count: 0, queue_paused: false, pending_steer_count: 0, todos: [] })
const defaults = { reasoning_effort: 'medium', permission_mode: 'manual', follow_up_behavior: 'steer' }
const usage = { requests: 0, input_tokens: 0, cached_input_tokens: 0, cache_write_input_tokens: 0, output_tokens: 0, reasoning_output_tokens: 0, total_tokens: 0, last_request_total_tokens: 0 }
const presentation = { usage, main_context_tokens: null, last_todo_run_id: null, last_todos: [] }
const imageConfig = { max_images_per_input: 3, max_image_bytes: png.length + 20, max_total_image_bytes: png.length * 2 + 20, default_detail: 'auto', accepted_input_mime_types: ['image/jpeg', 'image/png', 'image/gif', 'image/webp'], renderable_mime_types: ['image/jpeg', 'image/png', 'image/gif', 'image/webp', 'image/svg+xml'] }
let failSend = true
let sent = null
let history = []
let queue = []
let socket
await page.routeWebSocket('**/ws', (connection) => { socket = connection })
const errors = []
const remoteRequests = []
page.on('console', (message) => { if (message.type() === 'error') console.error(message.text()) })
page.on('pageerror', (error) => errors.push(error.message))
await page.route('https://images.example.test/**', async (route) => { remoteRequests.push(route.request()); await route.fulfill({ contentType: 'image/png', body: png }) })
await page.route('**/api/**', async (route) => {
  const path = new URL(route.request().url()).pathname
  if (!path.startsWith('/api/')) return route.continue()
  const id = path.split('/')[3]
  let value
  if (path === '/api/bootstrap') value = { version: 'test', initial_session_id: 'A', agent_name: 'Agent', context_window_tokens: 10000, defaults, image_config: imageConfig, web_config: { history_page_size: 100, websocket_reconnect_base_delay_ms: 60000, websocket_reconnect_max_delay_ms: 60000, max_notices: 10, composer_max_height_px: 200, unused_session_discard_delay_ms: 60000, run_timer_interval_ms: 1000 }, sessions: [session('A'), session('B')], runtime: runtime('A'), presentation, pending_approvals: [] }
  else if (path === '/api/sessions') value = { sessions: [session('A'), session('B')] }
  else if (path === '/api/defaults') value = defaults
  else if (path === '/api/portfolios/P') value = { portfolio: { portfolio_id: 'P', name: 'Demo', status: 'ACTIVE', base_currency: 'USD', strategy_binding: null, description: null, created_at: now, updated_at: now }, state: { portfolio_id: 'P', cash: {}, holdings: [] } }
  else if (path === '/api/portfolios/P/ledger') value = { portfolio_id: 'P', entries: [], returned: 0, total: 0, has_older: false }
  else if (path === '/api/portfolios/P/ask') {
    sent = route.request().postDataJSON()
    if (failSend) return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'Try again', details: {} } } })
    value = { session: session('A'), run_id: 'r', started: true }
  }
  else if (path.endsWith('/state')) value = { session: session(id), runtime: { ...runtime(id), queued_count: queue.length }, presentation, pending_approvals: [], queue }
  else if (path.endsWith('/history')) value = { records: history, has_older: false, oldest_seq: history[0]?.seq ?? null, newest_seq: history.at(-1)?.seq ?? null }
  else if (path.endsWith('/messages')) {
    sent = route.request().postDataJSON()
    if (failSend) return route.fulfill({ status: 503, json: { error: { code: 'unavailable', message: 'Try again', details: {} } } })
    value = { session_id: id, disposition: 'run_started', run_id: 'run', follow_up_id: null }
  } else if (path.endsWith('/related-portfolios') || path === '/api/portfolios') value = { portfolios: [] }
  else if (path.endsWith('/discard-unused')) value = { discarded: false }
  else value = { session: session(id) }
  await route.fulfill({ json: value })
})
const input = page.getByLabel('Select images')
const textarea = page.getByRole('textbox', { name: 'Message InvestOrch Agent' })
const send = page.getByRole('button', { name: 'Send', exact: true })
const remove = page.getByRole('button', { name: 'Remove chart.png', exact: true })
const file = { name: 'chart.png', mimeType: 'image/png', buffer: png }
try {
  await page.goto(`${base}/c/A`)
  await textarea.waitFor()
  await input.setInputFiles(file)
  await remove.waitFor()
  assert.equal(await send.isEnabled(), true, 'image-only input can send')
  await page.waitForFunction(() => [...document.querySelectorAll('img')].some((image) => image.complete && image.naturalWidth === 160))
  await page.screenshot({ path: `${outputDir}/image-composer.png` })
  await textarea.fill('/new')
  await send.click()
  await page.getByText('This action is available in the Web interface.').waitFor()
  assert.equal(await remove.count(), 1, 'native slash actions retain images')
  await textarea.fill('explain')
  await page.getByRole('button', { name: 'Session B', exact: false }).first().click()
  await page.waitForURL('**/c/B')
  await page.getByRole('heading', { name: 'Session B', exact: true }).waitFor()
  await textarea.waitFor()
  assert.equal(await input.count(), 1)
  assert.equal(await remove.count(), 0, 'other sessions have independent drafts')
  await page.getByRole('button', { name: 'Session A', exact: false }).first().click()
  await page.waitForURL('**/c/A')
  await page.getByRole('heading', { name: 'Session A', exact: true }).waitFor()
  await remove.waitFor()
  assert.equal(await textarea.inputValue(), 'explain')
  await send.click()
  await page.getByRole('alert').filter({ hasText: 'Try again' }).first().waitFor()
  assert.equal(await remove.count(), 1, 'failed send retains images')
  assert.equal(sent.images[0].image_url, image.image_url)
  failSend = false
  await textarea.fill('')
  await send.click()
  await page.waitForFunction(() => !document.querySelector('button[aria-label="Remove chart.png"]'))
  assert.equal(sent.text, '')
  assert.equal(await page.getByRole('button', { name: 'Enlarge chart.png' }).count(), 1, 'pending message retains image after draft clears')
  // Paste and drop through real browser events, including native FileReader conversion.
  for (const kind of ['paste', 'drop']) {
    await textarea.evaluate((el, { kind, bytes }) => {
      const transfer = new DataTransfer()
      transfer.items.add(new File([new Uint8Array(bytes)], 'chart.png', { type: 'image/png' }))
      el.dispatchEvent(kind === 'paste' ? new ClipboardEvent('paste', { clipboardData: transfer, bubbles: true, cancelable: true }) : new DragEvent('drop', { dataTransfer: transfer, bubbles: true, cancelable: true }))
    }, { kind, bytes: [...png] })
    await remove.waitFor()
    await remove.click()
  }
  await input.setInputFiles([file, file])
  await page.waitForFunction(() => document.querySelectorAll('button[aria-label="Remove chart.png"]').length === 2)
  await remove.first().click()
  assert.equal(await remove.count(), 1, 'removes one of multiple images')
  await remove.click()
  for (const [files, message] of [
    [[file, file, file, file], 'at most 3'],
    [[{ ...file, buffer: Buffer.alloc(imageConfig.max_image_bytes + 1) }], 'per-image limit'],
    [[file, file, file], 'total'],
    [[{ name: 'vector.svg', mimeType: 'image/svg+xml', buffer: Buffer.from('<svg/>') }], 'cannot be sent'],
  ]) {
    await input.setInputFiles(files)
    await page.getByRole('alert').filter({ hasText: message }).waitFor()
    assert.equal(await remove.count(), 0)
  }
  // Reload history verifies real app projection and shared renderer for each content role.
  const record = (seq, type, rest) => ({ seq, timestamp: now, type, ...rest })
  history = [record(1, 'user_message', { text: '', images: [image] }), record(2, 'user_steer', { text: 'steer', run_id: 'r', images: [image] }), record(3, 'tool_called', { name: 'explore', arguments: '{}' }), record(4, 'tool_output', { output: '', images: [image] }), record(5, 'assistant_message', { text: '![external](https://images.example.test/markdown.png)', images: [{ ...image, image_url: 'https://images.example.test/remote.png' }, { ...image, image_url: 'http://images.example.test/blocked.png' }, { ...image, image_url: 'javascript:alert(1)' }, { ...image, image_url: 'file:///tmp/image.png' }, { ...image, image_url: 'blob:https://images.example.test/test' }, { ...image, image_url: 'data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIzMjAiIGhlaWdodD0iMTYwIiB2aWV3Qm94PSIwIDAgMzIwIDE2MCI+PHJlY3Qgd2lkdGg9IjMyMCIgaGVpZ2h0PSIxNjAiIGZpbGw9IiNlOGVmZmYiLz48Y2lyY2xlIGN4PSI4MCIgY3k9IjgwIiByPSI1MCIgZmlsbD0iIzMyNjRiZCIvPjx0ZXh0IHg9IjE1MCIgeT0iOTAiIGZvbnQtc2l6ZT0iMjQiIGZpbGw9IiMyMjIiPlNWRyBpbWFnZTwvdGV4dD48L3N2Zz4=', filename: 'vector.svg' }] })]
  queue = [{ queue_id: 'q', session_id: 'A', text: '', images: [image], created_at: now }]
  await page.reload()
  await textarea.waitFor()
  await page.getByText('External image · images.example.test').first().waitFor()
  assert.equal(remoteRequests.length, 0, 'history and Markdown never auto-load remote images')
  assert.equal(await page.locator('img[src^="http"]').count(), 0)
  assert.equal(await page.getByText('Unsupported image source', { exact: true }).count(), 4)
  await page.getByRole('button', { name: 'Calling explore…', exact: true }).first().click()
  await page.getByRole('button', { name: 'Calling explore…', exact: true }).last().click()
  await page.getByRole('button', { name: 'Manage', exact: true }).click()
  assert.equal(await page.getByRole('button', { name: 'Enlarge chart.png' }).count(), 4, 'user, steer, tool and queue images render')
  await page.getByRole('button', { name: 'Enlarge vector.svg' }).click()
  await page.getByRole('dialog').waitFor()
  assert.equal(await page.getByRole('dialog').locator('img').count(), 1, 'SVG is an img inside lightbox')
  await page.waitForFunction(() => { const image = document.querySelector('[role=dialog] img'); return image?.complete && image.naturalWidth === 320 })
  await page.screenshot({ path: `${outputDir}/image-lightbox.png` })
  await page.keyboard.press('Escape')
  const remoteResponse = page.waitForResponse('https://images.example.test/remote.png')
  await page.getByRole('button', { name: 'Load image' }).last().click()
  await page.waitForFunction(() => document.querySelector('img[src="https://images.example.test/remote.png"]'))
  await page.locator('img[src="https://images.example.test/remote.png"]').scrollIntoViewIfNeeded()
  await remoteResponse
  await page.waitForFunction(() => { const image = document.querySelector('img[src="https://images.example.test/remote.png"]'); return image?.complete && image.naturalWidth === 160 })
  assert.equal(remoteRequests.length, 1)
  assert.equal(remoteRequests[0].headers().referer, undefined)
  assert.equal(await page.locator('img[src="https://images.example.test/remote.png"]').getAttribute('referrerpolicy'), 'no-referrer')
  const beforeLive = await page.getByRole('button', { name: 'Enlarge chart.png' }).count()
  socket.send(JSON.stringify({ kind: 'output', session_id: 'A', run_id: 'r', journal_seq: 6, event: { type: 'assistant_message', text: '', images: [image] } }))
  await page.waitForFunction((count) => document.querySelectorAll('button[aria-label="Enlarge chart.png"]').length === count, beforeLive + 1)
  socket.send(JSON.stringify({ kind: 'follow_up', event_kind: 'steer_submitted', session_id: 'A', run_id: 'r', source_run_id: 'r', follow_up_id: 'f', journal_seq: 7, text: '', images: [image] }))
  await page.waitForFunction((count) => document.querySelectorAll('button[aria-label="Enlarge chart.png"]').length === count, beforeLive + 2)
  socket.send(JSON.stringify({ kind: 'output', session_id: 'A', run_id: 'r', journal_seq: 8, event: { type: 'tool_output', output: '', images: [image] } }))
  await page.getByRole('button', { name: 'Unmatched tool output', exact: true }).first().click()
  await page.getByRole('button', { name: 'Unmatched tool output', exact: true }).last().click()
  assert.equal(await page.getByRole('button', { name: 'Enlarge chart.png' }).count(), beforeLive + 3)
  await page.screenshot({ path: `${outputDir}/image-timeline.png` })
  socket.send(JSON.stringify({ kind: 'follow_up', event_kind: 'queue_promoted', session_id: 'A', run_id: 'r2', source_run_id: 'r', follow_up_id: 'q', journal_seq: 9, text: '', images: [image] }))
  await page.waitForFunction((count) => document.querySelectorAll('button[aria-label="Enlarge chart.png"]').length === count, beforeLive + 4)
  await page.goto(`${base}/portfolios/P`)
  await page.getByRole('button', { name: 'Ask Agent', exact: true }).click()
  await input.setInputFiles(file)
  await remove.waitFor()
  failSend = true
  await send.click()
  await page.getByRole('alert').filter({ hasText: 'Try again' }).first().waitFor()
  assert.equal(await remove.count(), 1, 'Portfolio Ask failure retains image')
  const requestId = sent.request_id
  assert.equal(sent.text, '')
  assert.equal(sent.images[0].image_url, image.image_url)
  failSend = false
  await send.click()
  await page.waitForURL('**/c/A')
  assert.equal(sent.request_id, requestId, 'Portfolio retries reuse request identity')
  assert.deepEqual(errors, [])
  console.log('PASS: browser image composer, drafts, pending, limits, history, live WebSocket images, Portfolio Ask, tool/steer/queue, SVG/lightbox, remote network gate and referrer behavior')
} catch (error) { console.error(errors, await page.locator('body').innerText()); throw error } finally { await browser.close() }
