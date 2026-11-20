import { chromium, expect } from '@playwright/test'
import { createHash } from 'node:crypto'
const browser = await chromium.launch({channel:'chrome', headless:true, args:['--no-sandbox']})
const page = await browser.newPage({viewport:{width:1400,height:900}})
const api = page.request
try {
  await page.goto('http://127.0.0.1:18862/', {waitUntil:'domcontentloaded'})
  await page.locator('summary').getByText('Chart signal',{exact:true}).click()
  const signal = page.getByRole('button',{name:'Analyze visible chart'})
  await expect(signal).toBeEnabled({timeout:15000})
  const [firstReply] = await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/api/chart-agent') && r.request().method() === 'POST'),
    signal.click()
  ])
  const {snapshot} = firstReply.request().postDataJSON()
  const {tools} = await firstReply.json()
  expect(tools.map(tool => tool.name)).toEqual(['assess_visible_chart'])
  const assessment = tools[0].result
  const chartBytes = Buffer.from(snapshot.image_url.split(',')[1], 'base64')
  expect(assessment.png_sha256).toBe(createHash('sha256').update(chartBytes).digest('hex'))
  expect(assessment.context.symbol).toBe(snapshot.context.symbol)
  expect(assessment.context.interval).toBe(snapshot.context.interval)
  expect(assessment.context.last_completed_candle).toBe(snapshot.context.last_completed_candle)
  expect((await (await api.get(new URL(assessment.image_url, page.url()).href)).body()).equals(chartBytes)).toBe(true)
  await expect(page.getByText('WAIT (probability: 0.00)',{exact:true})).toBeVisible({timeout:15000})
  await expect(page.getByAltText('Chart image sent for analysis')).toBeVisible()
  await expect(page.getByAltText('Chart image sent for analysis')).toHaveAttribute('src', assessment.image_url)
  await expect(page.getByText('Visible chart',{exact:true})).toBeVisible()
  for (const choice of ['long','short']) {
    await api.post(`http://127.0.0.1:18861/__fixture/${choice}`)
    await signal.click()
    await expect(page.getByText(`${choice.toUpperCase()} (probability: ${choice === 'long' ? '0.65' : '0.35'})`,{exact:true})).toBeVisible()
  }
  await api.post('http://127.0.0.1:18861/__fixture/failure')
  await signal.click()
  await expect(page.getByText('Analysis provider error',{exact:true})).toBeVisible()
  let result = await (await api.get('http://127.0.0.1:18861/__fixture/state')).json()
  expect(result.orders).toBe(0)
  expect(result.calls).toHaveLength(4)
  expect(result.lessons).toHaveLength(4)
  expect(result.calls.map(x=>x[0])).toEqual(result.lessons.map(x=>x[0]))
  expect(result.lessons.at(-1)[1]).toBe('provider_error')
  await page.reload()
  await page.locator('summary').getByText('Chart signal',{exact:true}).click()
  await page.locator('summary').getByText('Chart chat',{exact:true}).click()
  await expect(page.getByText('Recent chart interpretations',{exact:true})).toBeVisible()
  await page.getByRole('button',{name:/^LONG · AAPL 1m/}).click()
  await expect(page.getByText('LONG (probability: 0.65)',{exact:true})).toBeVisible()
  await expect(page.getByAltText('Chart image sent for analysis')).toBeVisible()
  result = await (await api.get('http://127.0.0.1:18861/__fixture/state')).json()
  expect(result.orders).toBe(0)
  expect(result.fills).toBe(0)
  expect(result.cash).toBe('1000')
  expect(result.positions).toEqual([])

  const chat = page.getByRole('region', {name:'Chart chat'})
  const input = chat.getByRole('textbox')
  const lastTurn = chat.locator('.chat-turn').last()
  async function send(question) {
    await input.fill(question)
    const [response] = await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/api/chart-agent')),
      chat.getByRole('button', {name:'Send', exact:true}).click(),
    ])
    const request = response.request().postDataJSON()
    expect(request.snapshot.image_url).toMatch(/^data:image\/png;base64,/)
    const reply = await response.json()
    await expect(lastTurn.locator('.chat-reply')).toHaveText(reply.message)
    await lastTurn.locator('summary').click()
    await expect(lastTurn.locator('summary')).toHaveText(`Tool calls (${reply.tools.length})`)
    for (const call of reply.tools) {
      await expect(lastTurn.getByText(call.name, {exact:true})).toBeVisible()
      expect(JSON.parse(await lastTurn.locator('pre').textContent())).toEqual({arguments:call.arguments, result:call.result})
    }
    return {request, reply}
  }
  const preview = page.getByAltText('Chart image sent for analysis')
  const previousImage = await preview.getAttribute('src')
  await api.post('http://127.0.0.1:18861/__fixture/tool/rules')
  const rule = await send('Explain the chart decision rules.')
  expect(rule.reply.tools.map(x=>x.name)).toEqual(['lookup_workstation_rules'])
  expect(rule.reply.message).toContain(rule.reply.tools[0].result.question.choices[0].description)
  const unclear = await send('Should I wait?')
  expect(unclear.reply.status).toBe('clarification')
  expect(unclear.reply.tools).toHaveLength(0)
  expect(unclear.request.history.at(-1).content).toBe(rule.reply.message)
  await expect(preview).toHaveAttribute('src', previousImage)
  expect((await (await api.get('http://127.0.0.1:18861/__fixture/state')).json()).calls).toHaveLength(4)

  await api.post('http://127.0.0.1:18861/__fixture/tool/assessment')
  await api.post('http://127.0.0.1:18861/__fixture/wait')
  const checked = await send('Assess the chart currently on screen.')
  const decision = checked.reply.tools[0].result
  const chartResult = page.locator('.lesson-result')
  expect(checked.reply.message).toContain(decision.context.symbol)
  expect(checked.reply.message).toContain(decision.context.last_completed_candle)
  await expect(chartResult.locator('.lesson-choice')).toHaveText('WAIT (probability: 0.00)')
  expect(checked.reply.message).toContain(await chartResult.locator('.lesson-choice').textContent())
  await expect(preview).toHaveAttribute('src', decision.image_url)
  expect(decision.png_sha256).toBe(createHash('sha256').update(Buffer.from(checked.request.snapshot.image_url.split(',')[1], 'base64')).digest('hex'))

  await api.post('http://127.0.0.1:18861/__fixture/failure')
  const failed = await send('Assess this chart again.')
  expect(failed.reply.tools[0].result.status).toBe('provider_error')
  expect(failed.reply.tools[0].result.answer).toBeNull()
  await expect(chartResult.locator('.lesson-choice')).toHaveText('Analysis provider error')
  await expect(preview).toHaveAttribute('src', failed.reply.tools[0].result.image_url)

  await api.post('http://127.0.0.1:18861/__fixture/short')
  let release, received = false
  const held = new Promise(resolve => { release = resolve })
  await page.route('**/api/chart-agent', async route => {
    const response = await route.fetch()
    received = true
    await held
    await route.fulfill({response})
  }, {times:1})
  await input.fill('Assess the visible chart.')
  await chat.getByRole('button', {name:'Send', exact:true}).click()
  try {
    await expect.poll(() => received, {timeout:10000}).toBe(true)
    await page.getByRole('button', {name:'5m', exact:true}).click()
    await expect(page.getByRole('button', {name:'5m', exact:true})).toHaveAttribute('aria-pressed','true')
  } finally { release() }
  await expect(lastTurn.locator('.chat-reply')).toContainText('SHORT (probability: 0.35)')
  await expect(lastTurn.locator('.chat-note')).toContainText('earlier capture')
  await expect(chartResult).toHaveCount(0)
  await expect(preview).toHaveCount(0)
  result = await (await api.get('http://127.0.0.1:18861/__fixture/state')).json()
  expect(result.calls).toHaveLength(7)
  expect(result.lessons).toHaveLength(7)
  expect(result.fills).toBe(0)
  expect(result.orders).toBe(0)
  expect(result.cash).toBe('1000')
  console.log('PASS: chat rule lookup, clarification, assessment, exact tool traces, saved PNG, provider failure and late 1m reply after switching to 5m; $1,000 cash, zero orders and fills')
} finally { await browser.close() }
