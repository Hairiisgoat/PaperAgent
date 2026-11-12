import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtempSync, writeFileSync, readdirSync, statSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { createServer } from 'node:net'
async function freePort(){return new Promise((resolve,reject)=>{const s=createServer();s.on('error',reject);s.listen(0,'127.0.0.1',()=>{const p=s.address().port;s.close(()=>resolve(p))})})}
test('real manuscript workspace read-only interactions', {skip:process.env.PAPERAGENT_WORKSPACE_LIVE_TEST!=='1',timeout:90000},async()=>{
 const project=process.env.PAPERAGENT_TEST_PROJECT_ID,run=process.env.PAPERAGENT_TEST_RUN_ID
 assert.ok(project && run, 'Set PAPERAGENT_TEST_PROJECT_ID and PAPERAGENT_TEST_RUN_ID')
 const dir=mkdtempSync(join(tmpdir(),'paperagent-workspace-')),port=await freePort();let ws
 const chrome=spawn(process.env.PAPERAGENT_CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',['--headless=new',`--remote-debugging-port=${port}`,`--user-data-dir=${join(dir,'profile')}`,'--no-first-run','about:blank'],{windowsHide:true,stdio:'ignore'})
 try{
  let targets;for(let i=0;i<100;i++){try{targets=await(await fetch(`http://127.0.0.1:${port}/json`)).json();break}catch{}await new Promise(r=>setTimeout(r,100))}assert.ok(targets)
  ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl);await new Promise((ok,fail)=>{ws.onopen=ok;ws.onerror=fail})
  let seq=0;const pending=new Map();ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id&&pending.has(m.id)){const [ok,fail]=pending.get(m.id);pending.delete(m.id);m.error?fail(Error(m.error.message)):ok(m.result)}}
  const call=(method,params={})=>new Promise((ok,fail)=>{const id=++seq;pending.set(id,[ok,fail]);ws.send(JSON.stringify({id,method,params}))})
  const ev=async expression=>{const v=await call('Runtime.evaluate',{expression,returnByValue:true});if(v.exceptionDetails)throw Error(v.exceptionDetails.text);return v.result.value}
  const until=async fn=>{const end=Date.now()+30000;while(Date.now()<end){if(await fn())return;await new Promise(r=>setTimeout(r,150))}throw Error('timeout')}
  await call('Page.enable');await call('Page.setDownloadBehavior',{behavior:'allow',downloadPath:dir});await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:`${process.env.PAPERAGENT_FRONTEND_URL || 'http://127.0.0.1:5173'}/projects/${project}/runs/${run}`})
  await until(()=>ev(`document.querySelectorAll('.chapter-link').length===4 && document.querySelectorAll('.citation').length>0 && !!document.querySelector('.review')`))
  assert.equal(await ev(`document.querySelectorAll('.chapter-link').length`),4)
  await ev(`document.querySelectorAll('.chapter-link')[1].click()`);assert.equal(await ev(`document.querySelectorAll('.chapter-link.selected').length`),1)
  await ev(`document.querySelector('.citation').click()`);assert.equal(await ev(`!!document.querySelector('[aria-label="文献详情"] blockquote')`),true);await ev(`document.querySelector('[aria-label="关闭文献详情"]').click()`)
  assert.equal(await ev(`['下载论文 Word','下载论文 PDF','下载审查报告'].every(s=>[...document.querySelectorAll('button')].some(b=>b.textContent.trim()===s))`),true)
  await ev(`[...document.querySelectorAll('.tabs button')].find(b=>b.textContent.includes('质量审查')).click()`);assert.equal(await ev(`getComputedStyle(document.querySelector('.review-side')).display!=='none'`),true)
  await until(()=>ev(`document.querySelectorAll('.review article').length>0`))
  assert.equal(await ev(`document.querySelector('.review-note')?.textContent.includes('暂无匹配的完整审查报告')`),true)
  await ev(`document.querySelector('.review article details summary').click()`)
  assert.equal(await ev(`document.querySelector('.review article details').open`),true)
  await ev(`(()=>{const select=document.querySelectorAll('.review select')[1]; const old=[...select.options].find(o=>o.textContent.includes('历史快照')); if(!old)throw Error('no historical report'); select.value=old.value;select.dispatchEvent(new Event('change',{bubbles:true}))})()`)
  assert.equal(await ev(`document.querySelector('.review').textContent.includes('历史快照')`),true)
  await ev(`document.querySelectorAll('.review select')[2].value='low'; document.querySelectorAll('.review select')[2].dispatchEvent(new Event('change',{bubbles:true}))`)
  await until(()=>ev(`document.querySelectorAll('.review article').length>0`))
  await ev(`[...document.querySelectorAll('.review article button')].find(b=>b.textContent.includes('定位正文')).click()`);await until(()=>ev(`document.querySelector('.tabs .active').textContent.includes('论文阅读')`))
  await ev(`[...document.querySelectorAll('.tabs button')].find(b=>b.textContent.includes('版本历史')).click()`);assert.equal(await ev(`document.querySelectorAll('.version-list > div').length`),4)
  await ev(`document.querySelector('.version-list button').click()`);await until(()=>ev(`!!document.querySelector('select') && document.querySelector('.edit-tools textarea')!==null`))
  await until(()=>ev(`document.querySelector('.edit-tools select')?.options.length >= 2`))
  await ev(`(()=>{const select=document.querySelector('.edit-tools select'); if(select.options.length<2) throw Error('missing historical version');select.selectedIndex=1;select.dispatchEvent(new Event('change',{bubbles:true}))})()`)
  await until(()=>ev(`!!document.querySelector('.version-compare')`))
  for (const [label,name] of [['下载论文 Word','PaperAgent-论文.docx'],['下载论文 PDF','PaperAgent-论文.pdf']]) {
   await ev(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(label)}).click()`)
   await until(()=>readdirSync(dir).includes(name) && statSync(join(dir,name)).size>1000)
  }
  await ev(`[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='下载审查报告').click()`)
  await until(()=>ev(`document.body.textContent.includes('当前论文没有匹配的完整审查报告')`))
  await call('Emulation.setDeviceMetricsOverride',{width:1280,height:800,deviceScaleFactor:1,mobile:false});await new Promise(r=>setTimeout(r,200));assert.equal(await ev(`document.documentElement.scrollWidth <= innerWidth`),true)
  await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});await new Promise(r=>setTimeout(r,400));assert.equal(await ev(`document.documentElement.scrollWidth <= innerWidth`),true)
  const shot=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(dir,'mobile.png'),Buffer.from(shot.data,'base64'))
  await call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});await ev(`document.querySelector('.draft').scrollIntoView()`);await new Promise(r=>setTimeout(r,400));const desk=await call('Page.captureScreenshot',{format:'png'});writeFileSync(join(dir,'desktop.png'),Buffer.from(desk.data,'base64'));console.log('Screenshots:',dir)
 } finally {if(ws)ws.close();if(chrome.exitCode===null)await new Promise(resolve=>{const k=spawn('taskkill.exe',['/PID',String(chrome.pid),'/T','/F'],{stdio:'ignore',windowsHide:true});k.once('exit',resolve);k.once('error',resolve)})}
})
