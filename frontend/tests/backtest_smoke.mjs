import { chromium, expect } from '@playwright/test'
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-sandbox']})
const page=await browser.newPage({viewport:{width:1400,height:900}})
try {
  await page.goto('http://127.0.0.1:18882/',{waitUntil:'domcontentloaded'})
  const accountBefore=await (await page.request.get('http://127.0.0.1:18881/api/paper/account')).json()
  await page.locator('summary').getByText('SMA backtest',{exact:true}).click()
  const panel=page.locator('.backtest-panel')
  await expect(panel.getByLabel('Start UTC')).not.toHaveValue('')
  await panel.getByRole('button',{name:'Run backtest'}).click()
  await expect(panel.getByText('BUY · filled',{exact:false})).toBeVisible({timeout:15000})
  await expect(panel.getByText('CLOSE · filled',{exact:false})).toBeVisible()
  await expect(panel.getByText('total P&L $1',{exact:false})).toBeVisible()
  const accountAfter=await (await page.request.get('http://127.0.0.1:18881/api/paper/account')).json()
  expect(accountAfter).toEqual(accountBefore)
  console.log('PASS: recorded fixture backtest, two fills, $1 P&L, paper account unchanged')
} finally { await browser.close() }
