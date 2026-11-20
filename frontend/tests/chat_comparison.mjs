import { createHash } from 'node:crypto'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'
import { chromium, expect } from '@playwright/test'

// The default run uses the offline classroom fixture. --live opts into provider calls.
const live = process.argv.includes('--live')
const base = live ? 'http://127.0.0.1:5173' : 'http://127.0.0.1:18862'
const output = process.env.CHART_EVIDENCE_DIR
const [start, firstEnd, laterEnd] = live
  ? ['2026-11-17T18:45:00Z', '2026-11-17T19:18:45Z', '2026-11-17T19:30:45Z']
  : ['2026-11-09T14:30:00Z', '2026-11-09T14:33:10Z', '2026-11-09T14:35:30Z']
const browser = await chromium.launch({channel:'chrome', headless:true})
try {
  const page = await browser.newPage({viewport:{width:1440,height:1100}, deviceScaleFactor:1.5})
  const chat = page.getByRole('region', {name:'Chart chat'})
  const last = chat.locator('.chat-turn').last()
  if (output) await mkdir(output, {recursive:true})

  async function fixture(path) {
    if (!live) expect((await page.request.post('http://127.0.0.1:18861/__fixture/' + path)).ok()).toBe(true)
  }
  async function loadRange(end) {
    await page.getByRole('button',{name:'Change time range'}).click()
    await page.getByLabel('Start UTC:').fill(start)
    await page.getByLabel('End UTC:').fill(end)
    const [response] = await Promise.all([
      page.waitForResponse(r => r.url().includes('/api/chart-snapshot?')),
      page.getByRole('button',{name:'Load snapshot'}).click(),
    ])
    expect(response.ok()).toBe(true)
    const data = await response.json()
    expect(Date.parse(data.as_of)).toBe(Date.parse(end))
    await expect(page.getByText(/^Snapshot as of /)).toContainText(end.slice(11,19))
    await page.getByRole('button',{name:'Hide time range'}).click()
    return data.records.filter(record => record.ended).at(-1).candle.timestamp
  }
  async function send(question, expectedTool, screenshot) {
    await chat.getByRole('textbox').fill(question)
    const [response] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/chart-agent'), {timeout:120000}),
      chat.getByRole('button',{name:'Send', exact:true}).click(),
    ])
    const request = response.request().postDataJSON()
    const reply = await response.json()
    expect(reply.status, reply.detail || reply.message).toBe('completed')
    await expect(last.locator('.chat-question')).toHaveText(question)
    await expect(last.locator('.chat-reply')).toHaveText(reply.message)
    expect(reply.tools.map(call => call.name)).toEqual([expectedTool])
    const [call] = reply.tools
    await last.locator('summary').click()
    expect(JSON.parse(await last.locator('pre').innerText())).toEqual({arguments:call.arguments, result:call.result})
    if (output && screenshot) {
      await last.evaluate(turn => {
        const log = turn.parentElement
        log.scrollTop += turn.getBoundingClientRect().top - log.getBoundingClientRect().top
      })
      await chat.screenshot({path:join(output, screenshot)})
    }
    await last.locator('summary').click()
    console.log(expectedTool, reply.message)
    return {request, result:call.result, message:reply.message}
  }
  async function checkChart(turn, stamp, screenshot) {
    const {result, request, message} = turn
    expect(result.status).toBe('ok')
    expect(result.context.last_completed_candle).toBe(stamp)
    expect(result.context.mode).toBe('replay')
    const choice = result.answer.choice.toUpperCase()
    const value = result.answer.probability?.toFixed(2) ?? 'unavailable'
    const label = choice + ' (probability: ' + value + ')'
    expect(message).toContain(stamp + ' · ' + label)
    const card = page.locator('.lesson-result')
    await expect(card.locator('.lesson-choice')).toHaveText(label)
    await expect(card).toContainText(stamp.slice(11,19))
    await expect(page.getByAltText('Chart image sent for analysis')).toHaveAttribute('src', result.image_url)
    const png = Buffer.from(request.snapshot.image_url.split(',')[1], 'base64')
    expect(createHash('sha256').update(png).digest('hex')).toBe(result.png_sha256)
    expect((await (await page.request.get(base + result.image_url)).body()).equals(png)).toBe(true)
    if (output && screenshot) {
      const clip = await page.locator('.lesson-panel').evaluate(panel => {
        const first = panel.querySelector('.lesson-capture').getBoundingClientRect()
        const last = panel.querySelector('.lesson-result').getBoundingClientRect()
        return {x:first.left + scrollX - 8, y:first.top + scrollY - 8,
          width:first.width + 16, height:last.bottom - first.top + 16}
      })
      await page.screenshot({path:join(output,screenshot), fullPage:true, clip})
    }
    console.log('Matched chart, saved PNG and tool result', result.id, png.length, 'bytes')
  }

  await page.goto(base, {waitUntil:'domcontentloaded'})
  await page.locator('summary').getByText('Chart chat',{exact:true}).click()
  await page.locator('summary').getByText('Chart signal',{exact:true}).click()
  await page.getByRole('button',{name:'Snapshot',exact:true}).click()
  await expect(page.getByText(/^Snapshot as of /)).toBeVisible({timeout:25000})
  const firstTime = await loadRange(firstEnd)
  await fixture('tool/rules')
  const rules = await send('What does WAIT mean in this app?', 'lookup_workstation_rules', '01-wait-rule.png')
  expect(rules.result.rule_set).toBe('chart_signal')
  expect(rules.message).toContain(rules.result.question.choices.find(c => c.value === 'wait').description)
  await fixture('tool/assessment')
  await fixture('long')
  const first = await send("Run the decision on the chart I'm looking at. What do you recommend?",
    'assess_visible_chart', '02-first-assessment.png')
  expect(first.result.previous_assessment).toBeNull()
  expect(first.message).toContain('No earlier AAPL assessment')
  await checkChart(first, firstTime, '03-first-chart.png')

  const laterTime = await loadRange(laterEnd)
  expect(Date.parse(laterTime)).toBeGreaterThan(Date.parse(firstTime))
  await fixture('short')
  const compare = 'Run a new decision for this chart and compare it with the earlier assessment.'
  const later = await send(compare, 'assess_visible_chart', '04-comparison.png')
  expect(rules.request.conversation_id).toBeTruthy()
  expect(first.request.conversation_id).toBe(rules.request.conversation_id)
  expect(later.request.conversation_id).toBe(first.request.conversation_id)
  expect(later.result.previous_assessment.id).toBe(first.result.id)
  expect(later.result.previous_assessment.answer).toEqual(first.result.answer)
  expect(later.message).toContain('Earlier · ' + first.message.split('\n')[0])
  expect(later.result.id).not.toBe(first.result.id)
  expect(later.result.png_sha256).not.toBe(first.result.png_sha256)
  await checkChart(later, laterTime, '05-updated-chart.png')
  await chat.getByRole('button',{name:'New chat'}).click()
  await expect(chat.locator('.chat-turn')).toHaveCount(0)
  if (!live) {
    const fresh = await send(compare, 'assess_visible_chart')
    expect(fresh.request.conversation_id).not.toBe(first.request.conversation_id)
    expect(fresh.request.history).toEqual([])
    expect(fresh.result.previous_assessment).toBeNull()
    expect(fresh.message).toContain('No earlier AAPL assessment')
  }
  console.log('PASS: three questions in one conversation, two chart times and matching current decision; New chat cleared transcript')
} finally { await browser.close() }
