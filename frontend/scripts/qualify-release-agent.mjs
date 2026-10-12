/* global process, console, URL, document, window */

// Run only against a disposable, real-provider production Web instance.
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { setTimeout as wait } from 'node:timers/promises'

const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.WEB_BASE_URL
const root = process.env.QA_ROOT
assert(base && new URL(base).hostname === '127.0.0.1')
assert(root && /^\/(?:private\/)?tmp\/investorch-release-browser-[^/]+\/real\/\.investorch$/.test(root))
const outputDir = process.env.OUTPUT_DIR
assert(outputDir)
await mkdir(outputDir, { recursive: true })
const browser = await chromium.launch({ channel: 'msedge', headless: true, args: ['--no-proxy-server'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
const checks = []
page.on('pageerror', (error) => errors.push(error.message))
const get = async (path) => {
  const response = await page.request.get(base + path)
  assert(response.ok(), `${path}: ${response.status()}`)
  return response.json()
}
const post = async (path, data = {}) => {
  const response = await page.request.post(base + path, { data, headers: { Origin: base } })
  assert(response.ok(), `${path}: ${response.status()}`)
  return response.json()
}
const finish = async (id, allow = () => false) => {
  const deadline = Date.now() + 240000
  while (Date.now() < deadline) {
    const state = await get(`/api/sessions/${id}/state`)
    for (const approval of state.pending_approvals) {
      assert(allow(approval), `Unexpected approval for ${approval.tool_name}`)
      await page.getByRole('button', { name: 'Approve', exact: true }).click()
    }
    const history = await get(`/api/sessions/${id}/history?limit=1000`)
    const ended = history.records.filter((record) => record.type === 'run_ended').at(-1)
    if (!state.runtime.run_id && ended) {
      assert.equal(ended.status, 'completed')
      return history.records
    }
    await wait(250)
  }
  await post(`/api/sessions/${id}/stop`)
  throw new Error('Real-provider Web Run timed out')
}
const send = async (text, allow) => {
  const response = page.waitForResponse((response) => response.request().method() === 'POST' && /\/messages$/.test(new URL(response.url()).pathname))
  await page.getByRole('textbox', { name: 'Message InvestOrch Agent' }).fill(text)
  await page.getByRole('button', { name: 'Send', exact: true }).click()
  const sent = await response
  assert(sent.ok())
  return finish((await sent.json()).session_id, allow)
}
const assistantText = (records) => records.filter((record) => record.type === 'assistant_message').map((record) => record.text || '').join('\n')
const shapeAnswers = []
const assertShapes = (records) => {
  const answer = records.filter((record) => record.type === 'assistant_message').at(-1).text.trim().toLowerCase()
  const triangle = answer.search(/triangle|三角形/)
  const circle = answer.search(/circle|圆形/)
  const square = answer.search(/square|正方形/)
  assert(triangle >= 0 && circle > triangle && square > circle, `Unexpected shape recognition: ${answer}`)
  shapeAnswers.push(answer)
}

try {
  await page.goto(base)
  const config = await get('/api/config')
  assert.equal(config.values.paths.root, root, 'Never mutate a real user instance')
  assert.equal((await get('/api/bootstrap')).version, '0.2.0')
  await page.getByRole('button', { name: 'New', exact: true }).click()
  await page.waitForURL('**/c/*')
  const id = new URL(page.url()).pathname.split('/').at(-1)
  let records = await send('Use calculate to evaluate 6 * 7, then reply exactly RELEASE_CALC_42. Use no other tools.')
  assert(records.some((record) => record.type === 'tool_called' && record.name === 'calculate'))
  assert(assistantText(records).includes('RELEASE_CALC_42'))
  checks.push('Production wheel, real HTTP/WebSocket, browser composer and real model calculate completed.')

  records = await send('Use exec_command with command exactly `printf RELEASE_QA_OK`. Run it in the temporary Workspace, foreground, timeout 10 seconds. I authorize this exact disposable qualification command. After approval and execution, report its stdout. Use no other tools.', (approval) => {
    const args = JSON.parse(approval.arguments)
    return approval.tool_name === 'exec_command' && args.command === 'printf RELEASE_QA_OK' && args.background === false && args.timeout_seconds === 10
  })
  assert(records.some((record) => record.type === 'approval' && record.tool_name === 'exec_command' && record.approved === true))
  assert(records.some((record) => record.type === 'tool_output' && JSON.stringify(record.output).includes('RELEASE_QA_OK')))
  assert(assistantText(records).includes('RELEASE_QA_OK'))
  checks.push('Real model command paused for manual UI approval, executed in macOS sandbox, and resumed.')

  const png = await page.evaluate(() => {
    const canvas = document.createElement('canvas')
    canvas.width = 600
    canvas.height = 200
    const context = canvas.getContext('2d')
    context.fillStyle = '#ffffff'
    context.fillRect(0, 0, canvas.width, canvas.height)
    context.fillStyle = '#202020'
    context.beginPath()
    context.moveTo(100, 40)
    context.lineTo(40, 160)
    context.lineTo(160, 160)
    context.closePath()
    context.fill()
    context.beginPath()
    context.arc(300, 100, 60, 0, Math.PI * 2)
    context.fill()
    context.fillRect(440, 40, 120, 120)
    return canvas.toDataURL('image/png').split(',')[1]
  })
  await page.getByLabel('Select images').setInputFiles({ name: 'release-shapes.png', mimeType: 'image/png', buffer: require('node:buffer').Buffer.from(png, 'base64') })
  records = await send('Identify the geometric shapes in the attached image from left to right. Reply with only their names in English. Use no tools.')
  assertShapes(records)
  assert(records.some((record) => record.type === 'user_message' && record.images?.length === 1))
  await page.getByRole('button', { name: 'Enlarge release-shapes.png' }).waitFor()
  await page.screenshot({ path: `${outputDir}/real-image-desktop.png` })
  checks.push('Actual browser file selection reached the real vision model; triangle, circle and square were identified in left-to-right order and persisted with inline history.')

  const fork = await post(`/api/sessions/${id}/fork`)
  const forkId = fork.session.session_id
  const forkHistory = await get(`/api/sessions/${forkId}/history?limit=1000`)
  assert.equal(forkHistory.records.filter((record) => record.type === 'user_message').length, 3)
  await post(`/api/sessions/${forkId}/archive`)
  assert((await get('/api/sessions/archived')).sessions.some((session) => session.session_id === forkId))
  await post(`/api/sessions/${forkId}/unarchive`)
  assert((await get('/api/sessions')).sessions.some((session) => session.session_id === forkId))
  const compact = await post(`/api/sessions/${id}/compact`)
  assert.equal(compact.changed, true)
  records = await send('What geometric shapes were in the image just before compaction, from left to right? Reply with only their names in English. Use no tools.')
  assertShapes(records)
  checks.push('Durable image history survived stable-head fork, archive/restore and real-model compaction/continuation.')

  await page.setViewportSize({ width: 390, height: 844 })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth))
  await page.screenshot({ path: `${outputDir}/real-image-mobile.png` })
  await page.reload()
  await page.getByRole('button', { name: 'Enlarge release-shapes.png' }).waitFor()
  assert.deepEqual(errors, [])
  await writeFile(`${outputDir}/evidence.json`, JSON.stringify({ status: 'passed', version: '0.2.0', simulatedProvider: false, realTransport: 'production CLI HTTP/WebSocket + Edge + configured model', checks, shapeAnswers, browserErrors: errors, sessionId: id, forkId }, null, 2))
  console.log(JSON.stringify({ status: 'passed', checks }))
} catch (error) {
  await page.screenshot({ path: `${outputDir}/failure.png`, fullPage: true })
  throw error
} finally {
  await browser.close()
}
