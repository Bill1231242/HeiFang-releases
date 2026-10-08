import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {mkdirSync,writeFileSync,existsSync} from 'node:fs';
import {resolve} from 'node:path';
const root=resolve(process.env.HEIFANG_MAC_SOURCE_ROOT),arch=process.env.HEIFANG_MAC_ARCH;
assert(['arm64','x64'].includes(arch));
const require=createRequire(root+'/apps/desktop/package.json');const {_electron}=require('playwright');
const profile='/tmp/mac098-native-'+arch,home='/tmp/mac098-home-'+arch,marker=home+'/mcp-started';
mkdirSync(profile,{recursive:true});mkdirSync(home,{recursive:true});assert(!existsSync(marker));
const proof={complete:false,architecture:arch,version:'0.9.98',scope:'Real macOS packaged Main/Preload/renderer; synthetic REST only, not production account/provider acceptance',checks:[],errors:[]};let app;
try{
 app=await _electron.launch({executablePath:resolve(process.env.HEIFANG_MAC_EXECUTABLE),args:['--user-data-dir='+profile],env:{...process.env,ELECTRON_RUN_AS_NODE:undefined,ELECTRON_RENDERER_URL:undefined},timeout:45000});
 proof.meta=await app.evaluate(({app,BrowserWindow},home)=>{app.setPath('home',home);for(const w of BrowserWindow.getAllWindows())w.hide();return {version:app.getVersion(),packaged:app.isPackaged,home:app.getPath('home'),userData:app.getPath('userData'),arch:process.arch}},home);
 assert.equal(proof.meta.version,'0.9.98');assert.equal(proof.meta.arch,arch);assert(proof.meta.packaged);assert.equal(proof.meta.userData,profile);
 const page=await app.firstWindow();page.setDefaultTimeout(12000);page.on('pageerror',e=>proof.errors.push(e.message));
 await page.route('https://heifang.billtsing.site/api/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace(/^\/api/,'');let body={data:[],items:[],total:0};
  if(route.request().method()!=='GET')return route.fulfill({status:403,contentType:'application/json',body:'{"error":"Scoped QA forbids writes"}'});
  if(path==='/v1/auth/me')body={id:'mac098-native-qa',username:'Ab',display_name:'Mac 验收',email:'qa@example.test',role:'user',created_at:'2026-10-08T00:00:00Z',password_must_change:false,avatar_url:null,credit_balance:1000};
  if(path==='/v1/capabilities')body={skills:[{name:'seo_research',summary:'目录验收，不执行查询',group:'研究',body:'QA metadata only'}],tools:[],guidelines:{ceo:'',ceo_addon:'',shared_base:'',worker_leaf:'',worker_captain:''}};
  if(path==='/v1/knowledge/libraries')body={items:[],limit:200,offset:0};
  await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(body)});
 });
 await page.reload({waitUntil:'domcontentloaded'});const editor=page.getByTestId('composer-body');await editor.waitFor();
 const ipc=await page.evaluate(async marker=>{const saved=await window.mcpApi.upsertServer({id:'mac098-cold-qa',name:'Mac 冷 MCP 验收',enabled:true,command:'/usr/bin/touch',args:[marker],origin:'manual'});if(!saved.ok)throw Error('Could not store isolated QA metadata');const catalog=await window.mcpApi.runOp({op:'list_tools',args:{catalog_only:true}});const local=await window.extensionApi.listCapabilities({localOnly:true});return {catalog,localOk:local.ok}},marker);
 assert(ipc.catalog.ok&&ipc.localOk);assert.equal(ipc.catalog.value.servers.find(s=>s.id==='mac098-cold-qa').status,'configured');assert(!existsSync(marker));
 for(const mode of ['Chat','Work']){
  await page.getByRole('radiogroup').getByText(mode,{exact:true}).click();await editor.fill('@');await page.getByRole('button',{name:/^Skill/}).click();await page.getByText('seo_research',{exact:true}).waitFor();
  await page.getByRole('button',{name:/返回/}).click();await page.getByRole('button',{name:/^MCP/}).click();await page.getByText('Mac 冷 MCP 验收',{exact:true}).waitFor();await page.getByRole('button',{name:/^Mac 冷 MCP 验收/}).click();assert(!existsSync(marker));
  await page.screenshot({path:'/tmp/heifang-mac-dist/mac-'+arch+'-'+mode+'.png'});proof.checks.push(mode+' first mention, actual native offline IPC, cold MCP selection without starting process');await editor.fill('');
 }
 assert.equal(proof.errors.length,0);proof.complete=true;
}catch(e){proof.failure=String(e);process.exitCode=1;}
finally{await app?.close();writeFileSync('/tmp/heifang-mac-dist/native-qa-'+arch+'.json',JSON.stringify(proof,null,2));console.log(JSON.stringify(proof));}
