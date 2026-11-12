import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pathToFileURL } from 'node:url'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
test('new browser session discovers existing paper and opens workspace', {skip:process.env.PAPERAGENT_WORKSPACE_LIVE_TEST!=='1',timeout:60000}, async () => {
 const project=process.env.PAPERAGENT_TEST_PROJECT_ID, run=process.env.PAPERAGENT_TEST_RUN_ID
 assert.ok(project && run, 'Set PAPERAGENT_TEST_PROJECT_ID and PAPERAGENT_TEST_RUN_ID for the opt-in live test')
 const entry=process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
 const { chromium } = await import(entry ? pathToFileURL(entry).href : 'playwright')
 const browser=await chromium.launch({executablePath:process.env.PAPERAGENT_CHROME_PATH,headless:true})
 const page=await browser.newPage({viewport:{width:1440,height:900}})
 const writes=[];page.on('request',r=>{if(r.url().includes('/api/') && r.method()!=='GET') writes.push(r.method())})
 try {
  await page.goto('http://127.0.0.1:5173/projects');await page.getByRole('heading',{name:'我的项目'}).waitFor()
  await page.screenshot({path:join(tmpdir(),'paperagent-project-list.png')})
  await page.locator(`a[href="/projects/${project}"]`).first().click()
  await page.getByRole('link',{name:/打开论文工作台/}).waitFor()
  await page.screenshot({path:join(tmpdir(),'paperagent-project-entry.png')})
  await page.getByText(/查看最近.*次运行/).click();await page.getByText(/查看最近.*次运行/).waitFor()
  await page.getByRole('link',{name:/打开论文工作台/}).click();await page.waitForURL(`**/runs/${run}`)
  await page.getByRole('navigation',{name:'工作台视图'}).waitFor()
  await page.waitForFunction(() => document.querySelectorAll('.chapter-link').length === 4)
  await page.screenshot({path:join(tmpdir(),'paperagent-reading-1440.png')})
  const firstTop = await page.locator('.paragraph').first().evaluate(el => el.getBoundingClientRect().top)
  assert.ok(firstTop < 900, `First paragraph starts below the first viewport: ${firstTop}`)
  for (const width of [1280,390]) {
   await page.setViewportSize({width,height:900})
   assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true)
   await page.screenshot({path:join(tmpdir(),`paperagent-reading-${width}.png`)})
  }
  assert.equal(await page.locator('.chapter-link').count(),4);assert.deepEqual(writes,[])
 } finally {await browser.close()}
})
