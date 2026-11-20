import { chromium, expect } from '@playwright/test'
const browser=await chromium.launch({channel:'chrome',headless:true,args:['--no-sandbox']})
const page=await browser.newPage({viewport:{width:1400,height:900}})
try {
  await page.goto('http://127.0.0.1:18872/',{waitUntil:'domcontentloaded'})
  await page.locator('summary').getByText('Watchlist review',{exact:true}).click()
  const panel=page.locator('.watchlist-panel')
  await expect(panel.getByText('AAPL · ready')).toBeVisible()
  await expect(panel.getByText('AMD · stale')).toBeVisible()
  await expect(panel.getByText('SPY · missing')).toBeVisible()
  await panel.getByRole('button',{name:'Run once'}).click()
  await expect(panel.getByText('WAIT · probability 0.00',{exact:false})).toBeVisible({timeout:15000})
  await expect(panel.locator('.watchlist-log').getByText('Completed assessment',{exact:false})).toBeVisible()
  await panel.getByLabel('Minimum rise ($)').fill('0.50')
  await panel.getByRole('button',{name:'Run once'}).click()
  await expect(panel.getByText('No accepted candidates; no chart assessments.',{exact:false})).toBeVisible({timeout:15000})
  await expect(panel.getByText('WAIT · probability 0.00',{exact:false})).toHaveCount(0)
  console.log('PASS: frozen synthetic watchlist, WAIT with zero probability, and empty scan')
} finally { await browser.close() }
