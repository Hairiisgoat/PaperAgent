/** Opt-in real browser downloads; GET only. */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtempSync, realpathSync, rmSync, readdirSync, statSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { createServer } from 'node:net'

async function freePort() { return await new Promise((resolve, reject) => { const s=createServer(); s.on('error',reject); s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))}) }) }
async function waitUrl(url, child) { for(let n=0;n<100;n++){if(child.exitCode!==null)throw Error('Browser exited'); try {if((await fetch(url)).ok)return}catch{} await new Promise(r=>setTimeout(r,100))} throw Error('Browser not ready') }
test('real manuscript browser downloads', {skip:process.env.PAPERAGENT_EXPORT_LIVE_TEST!=='1', timeout:90000}, async () => {
  const project=process.env.PAPERAGENT_TEST_PROJECT_ID,run=process.env.PAPERAGENT_TEST_RUN_ID
  assert.ok(project && run, 'Set PAPERAGENT_TEST_PROJECT_ID and PAPERAGENT_TEST_RUN_ID')
  const root=realpathSync(tmpdir()), temp=mkdtempSync(join(root,'paperagent-export-browser-'))
  assert.equal(dirname(realpathSync(temp)).toLowerCase(),root.toLowerCase())
  let chrome,ws
  try {
    const port=await freePort()
    chrome=spawn(process.env.PAPERAGENT_CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', ['--headless=new',`--remote-debugging-port=${port}`,`--user-data-dir=${join(temp,'profile')}`,'--no-first-run','about:blank'],{stdio:'ignore',windowsHide:true})
    await waitUrl(`http://127.0.0.1:${port}/json/version`,chrome)
    const targets=await (await fetch(`http://127.0.0.1:${port}/json`)).json()
    ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl)
    await new Promise((ok,fail)=>{ws.onopen=ok;ws.onerror=fail})
    let serial=0;const pending=new Map()
    ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id&&pending.has(m.id)){const [ok,fail]=pending.get(m.id);pending.delete(m.id);m.error?fail(Error(m.error.message)):ok(m.result)}}
    const call=(method,params={})=>new Promise((ok,fail)=>{const id=++serial;pending.set(id,[ok,fail]);ws.send(JSON.stringify({id,method,params}))})
    const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true});if(r.exceptionDetails)throw Error('Browser script failed');return r.result.value}
    await call('Page.enable'); await call('Page.setDownloadBehavior',{behavior:'allow',downloadPath:temp})
    await call('Page.navigate',{url:`${process.env.PAPERAGENT_FRONTEND_URL || 'http://127.0.0.1:5173'}/projects/${project}/runs/${run}`})
    async function until(fn){const end=Date.now()+30000;while(Date.now()<end){if(await fn())return;await new Promise(r=>setTimeout(r,150))}throw Error('Browser download timed out')}
    for (const [label,name] of [['下载论文 Word','PaperAgent-论文.docx'],['下载论文 PDF','PaperAgent-论文.pdf'],['下载审查报告','PaperAgent-审查报告.docx']]) {
      const q=JSON.stringify(label)
      await until(()=>ev(`!!document.body && !![...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${q} && !x.disabled)`))
      await ev(`[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${q}).click()`)
      await until(()=>readdirSync(temp).includes(name) && statSync(join(temp,name)).size>1000)
      assert.ok(statSync(join(temp,name)).size>1000)
      const signature=readFileSync(join(temp,name)).subarray(0,4).toString('ascii')
      assert.equal(signature, name.endsWith('.pdf') ? '%PDF' : 'PK\u0003\u0004')
    }
    console.log('Real browser downloaded three distinct files')
  } finally {
    if(ws)ws.close()
    if(chrome && chrome.exitCode===null) await new Promise(resolve=>{const k=spawn('taskkill.exe',['/PID',String(chrome.pid),'/T','/F'],{stdio:'ignore',windowsHide:true});k.once('exit',resolve);k.once('error',resolve)})
    if(chrome && chrome.exitCode===null) await new Promise(resolve=>{chrome.once('exit',resolve);setTimeout(resolve,5000)})
    assert.equal(dirname(realpathSync(temp)).toLowerCase(),root.toLowerCase())
    rmSync(temp,{recursive:true,force:true,maxRetries:10,retryDelay:300})
  }
})
