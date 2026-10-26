import {icon} from './icons.js';
import * as transport from './transport.js';
const $=s=>document.querySelector(s);
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const parse=v=>typeof v==='string'?JSON.parse(v):v;
const session={get(k){try{return sessionStorage.getItem(k);}catch{return null;}},set(k,v){try{sessionStorage.setItem(k,v);}catch{}},remove(k){try{sessionStorage.removeItem(k);}catch{}}};
const state={page:'chat',filter:'pending',data:null,online:false,cached:false,loading:false,error:'',pairError:'',pair:null,pairPayload:null,attachments:[],outbox:null,answering:new Map(),imageCache:new Map(),imageBytes:new Map(),viewerURL:null,imageLoading:new Set(),lastSignature:'',theme:'dark',draft:'',messageBusy:false,networkStatus:null,networkBusy:false};
try {state.theme=localStorage.getItem('pocket.theme')||'dark';}catch{}
let networkView=null,networkOperation=0;
const questionDrafts=new Map();const controllerDrafts=new Map();
const MEDIA_CACHE_BUDGET=12*1024*1024;const MEDIA_CACHE_COUNT=30;let inlineObserver=null;let viewerGeneration=0;
let messageOperation=0;
let toastTimer,refreshTimer,pollTimer,pairGeneration=0,refreshQueued=false,refreshRunning=false,refreshEpoch=null,lastController='',controllerLoaded=false,inboxScroll=0;
const CACHE='pocket.snapshot';const OUTBOX='pocket.outbox';
const appVersion='0.4.0';
function uuid(){if(crypto.randomUUID)return crypto.randomUUID();const a=crypto.getRandomValues(new Uint8Array(16));return Array.from(a,x=>x.toString(16).padStart(2,'0')).join('');}
function messageOf(error){return error?.message || '操作没有完成，请稍后重试';}
function toast(text){$('#toast').textContent=text;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,3300);}
function pendingRequests(){return (state.data?.requests||[]).filter(r=>['pending','submitting','unknown'].includes(r.status||'pending'));}
function cancelled(e){return ['stale_connection','stale_pairing'].includes(e?.code);}
let configRaw=null,configGeneration=0,configBusy=false;
function cancelBusyImport(){if(configBusy){stopPairPoll(true);if(connected())connectEvents();}}
function closeSheet(){configGeneration++;configRaw=null;networkView=null;clearInterval(window.sheetTimer);$('#sheetback').hidden=true;$('#sheet').replaceChildren();}
function sheet(html){closeSheet();$('#sheet').innerHTML='<div class="handle"></div>'+html;$('#sheetback').hidden=false;$('#sheet').querySelector('button,input,textarea')?.focus();}
function button(action,label,primary=false,extra=''){return '<button class="action '+(primary?'primary':'')+'" data-action="'+action+'" '+extra+'>'+label+'</button>';}
function connected(){return transport.getConnection().paired;}
function storageKey(){return transport.getConnection().url;}
function saveCache(){
  if(!state.data || !connected())return;
  const limit=Number(state.data.settings?.cache_limit)||30;
  const cache={url:storageKey(),saved:Date.now(),data:{...state.data,messages:(state.data.messages||[]).slice(-limit)}};
  let encoded=JSON.stringify(cache);
  while(encoded.length>850000 && cache.data.messages.length){cache.data.messages.shift();encoded=JSON.stringify(cache);}
  if(encoded.length<=1000000)session.set(CACHE,encoded);
}
function loadCache(){
  try {const c=JSON.parse(session.get(CACHE)||'null');if(c && c.url===storageKey() && c.data?.protocol===1){state.data=c.data;state.cached=true;lastController=c.data.controller?.id||'';controllerLoaded=true;}}
  catch{session.remove(CACHE);}
  try{const o=JSON.parse(session.get(OUTBOX)||'null');if(o && o.url===storageKey())state.outbox=o;}catch{session.remove(OUTBOX);}
}
function clearCache(){session.remove(CACHE);inlineObserver?.disconnect();closeViewer();for(const v of state.imageCache.values()){if(v.startsWith('blob:'))URL.revokeObjectURL(v);}state.imageCache.clear();state.imageBytes.clear();}
function safeMarkup(text){
  const source=String(text??'');const chunks=source.split(/```([^\n]*)\n([\s\S]*?)(?:```|$)/g);let html='';
  for(let i=0;i<chunks.length;i+=3){html+=esc(chunks[i]).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>').replace(/`([^`\n]+)`/g,'<code>$1</code>');if(i+2<chunks.length)html+='<pre><code>'+esc(chunks[i+2])+'</code></pre>';}
  return html;
}
function formatTime(value){if(!value)return '';const d=new Date(typeof value==='number'&&value<1e12?value*1000:value);return Number.isNaN(d.getTime())?'':d.toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'});}
function messageHTML(m){
  const user=m.role==='user';let text=safeMarkup(m.text);const imgs=(m.images||[]).filter(i=>i && typeof i.id==='string').map(i=>'<button class="imagebutton" data-action="image" data-media="'+esc(i.id)+'"><img class="message-image" data-media="'+esc(i.thumbnail_id||i.id)+'" alt="会话图片缩略图" loading="lazy"><span class="image-status" data-media-status="'+esc(i.thumbnail_id||i.id)+'">图片按需加载</span></button>').join('');
  return '<article class="'+(user?'user':'assistant')+'" data-message="'+esc(m.id)+'">'+(user?'<div class="bubble">':'<div><div class="agent-name">'+icon('codex')+'Codex</div>')+'<div class="message-content">'+text+'</div>'+imgs+'<time class="message-time">'+esc(formatTime(m.created_at))+'</time></div></article>';
}
function requestSupported(r){const c=state.data?.capabilities||{};if(r.type==='desktop_only')return false;if(r.type==='question'||r.type==='user_input')return !!c.user_input;return ['command_approval','file_approval','command','file','approval'].includes(r.type)&&!!c.approvals;}
function requestCard(r){
  const local=state.answering.get(r.id);const status=local?.status || r.status || 'pending';const title=r.title || (r.type==='question'?'需要你的选择':'需要你的允许');const body=typeof r.body==='object'?JSON.stringify(r.body,null,2):r.body||'';
  let html='<article class="card" data-request="'+esc(r.id)+'"><button class="source" data-action="request-source" data-id="'+esc(r.id)+'">'+icon('message')+'<span>'+esc(r.thread_title||'来源会话')+'</span>'+icon('chevron')+'</button><div class="card-inner">';
  if(['resolved','expired','denied','answered'].includes(status))return html+'<div class="resolved">'+icon('check')+'<div>'+esc(r.result_label||r.answer_label||(status==='expired'?'请求已失效':'已处理'))+'<small>以电脑端当前状态为准</small></div></div></div></article>';
  html+='<div class="card-label">'+icon(r.type==='question'||r.type==='user_input'?'help':'shield')+esc(r.type==='question'||r.type==='user_input'?'需要你选择':'需要你允许')+'</div><h3>'+esc(title)+'</h3>'+(body?'<div class="card-description message-content">'+esc(body)+'</div>':'');
  if(status==='unknown')return html+'<div class="unsupported">结果尚未确认，不会自动重发。请先核对电脑端当前状态。</div><button class="retry" data-action="refresh">核对状态</button></div></article>';
  if(status==='submitting')return html+'<div class="request-progress">正在回传你的选择…</div></div></article>';
  if(!requestSupported(r))return html+'<div class="unsupported">当前桌面连接暂不支持手机回答此类请求，请在电脑端处理。</div></div></article>';
  if(!state.online || state.cached)return html+'<div class="unsupported">连接恢复后，将核对这条请求是否仍有效。</div></div></article>';
  if(r.type==='question'||r.type==='user_input'){
    const qs=r.questions?.length?r.questions:[{id:'answer',question:title,options:r.options||[],allow_other:!r.options?.length}];
    html+='<form class="question-form" data-request="'+esc(r.id)+'">';
    qs.forEach((q,index)=>{
      const options=q.options||[];const multiple=!!(q.is_multiple||q.multiple||q.multi_select);const qid=String(q.id??index);
      html+='<fieldset class="question-field" data-question="'+esc(qid)+'"><legend>'+esc(q.question||q.text||q.header||title)+'</legend>';
      for(const option of options){const label=typeof option==='string'?option:option.label||option.text||option.id;const value=String(label);html+='<label class="question-option"><input type="'+(multiple?'checkbox':'radio')+'" name="question-'+index+'" value="'+esc(value)+'"><span>'+esc(label)+(option.description?'<small>'+esc(option.description)+'</small>':'')+'</span></label>';}
      if(q.allow_other||q.allowOther||q.is_other||!options.length)html+='<label class="question-label">'+(options.length?'其他回答':'你的回答')+'<textarea class="question-other" maxlength="12000" data-other="1" placeholder="写下你的回答…"></textarea></label>';
      html+='</fieldset>';
    });
    html+='<button class="action primary" type="submit" style="width:100%">发送回答</button></form>';
  }else{
    const options=r.options||[];
    if(!options.length)html+='<div class="unsupported">原请求未提供可回传选项，请在电脑处理。</div>';
    else html+='<div class="actions">'+options.map(o=>{const label=typeof o==='string'?o:o.label||o.id;const id=typeof o==='string'?o:String(o.id??o.value??label);return '<button class="action" data-action="answer" data-id="'+esc(r.id)+'" data-choice="'+esc(id)+'">'+esc(label)+'</button>';}).join('')+'</div>';
  }
  if(local?.error)html+='<div class="request-progress">'+esc(local.error)+'</div>';
  return html+'</div></article>';
}
function outboxHTML(){const o=state.outbox;if(!o)return '';if(o.controller_id!==state.data?.controller?.id)return '<div class="info-box">上一条消息的发送目标是「'+esc(o.controller_title||'原总控')+'」。请先核对原会话。<button class="retry" data-action="review-outbox">我已核对</button></div>';const status=o.status||'unknown';if(status==='delivered')return '<div class="info-box">电脑已接收你的上一条消息，等待原会话继续更新。<button class="retry" data-action="dismiss-delivered">收起提示</button></div>';return '<article class="user"><div class="bubble"><div class="message-content">'+esc(o.text||'图片消息')+'</div><div class="message-error">'+(status==='sending'?'正在发送…':status==='delivered'?'电脑已接收，等待会话更新。':'发送结果尚未确认，不会自动重发。')+'</div>'+(status==='unknown'?'<button class="retry" data-action="refresh">核对会话</button> <button class="retry" data-action="review-outbox">我已核对</button>':'')+'</div></article>';}
function renderChat(){
  const messages=state.data?.messages||[];
  const controller=state.data?.controller;
  let html='<div class="conversation">';
  if(!controller)html+='<div class="empty">'+icon('message')+'先选择电脑上的总控会话<br><button class="textlink" data-action="choose-controller">选择总控</button></div>';
  else if(!messages.length)html+='<div class="empty">'+icon('message')+'和总控继续聊<br>这里仅保留少量最近内容。</div>';
  if(state.outbox?.status==='unknown')html+='<div class="info-box unknown-alert">上次发送结果尚未确认。请核对最近消息，避免重复发送。<button class="retry" data-action="refresh">更新会话</button></div>';
  html+=messages.map(messageHTML).join('')+outboxHTML();
  const requests=pendingRequests();
  if(requests.length)html+='<div class="section-label">需要你处理</div>'+requests.map(requestCard).join('');
  html+='</div>';$('#page').innerHTML=html;loadImages();
}
function renderInbox(){
  const all=state.data?.requests||[];const pending=pendingRequests();const requests=state.filter==='pending'?pending:all.filter(r=>!['pending','submitting','unknown'].includes(r.status||'pending'));
  $('#page').innerHTML='<div class="page-title"><h2>需要你的时候</h2><p>各会话的请求汇在这里。<br>决定后，结果回到原会话。</p></div><div class="inbox-tabs"><button class="pill '+(state.filter==='pending'?'selected':'')+'" data-filter="pending">待处理 '+pending.length+'</button><button class="pill '+(state.filter==='done'?'selected':'')+'" data-filter="done">已处理</button></div><div class="inbox-content">'+(requests.length?requests.map(requestCard).join(''):'<div class="empty">'+icon('check')+(state.filter==='pending'?'暂时没有待处理事项':'暂时没有已处理记录')+'<br>新请求会出现在这里。</div>')+'</div>';
}
function setting(action,symbol,title,detail,value='',disabled=false){return '<button class="setting-row" data-action="'+action+'" '+(disabled?'disabled':'')+'>'+icon(symbol)+'<span class="setting-copy"><strong>'+title+'</strong><small>'+esc(detail)+'</small></span><span class="setting-value long-value">'+esc(value)+'</span>'+icon('chevron')+'</button>';}
function renderSettings(){
  state.networkStatus=transport.getNetworkStatus();
  const c=transport.getConnection();const native=transport.isNative();let nativeInfo={};try{nativeInfo=native?parse(window.Pocket.getConnection()):{};}catch{}
  $('#page').innerHTML='<div class="page-title"><h2>按你的习惯来</h2><p>连接自己的服务，把总控放在手边。</p></div><div class="settings"><div class="server-hero"><div class="server-heading">'+icon('threads')+esc(state.data?.server?.name||'我的电脑')+'<span class="setting-value" style="margin-left:auto">'+(state.online?'已连接':'连接待恢复')+'</span></div><p>'+esc(state.networkStatus?.active_url||'通过已保存的安全连接访问')+'<br>'+esc(state.data?.device?.name||c.deviceName||'这台设备')+'</p><button class="action" style="width:100%" data-action="pair">导入或更换服务</button></div><div class="section-label">连接与总控</div><div class="settings-group">'+setting('choose-controller','message','默认总控','打开应用，直接进入这个会话',state.data?.controller?.title||'选择总控')+setting('devices','shield','配对设备','查看设备，随时撤销访问','设备管理')+setting('networks','wifi','网络与线路',networkSummary(state.networkStatus),networkModeLabel(state.networkStatus))+setting('notifications','inbox','后台提醒',nativeInfo.backgroundEnabled?'后台连接服务运行时提醒；不保证被系统停止后送达':'可开启可见的后台连接服务',native?(nativeInfo.backgroundEnabled?'后台连接开启':'未开启'):'浏览器不支持')+'</div><div class="section-label">速度与存储</div><div class="settings-group">'+setting('cache-limit','threads','最近消息','只保留总控少量内容；其他历史按需读取','最近 '+(state.data?.settings?.cache_limit||30)+' 条')+setting('clear-cache','reset','清除本地内容','不删除电脑会话，也不清除未知回执','清除')+'</div><div class="section-label">应用</div><div class="settings-group">'+setting('theme','moon','外观','切换明暗',state.theme==='light'?'浅色':'深色')+setting('capabilities','help','连接能力','查看当前桌面支持的操作','查看')+setting('about','codex','开源与自托管','客户端和服务端，使用自己的服务','v'+appVersion)+setting('disconnect','close','退出这台设备','移除手机上的访问凭据','退出')+'</div><p class="subline">'+(native?'独立客户端，非 OpenAI 官方应用。':'浏览器备用入口；访问凭据只保留在当前浏览器会话。')+'</p></div>';
}
function renderThreads(query=''){
  const threads=state.data?.threads||[];
  $('#page').innerHTML='<div class="page-title"><h2>电脑上的会话</h2><p>只读取必要摘要，日常工作交给总控管理。</p></div><label class="search">'+icon('search')+'<input id="search" placeholder="搜索会话" aria-label="搜索会话" value="'+esc(query)+'"></label><div class="thread-list" id="thread-results">'+threadRows(threads,query)+'</div>';
}
function threadRows(threads,query){const rows=threads.filter(t=>(t.title||'').toLowerCase().includes(query.toLowerCase()));return rows.map(t=>'<button class="thread-row" data-action="thread-info" data-id="'+esc(t.id)+'"><i class="dot '+(['running','active','inProgress'].includes(t.status)?'blue':'gray')+'"></i><div class="thread-copy"><strong>'+esc(t.title||'未命名会话')+'</strong><p>'+esc(t.summary||t.status||'状态尚未加载')+'</p></div>'+icon('chevron')+'</button>').join('')||'<div class="empty">没有匹配的会话</div>';}
function renderPairing(){
  $('#page').innerHTML='<div class="pairing-screen"><span class="brandmark">'+icon('codex')+'</span><h1>把电脑上的 Codex<br>放到手机里。</h1><p>在电脑服务端打开配对页，扫描一次性二维码。聊天、图片和需要你的确认，都从这里接上。</p>'+(state.pairError?'<div class="error-box" role="alert">'+esc(state.pairError)+'</div>':'')+'<button class="action primary" data-action="scan">'+(transport.isNative()?'扫描电脑配对码':'使用配对内容')+'</button><button class="action" data-action="manual-pair">手动输入配对内容</button><p class="subline">使用你自己部署的服务。配对需要电脑端确认，不会改变 Codex 原有权限。</p></div>';
}
function captureQuestionDrafts(){for(const form of document.querySelectorAll('.question-form')){const values={};for(const field of form.querySelectorAll('[data-question]')){values[field.dataset.question]={selected:[...field.querySelectorAll('input:checked')].map(el=>el.value),other:field.querySelector('[data-other]')?.value||''};}questionDrafts.set(form.dataset.request,values);}while(questionDrafts.size>50)questionDrafts.delete(questionDrafts.keys().next().value);}
function restoreQuestionDrafts(){for(const form of document.querySelectorAll('.question-form')){const values=questionDrafts.get(form.dataset.request);if(!values)continue;for(const field of form.querySelectorAll('[data-question]')){const value=values[field.dataset.question];if(!value)continue;for(const input of field.querySelectorAll('input'))input.checked=value.selected.includes(input.value);const other=field.querySelector('[data-other]');if(other)other.value=value.other;}}}
function render(){
  captureQuestionDrafts();
  const paired=connected();const pageTop=$('#page').scrollTop;
  $('#app').classList.toggle('light',state.theme==='light');document.documentElement.style.colorScheme=state.theme;
  $('#title').textContent=!paired?'Codex Pocket':state.page==='chat'?'总控':state.page==='inbox'?'待处理':state.page==='threads'?'会话':'设置';
  const connectionText=!paired?'未配对':state.loading&&!state.data?'正在读取…':state.online?'已连接':state.cached?'离线 · 显示最近内容':'连接中断';
  $('#connection').innerHTML='<i class="dot '+(state.online?'':paired?'amber':'gray')+'"></i>'+connectionText;
  $('#offlinebar').hidden=!paired||(!state.error && state.online && !state.cached);
  $('#offlinebar').textContent=state.error || (state.cached?'正在显示最近内容；恢复连接后再核对待处理请求。':'暂时无法连接电脑，输入内容会保留。');
  $('#menu').disabled=!paired;$('#menu').innerHTML=icon(state.page==='threads'?'back':'menu');$('#refresh').hidden=!paired;$('#settings').hidden=!paired;
  const count=pendingRequests().length;$('#pending-strip').hidden=!paired||state.page!=='chat'||!count;$('#pending-label').textContent=count+' 项需要你处理';
  $('#composer-area').hidden=!paired||state.page!=='chat';
  const canSend=paired && !!state.data?.controller && !!state.data?.capabilities?.chat && state.online && !state.cached;
  $('#send').disabled=!canSend||state.messageBusy;$('#attach').disabled=!canSend||!state.data?.capabilities?.images;
  $('#composer-hint').textContent=!state.data?.capabilities?.chat&&paired?'当前桌面连接不支持发送消息':state.messageBusy?'正在交给电脑处理…':'电脑接收后，任务结果会继续更新';
  $('#send-target').textContent='发给总控'+(state.data?.controller?.title?' · '+state.data.controller.title:'');
  $('#bottomnav').hidden=!paired;
  $('#bottomnav').innerHTML=[['chat','message','总控'],['inbox','inbox','待处理'],['settings','settings','设置']].map(([id,symbol,label])=>'<button class="navitem '+(state.page===id?'active':'')+'" data-nav="'+id+'">'+icon(symbol)+'<span>'+label+'</span>'+(id==='inbox'&&count?'<b class="badge">'+count+'</b>':'')+'</button>').join('');
  if(!paired)renderPairing();else if(state.page==='chat')renderChat();else if(state.page==='inbox')renderInbox();else if(state.page==='threads')renderThreads();else renderSettings();
  restoreQuestionDrafts();
  $('#page').scrollTop=pageTop;
}
function navigate(page){if(state.page==='inbox')inboxScroll=$('#page').scrollTop;state.page=page;render();$('#page').scrollTop=page==='inbox'?inboxScroll:0;if(page==='chat')scrollBottom();}
function scrollBottom(){requestAnimationFrame(()=>$('#page').scrollTop=$('#page').scrollHeight);}
async function refresh({quiet=false}={}){
  if(!connected())return;
  const epoch=transport.getConnectionEpoch();
  if(refreshRunning&&refreshEpoch===epoch){refreshQueued=true;return;}
  if(refreshEpoch!==epoch)refreshQueued=false;
  refreshEpoch=epoch;refreshRunning=true;state.loading=true;if(!quiet)render();
  const nearBottom=$('#page').scrollHeight-$('#page').scrollTop-$('#page').clientHeight<120;
  try{
    const data=await transport.api('GET','/api/state');
    if(data.protocol!==1)throw new Error('服务协议不兼容，请更新客户端或服务端');
    state.data=data;state.online=true;state.cached=false;state.error='';state.loading=false;
    for(const [id,value]of state.answering){const request=data.requests?.find(r=>r.id===id);if(request && request.status!=='pending')state.answering.delete(id);}
    if(state.outbox && data.messages?.some(m=>m.client_id===state.outbox.client_id)){state.outbox=null;session.remove(OUTBOX);}
    if(!controllerLoaded){lastController=data.controller?.id||'';controllerLoaded=true;state.draft=$('#prompt').value;}else if(lastController!==(data.controller?.id||'')){if(lastController)controllerDrafts.set(lastController,$('#prompt').value);lastController=data.controller?.id||'';state.draft=controllerDrafts.get(lastController)||'';$('#prompt').value=state.draft;}
    saveCache();render();if(nearBottom&&state.page==='chat')scrollBottom();
  }catch(error){
    if(cancelled(error))return;
    state.loading=false;state.online=false;
    if(error.status===401){await unauthorized();return;}
    state.error=messageOf(error);render();
  }finally{if(refreshEpoch===epoch){refreshRunning=false;if(refreshQueued){refreshQueued=false;setTimeout(()=>refresh({quiet:true}),100);}}}
}
function connectEvents(){
  transport.subscribe(event=>{
    if(event.type==='settings'){render();return;}
    if(event.type==='network'){updateNetworkUI();return;}
    if(event.type==='unauthorized'){unauthorized();return;}
    if(event.type==='connected'){refresh({quiet:true});return;}
    if(event.type==='disconnected'){state.online=false;state.error='实时连接中断，正在重连。不会重复发送未确认操作。';render();return;}
    clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh({quiet:true}),120);
  },state.data?.revision||0);
}
function clearPrivateClientState(){clearCache();session.remove(OUTBOX);state.outbox=null;state.data=null;state.online=false;state.cached=false;state.error='';for(const a of state.attachments)URL.revokeObjectURL(a.preview);state.attachments=[];questionDrafts.clear();controllerDrafts.clear();lastController='';controllerLoaded=false;state.answering.clear();state.imageLoading.clear();state.networkStatus=null;state.networkBusy=false;networkOperation++;state.draft='';$('#prompt').value='';messageOperation++;state.messageBusy=false;renderAttachments();}
async function unauthorized(){transport.disconnect();clearPrivateClientState();state.pairError='这台设备的访问已失效，请重新扫码配对。';render();closeSheet();}
function normalizeQr(raw){let payload;try{payload=parse(raw);}catch{throw new Error('请粘贴电脑端显示的完整配对内容');}transport.preparePairing(payload);return payload;}
function manualPair(){if(state.pair||state.pairPayload)stopPairPoll(true);sheet('<h2>导入服务配置</h2><p>粘贴电脑端配置链接，先查看服务再导入。也支持旧版短期配对内容。</p><label class="field-label">配对内容<textarea class="field" id="pair-payload" autocomplete="off" spellcheck="false" placeholder="粘贴 codexpocket:// 配置链接或配对内容"></textarea></label><label class="field-label">设备名称<input class="field" id="device-name" maxlength="80" value="'+esc(transport.getConnection().deviceName||'我的手机')+'"></label><div id="pair-error"></div><div class="actions">'+button('close','取消')+button('pair-continue','继续',true)+'</div>');}
async function configPreview(raw){
  if(configBusy||state.pair||state.pairPayload)throw new Error('已有导入或配对正在进行，请先完成或取消');
  const preview=transport.previewConfigLink(raw);
  sheet('<h2>导入服务配置</h2><p>配置链接只提供连接信息，不授予访问权限。</p><div class="identity">'+esc(preview.name||'我的电脑')+'<small>服务名称仅为标签，请核对身份指纹</small><small>服务 '+esc((preview.server_identity?.id||'未知').slice(0,16))+'</small><small>TLS '+esc((preview.discovery?.tls_spki||'未知').slice(0,16))+'</small>'+((preview.different_server||transport.getConnection().paired&&!preview.already_paired)?'<small>这是另一台电脑，导入后需要重新确认访问</small>':'')+'<small>'+esc(preview.already_paired?'已配对：只更新地址，保留当前会话和草稿':'首次连接还需在电脑核对短语并批准')+'</small></div><div class="route-list">'+(preview.endpoints||[]).map(e=>'<div class="route-row"><strong>'+esc(e.label||'服务地址')+'</strong><span class="route-url">'+esc(e.url)+'</span></div>').join('')+'</div><div id="pair-error"></div><div class="actions">'+button('close','取消')+button('import-config','导入并连接',true)+'</div>');configRaw=raw;
}
async function importConfig(){
  if(!configRaw||configBusy)return;configBusy=true;const raw=configRaw,generation=configGeneration;const b=$('[data-action="import-config"]');if(b)b.disabled=true;
  try{const result=await transport.importConfigLink(raw);if(generation!==configGeneration){transport.cancelPairing();if(connected()){refresh({quiet:true});connectEvents();}return;}if(result.already_paired){closeSheet();updateNetworkUI(result.network_status);await refresh({quiet:true});connectEvents();toast('地址已更新，保留原有配对');}else{await pairPreview(result.pairing);await claimPair();}}catch(e){if(generation!==configGeneration||cancelled(e))return;pairError(messageOf(e));if(b)b.disabled=false;}finally{configBusy=false;}
}
async function pairPreview(raw){
  stopPairPoll(true);
  const payload=normalizeQr(raw);state.pairPayload=payload;
  sheet('<h2>确认连接的服务</h2><p>请确认这是电脑端显示的地址。下一步还需要在电脑端核对并允许。</p><div class="identity">'+esc(new URL(payload.url).host)+'<small>'+esc(payload.url)+'</small></div><label class="field-label">这台设备的名称<input class="field" id="device-name" maxlength="80" value="'+esc(transport.getConnection().deviceName||'我的手机')+'"></label><div id="pair-error"></div><div class="actions">'+button('close','取消')+button('claim-pair','请求配对',true)+'</div>');
}
async function claimPair(){
  if(!state.pairPayload)return;
  const name=($('#device-name')?.value||'我的手机').trim();
  if(!name || name.length>80)return pairError('请填写 1 到 80 字的设备名称');
  const generation=pairGeneration;
  const b=$('[data-action="claim-pair"]');if(b)b.disabled=true;
  try{
    const result=await transport.api('POST','/pair/claim',{ticket:state.pairPayload.ticket,device_name:name,client_nonce:uuid()});
    if(generation!==pairGeneration)return;
    state.pair={...result,deviceName:name};showPairWaiting();startPairPoll();
  }catch(e){if(cancelled(e)||generation!==pairGeneration)return;pairError(messageOf(e));if(b)b.disabled=false;}
}
function pairError(text){const root=$('#pair-error');if(root)root.innerHTML='<div class="error-box">'+esc(text)+'</div>';else toast(text);}
function showPairWaiting(){
  const p=state.pair;
  sheet('<h2>等待电脑端确认</h2><p>在电脑配对页核对相同短语，再允许这台手机。</p><div class="identity"><small>两端核对短语</small><div class="pair-phrase">'+esc(p.phrase||'等待服务返回校验信息')+'</div></div><p id="pair-expiry"></p><p>尚未获得访问权限。不要把配对内容转发给别人。</p><button class="action" style="width:100%" data-action="cancel-pair">取消配对</button>');
  const update=()=>{const raw=p.expires_at;const end=typeof raw==='number'?(raw<1e12?raw*1000:raw):Date.parse(raw);const left=Math.max(0,Math.ceil((end-Date.now())/1000));const el=$('#pair-expiry');if(el)el.textContent=Number.isFinite(left)?'有效期 '+Math.floor(left/60)+':'+String(left%60).padStart(2,'0'):'请在电脑端及时确认';};update();window.sheetTimer=setInterval(update,1000);
}
function stopPairPoll(cancel=false){pairGeneration++;clearTimeout(pollTimer);state.pair=null;state.pairPayload=null;if(cancel)transport.cancelPairing();}
function startPairPoll(){
  const generation=++pairGeneration;
  const poll=async()=>{
    const p=state.pair;if(!p||generation!==pairGeneration)return;
    try{
      const result=await transport.api('POST','/pair/status',{request_id:p.request_id,poll_token:p.poll_token},{timeout:15000,deviceName:p.deviceName});
      if(generation!==pairGeneration)return;
      if(result.status==='approved'){stopPairPoll();closeSheet();clearPrivateClientState();state.error='';state.pairError='';state.page='chat';render();await refresh();connectEvents();toast('配对完成');return;}
      if(result.status==='denied'||result.status==='expired'){const denied=result.status==='denied';stopPairPoll(true);sheet('<h2>'+(denied?'电脑未允许配对':'配对码已过期')+'</h2><p>这台设备没有获得新的访问权限。请在电脑重新生成配对码。</p><button class="action primary" style="width:100%" data-action="scan">重新配对</button><button class="textlink" data-action="close">暂时取消</button>');return;}
    }catch(e){if(generation!==pairGeneration||cancelled(e))return;if(e.status===400||e.status===401||e.status===404||e.status===410){stopPairPoll(true);sheet('<h2>配对未完成</h2><p>'+esc(messageOf(e))+'</p><button class="action primary" data-action="scan">重新配对</button>');return;}const el=$('#pair-expiry');if(el)el.textContent='连接暂时中断，正在核对配对状态…';}
    pollTimer=setTimeout(poll,1500);
  };poll();
}
function renderAttachments(){
  const root=$('#attachments');root.replaceChildren();
  state.attachments.forEach(a=>{
    const holder=document.createElement('span');holder.className='attachment';const img=document.createElement('img');img.src=a.preview;img.alt=a.file.name||'待发送图片';const b=document.createElement('button');b.type='button';b.textContent='×';b.dataset.action='remove-image';b.dataset.id=a.key;b.setAttribute('aria-label','移除图片');b.disabled=state.messageBusy;holder.append(img,b);root.append(holder);
    if(a.error){const error=document.createElement('div');error.className='attachment-error';error.textContent=a.error;root.append(error);}
  });
}
function addFiles(files){
  for(const file of files){if(state.attachments.length>=4){toast('每条消息最多 4 张图片');break;}if(!['image/jpeg','image/png','image/webp','image/gif'].includes(file.type)||file.size>15*1024*1024){toast('请选择 15MB 以内的 PNG、JPEG、WebP 或 GIF');continue;}state.attachments.push({key:uuid(),file,preview:URL.createObjectURL(file),media:null,error:''});}
  renderAttachments();
}
async function sendMessage(){
  const text=$('#prompt').value.trim();
  if(text.length>24000){toast('这条消息超过 24000 字，请分段发送');return;}
  if(state.messageBusy||(!text&&!state.attachments.length))return;
  if(!state.online||state.cached||!state.data?.controller){toast('请先恢复连接并选择总控');return;}
  if(state.outbox?.status==='unknown'){toast('上次发送结果尚未确认，请先核对会话后清除提示');return;}
  const operation=++messageOperation;state.messageBusy=true;render();
  try{
    for(const a of state.attachments){if(!a.media){try{a.error='';a.media=await transport.upload(a.file);if(operation!==messageOperation)return;}catch(e){a.error=messageOf(e);renderAttachments();throw e;}}}
    const outbox={url:storageKey(),controller_id:state.data.controller.id,controller_title:state.data.controller.title,client_id:uuid(),text,attachments:state.attachments.map(a=>a.media.id),status:'sending',created_at:Date.now()};
    state.outbox=outbox;session.set(OUTBOX,JSON.stringify(outbox));render();scrollBottom();
    try{
      const result=await transport.api('POST','/api/messages',{client_id:outbox.client_id,controller_id:outbox.controller_id,text,attachments:outbox.attachments});
      outbox.status=result.status==='delivered'?'delivered':'unknown';session.set(OUTBOX,JSON.stringify(outbox));
      if(result.status==='delivered'){$('#prompt').value='';state.draft='';for(const a of state.attachments)URL.revokeObjectURL(a.preview);state.attachments=[];renderAttachments();toast('电脑已接收');}
      else toast('发送结果未知，不会自动重发');
    }catch(e){
      if(cancelled(e))return;
      if(e.status>=400&&e.status<500 && ![408,429].includes(e.status)){state.outbox=null;session.remove(OUTBOX);throw e;}
      outbox.status='unknown';session.set(OUTBOX,JSON.stringify(outbox));toast('发送结果尚未确认，请核对会话');
    }
    await refresh({quiet:true});
  }catch(e){if(!cancelled(e))toast(messageOf(e));}
  finally{if(operation===messageOperation){state.messageBusy=false;render();}}
}
async function answerRequest(id,body){
  const r=state.data?.requests?.find(x=>x.id===id);if(!r||r.status&&r.status!=='pending'){toast('这条请求已不是待处理状态');return;}
  if(!state.online||state.cached||state.answering.has(id)){toast('请先核对最新请求状态');return;}
  state.answering.set(id,{status:'submitting'});render();
  try{
    const result=await transport.api('POST','/api/requests/'+encodeURIComponent(id)+'/answer',{client_id:uuid(),...body});
    if(result.status==='delivered'){state.answering.set(id,{status:'submitting'});toast('选择已交给电脑，正在核对结果');await refresh({quiet:true});}
    else{state.answering.set(id,{status:'unknown'});render();}
  }catch(e){if(cancelled(e))return;if(e.status>=400 && e.status<500 && ![408,429].includes(e.status)){state.answering.delete(id);toast(messageOf(e));await refresh({quiet:true});}else{state.answering.set(id,{status:'unknown',error:messageOf(e)});render();}}
}

function discardMediaURL(url){if(url?.startsWith('blob:'))URL.revokeObjectURL(url);}
function cacheMedia(id,url,bytes){
  state.imageCache.delete(id);state.imageBytes.delete(id);
  state.imageCache.set(id,url);state.imageBytes.set(id,bytes);
  const total=()=>[...state.imageBytes.values()].reduce((sum,n)=>sum+n,0);
  while(state.imageCache.size>MEDIA_CACHE_COUNT || total()>MEDIA_CACHE_BUDGET){
    const oldId=state.imageCache.keys().next().value;const oldURL=state.imageCache.get(oldId);
    for(const img of document.querySelectorAll('img[data-media]'))if(img.dataset.media===oldId)img.removeAttribute('src');
    discardMediaURL(oldURL);state.imageCache.delete(oldId);state.imageBytes.delete(oldId);
  }
}
function loadImages(){
  inlineObserver?.disconnect();
  const targets=[...document.querySelectorAll('.imagebutton')];
  if(typeof IntersectionObserver!=='function'){for(const el of targets)loadInlineImage(el.querySelector('img[data-media]')?.dataset.media);return;}
  inlineObserver=new IntersectionObserver(entries=>{
    for(const entry of entries){
      const img=entry.target.querySelector('img[data-media]');if(!img)continue;
      if(entry.isIntersecting)loadInlineImage(img.dataset.media);
      else if(img.hasAttribute('src')){if(img.naturalWidth&&img.naturalHeight)img.style.aspectRatio=img.naturalWidth+'/'+img.naturalHeight;img.removeAttribute('src');}
    }
  },{root:$('#page'),rootMargin:'180px 0px'});
  for(const el of targets)inlineObserver.observe(el);
}
async function loadInlineImage(id){
  if(!id)return;
  if(state.imageCache.has(id)){const url=state.imageCache.get(id);const bytes=state.imageBytes.get(id)||0;state.imageCache.delete(id);state.imageCache.set(id,url);applyImage(id,url);return;}
  if(state.imageLoading.has(id))return;state.imageLoading.add(id);
  try{
    const url=await transport.imageURL(id);const bytes=transport.imageBytes(id)||(url.startsWith('data:')?Math.ceil(url.length*.75):0);
    if(bytes>MEDIA_CACHE_BUDGET){discardMediaURL(url);for(const el of document.querySelectorAll('[data-media-status]'))if(el.dataset.mediaStatus===id)el.textContent='图片较大，点按查看原图';return;}
    cacheMedia(id,url,bytes);applyImage(id,url);
  }catch(error){if(cancelled(error))return;for(const el of document.querySelectorAll('[data-media-status]'))if(el.dataset.mediaStatus===id)el.textContent='缩略图暂时未能读取，点按查看原图';}
  finally{state.imageLoading.delete(id);}
}
function applyImage(id,url){for(const el of document.querySelectorAll('img[data-media]'))if(el.dataset.media===id)el.src=url;for(const el of document.querySelectorAll('[data-media-status]'))if(el.dataset.mediaStatus===id)el.textContent='点击查看原图';}
function closeViewer(){viewerGeneration++;const url=state.viewerURL;state.viewerURL=null;$('#viewer-img').removeAttribute('src');$('#viewer-img').classList.remove('zoom');$('#viewer').hidden=true;discardMediaURL(url);}
async function viewImage(id){
  try{closeViewer();const generation=viewerGeneration;const url=await transport.imageURL(id);if(generation!==viewerGeneration){discardMediaURL(url);return;}state.viewerURL=url;$('#viewer-img').src=url;$('#viewer-img').classList.remove('zoom');$('#viewer').hidden=false;}
  catch(error){if(!cancelled(error))toast(messageOf(error));}
}
async function chooseController(){
  if(!connected())return;
  const threads=(state.data?.threads||[]).filter(t=>!t.archived&&!t.subagent);
  sheet('<h2>选择默认总控</h2><p>从电脑上的现有会话里选择。绑定后，手机只读取这个会话的少量最近内容。</p>'+(threads.length?threads.map(t=>'<button class="sheet-option" data-action="bind-controller" data-id="'+esc(t.id)+'">'+icon('message')+'<span>'+esc(t.title||'未命名会话')+'</span></button>').join(''):'<p>没有可选会话，请先在电脑上打开 Codex 会话。</p>'));
}
async function listDevices(){try{const result=await transport.api('GET','/api/devices');sheet('<h2>配对设备</h2><p>撤销后，该设备需要重新配对。电脑上的会话不会删除。</p>'+result.devices.map(d=>'<div class="device-row"><strong>'+esc(d.name)+(d.current?' · 本机':'')+'</strong><small>最近活动 '+esc(formatTime(d.last_seen)||'尚无记录')+'</small><button class="textlink" data-action="revoke-device" data-id="'+esc(d.id)+'" data-current="'+(d.current?'1':'0')+'">撤销访问</button></div>').join(''));}catch(e){toast(messageOf(e));}}
function capabilities(){const c=state.data?.capabilities||{};sheet('<h2>当前连接能力</h2><p>功能由电脑端适配器实际提供。不支持的操作会保留明确提示。</p><div class="identity">'+[['chat','文字消息'],['images','图片'],['live','实时更新'],['approvals','原生权限确认'],['user_input','原生提问'],['interrupt','中断任务']].map(([k,label])=>'<div>'+label+'<small>'+(c[k]?'已提供':'当前未提供')+'</small></div>').join('')+'</div>'+button('close','知道了'));}

const NETWORK_KINDS={lan:'局域网',tailscale:'Tailscale',private:'私网',public:'公网'};
const NETWORK_STATES={available:'可达',unreachable:'不可达',identity_mismatch:'身份不符',unverified:'待验证'};
function routeLatency(endpoint){return endpoint?.status==='available'&&typeof endpoint.latency_ms==='number'&&Number.isFinite(endpoint.latency_ms)&&endpoint.latency_ms>=0?(endpoint.latency_ms<1?'＜1 ms':Math.round(endpoint.latency_ms)+' ms'):'未测量';}
function networkModeLabel(network){return !network?.supported?'当前不可用':network.mode==='manual'?'手动固定':'自动选优';}
function activeEndpoint(network){return network?.endpoints?.find(e=>e.active||e.url===network.active_url);}
function networkSummary(network){
  if(!network?.supported)return network?.error||'请使用手机 App 设置连接线路';
  const active=activeEndpoint(network);const name=active?.label||network.active_url;
  return name?'当前：'+name+' · '+routeLatency(active):'尚未选定可用线路';
}
function endpointLabel(endpoint){return endpoint.label||NETWORK_KINDS[endpoint.kind]||'服务地址';}
function networkConfigCurrent(network){
  const endpoints=(network.endpoints||[]).map(e=>({url:e.url,label:e.label||'',kind:e.kind,managed:e.managed===true,source:e.source,tls:e.tls}));
  const config={mode:network.mode,endpoints,auto_sync:network.auto_sync===true};if(config.mode==='manual')config.manual_url=network.manual_url||network.active_url;return config;
}
function networkPanelHTML(network){
  if(!network.supported)return '<h2>网络与线路</h2><p>'+esc(network.error||'当前客户端不支持多线路设置')+'</p><div class="info-box">浏览器备用入口只使用当前服务地址，不会在浏览器中模拟自动选路。手机 App 才负责检测和切线。</div>'+button('close','知道了');
  const busy=state.networkBusy||network.probing;const enabled=network.multi_route===true;
  const current=activeEndpoint(network);const currentLabel=current?endpointLabel(current):'尚未选定';
  let html='<div class="network-title"><h2>网络与线路</h2><button class="iconbtn" data-action="close" aria-label="关闭网络设置">'+icon('close')+'</button></div><p>优先低延迟，改善足够明显且持续稳定时再切换。切线不会重发未知结果的消息。</p>';
  const discoveryLabels={disabled:'自动同步已关闭',idle:'等待自动发现',syncing:'正在同步地址',searching:'正在寻找已配对电脑',ready:'地址已同步',error:'自动发现暂不可用'};
  html+='<div class="info-box"><strong>自动发现</strong><p>'+esc(network.discovery_supported?(discoveryLabels[network.discovery_state]||'等待自动发现'):'旧配对码未授权自动发现，请在新版电脑端重新扫码。之后地址变化无需重扫。')+'</p>'+(network.last_sync?'<small>上次更新 '+esc(new Date(network.last_sync).toLocaleString('zh-CN',{hour12:false}))+'</small>':'')+(network.discovery_error?'<p>'+esc(network.discovery_error)+'</p>':'')+'<div style="margin-top:12px"><button class="textlink" data-action="network-sync" '+(!network.discovery_supported||busy?'disabled':'')+'>'+(network.auto_sync?'关闭自动同步':'开启自动同步')+'</button></div></div>';
  if(network.error)html+='<div class="error-box">'+esc(network.error)+'</div>';
  if(!enabled)html+='<div class="info-box">当前配对只支持单地址。请在电脑重新生成配对码并扫码，启用带服务身份验证的多线路。</div>';
  if(network.development_http)html+='<div class="info-box">当前含开发用 HTTP 线路，传输未加密。正式使用请配置 HTTPS。</div>';
  html+='<div class="route-current"><span class="subline">当前线路</span><strong>'+esc(currentLabel)+'</strong><span class="route-url">'+esc(network.active_url||'等待可用地址')+'</span><span class="route-current-latency">响应耗时 '+esc(routeLatency(current))+'</span></div>';
  html+='<div class="network-mode"><button class="pill '+(network.mode==='auto'?'selected':'')+'" data-action="network-auto" '+(!enabled||busy?'disabled':'')+'>自动选优</button><button class="pill '+(network.mode==='manual'?'selected':'')+'" data-action="network-manual" '+(!enabled||busy?'disabled':'')+'>手动固定</button></div>';
  if(network.mode==='manual'&&network.manual_url)html+='<p class="subline">固定目标：<span class="route-url inline">'+esc(network.manual_url)+'</span><br>固定线路失败时不会自动换线。</p>';
  html+='<div class="network-head"><span class="section-label">服务地址 '+network.endpoints.length+'/6</span><button class="textlink" data-action="network-probe" '+(busy?'disabled':'')+'>'+(busy?'正在检测…':'立即检测')+'</button></div>';
  html+='<div class="route-list">'+network.endpoints.map((e,index)=>{
    const active=e.active||e.url===network.active_url;const fixed=network.mode==='manual'&&(network.manual_url||network.active_url)===e.url;
    return '<article class="route-row"><div class="route-heading"><strong>'+esc(endpointLabel(e))+'</strong><span class="route-state '+esc(e.status)+'">'+esc(NETWORK_STATES[e.status]||'待验证')+'</span></div><span class="route-url">'+esc(e.url)+'</span><div class="route-meta"><span>'+esc(NETWORK_KINDS[e.kind]||'私网')+'</span>'+(active?'<span>当前使用</span>':'')+(fixed?'<span>固定目标</span>':'')+'<span>'+esc(routeLatency(e))+'</span>'+(e.managed?'<span>'+(e.source==='auto'?'自动发现':'电脑登记')+'</span>':'')+'</div><div class="route-actions"><button class="textlink" data-action="network-edit" data-index="'+index+'" '+(!enabled||busy||e.managed?'disabled':'')+'>编辑</button><button class="textlink" data-action="network-remove" data-index="'+index+'" '+(!enabled||busy||e.managed||network.endpoints.length<=1?'disabled':'')+'>删除</button></div></article>';
  }).join('')+'</div><button class="action" style="width:100%" data-action="network-add" '+(!enabled||busy||network.endpoints.length>=6?'disabled':'')+'>添加服务地址</button><p class="route-help">'+(network.discovery_supported&&network.auto_sync?'局域网和 Tailscale 地址自动同步；公网等其他地址可选手动添加。':'新地址先在电脑管理页登记，再检测启用。')+'配置有 HTTPS 时，自动模式不会降级到 HTTP。只验证属于同一服务的地址；这里的耗时不代表 Tailscale 是直连或中继。</p>';
  return html;
}
function showNetworks(){state.networkStatus=transport.getNetworkStatus();sheet(networkPanelHTML(state.networkStatus));networkView='list';}
function updateNetworkUI(result){
  state.networkStatus=result||transport.getNetworkStatus();
  if(state.page==='settings')render();
  if(networkView==='list')$('#sheet').innerHTML='<div class="handle"></div>'+networkPanelHTML(state.networkStatus);
}
function showEndpointEditor(index){
  const network=transport.getNetworkStatus();if(!network.supported||!network.multi_route)return toast('请先重新扫码，启用多线路');
  if(state.networkBusy||network.probing)return toast('正在检测线路，请稍后编辑');
  if(index<0&&network.endpoints.length>=6)return toast('最多配置 6 个服务地址');
  const endpoint=index<0?{url:'',label:'',kind:'lan'}:network.endpoints[index];if(!endpoint)return;
  if(endpoint.managed)return toast('此地址由电脑管理，请在电脑端修改');
  const fixed=network.mode==='manual'&&(network.manual_url||network.active_url)===endpoint.url;
  sheet('<h2>'+(index<0?'添加服务地址':'编辑服务地址')+'</h2><p>新地址先在电脑管理页登记，再保存并检测。名称只用于帮助你识别。</p><form id="endpoint-form"><input id="endpoint-index" type="hidden" value="'+index+'"><label class="field-label">名称<input class="field" id="endpoint-label" maxlength="40" placeholder="例如：家中局域网" value="'+esc(endpoint.label)+'"></label><label class="field-label">服务根地址<input class="field route-input" id="endpoint-url" type="url" maxlength="2048" inputmode="url" autocomplete="off" autocapitalize="none" spellcheck="false" placeholder="https://host:port" value="'+esc(endpoint.url)+'"></label><label class="field-label">线路类型<select class="field" id="endpoint-kind">'+Object.entries(NETWORK_KINDS).map(([key,label])=>'<option value="'+key+'" '+(endpoint.kind===key?'selected':'')+'>'+label+'</option>').join('')+'</select></label>'+(fixed?'<div class="info-box">修改当前固定地址后，将先改为自动选优。新地址验证通过后，可再次手动固定。</div>':'')+'<div id="network-editor-error"></div><div class="actions">'+button('network-back','返回')+'<button class="action primary" type="submit">保存地址</button></div></form>');networkView='editor';
}
async function networkOperationRun(work,success){
  if(state.networkBusy)return;
  const operation=++networkOperation;state.networkBusy=true;updateNetworkUI();
  try{const result=await work();if(operation!==networkOperation)return;state.networkBusy=false;updateNetworkUI(result);if(success)toast(success);}
  catch(error){if(operation!==networkOperation||cancelled(error))return;state.networkBusy=false;updateNetworkUI();const root=$('#network-editor-error');if(networkView==='editor'&&root)root.innerHTML='<div class="error-box">'+esc(messageOf(error))+'</div>';else toast(messageOf(error));}
  finally{if(operation===networkOperation){state.networkBusy=false;if(networkView==='list')updateNetworkUI();}}
}
function cleanEndpointURL(raw){let u;try{u=new URL(raw);}catch{throw new Error('请填写完整的 HTTP 或 HTTPS 地址');}if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash||!['','/'].includes(u.pathname))throw new Error('请填写不含账号、路径和参数的服务根地址');return u.origin;}
async function saveEndpoint(){
  if(state.networkBusy)return;const network=transport.getNetworkStatus();if(!network.supported||!network.multi_route)return toast('当前配对不支持多线路');
  const index=Number($('#endpoint-index').value);const old=index>=0?network.endpoints[index]:null;if(old?.managed)return toast('此地址由电脑管理');let url,label,kind;
  try{url=cleanEndpointURL($('#endpoint-url').value.trim());label=$('#endpoint-label').value.trim();kind=$('#endpoint-kind').value;if(label.length>40||!Object.hasOwn(NETWORK_KINDS,kind))throw new Error('名称或类型不符合要求');if(network.endpoints.some((e,i)=>i!==index&&cleanEndpointURL(e.url)===url))throw new Error('这个地址已在列表中');}catch(error){$('#network-editor-error').innerHTML='<div class="error-box">'+esc(messageOf(error))+'</div>';return;}
  const config=networkConfigCurrent(network);if(index>=0){if(!old)return;config.endpoints[index]={url,label,kind};}else{if(config.endpoints.length>=6)return toast('最多配置 6 个地址');config.endpoints.push({url,label,kind});}
  if(config.mode==='manual'&&old&&config.manual_url===old.url&&old.url!==url){config.mode='auto';delete config.manual_url;}
  await networkOperationRun(async()=>{const result=await transport.configureNetworks(config);if(networkView==='editor'){networkView='list';}return result;},'地址已保存，请登记后检测启用');
}
function confirmRemoveEndpoint(index){const network=transport.getNetworkStatus();const endpoint=network.endpoints?.[index];if(endpoint?.managed)return toast('此地址由电脑管理');if(!endpoint||network.endpoints.length<=1)return toast('至少保留一个服务地址');const fixed=network.mode==='manual'&&(network.manual_url||network.active_url)===endpoint.url;sheet('<h2>删除这个地址？</h2><div class="identity">'+esc(endpointLabel(endpoint))+'<small class="route-url">'+esc(endpoint.url)+'</small></div><p>'+(fixed?'这是当前固定目标。删除后将改为自动选优，使用剩余已验证地址。':'将从候选列表移除这个地址，不会删除服务端或聊天内容。')+'</p><div class="actions">'+button('network-back','保留')+button('network-confirm-remove','删除地址',false,'data-index="'+index+'"')+'</div>');networkView='remove';}
async function removeEndpoint(index){const network=transport.getNetworkStatus();if(!network.multi_route||network.endpoints.length<=1)return;const endpoint=network.endpoints[index];if(!endpoint||endpoint.managed)return;const config=networkConfigCurrent(network);config.endpoints.splice(index,1);if(config.mode==='manual'&&config.manual_url===endpoint.url){config.mode='auto';delete config.manual_url;}await networkOperationRun(async()=>{const result=await transport.configureNetworks(config);if(networkView==='remove')networkView='list';return result;},'地址已移除');}
function showManualNetworks(){const network=transport.getNetworkStatus();if(!network.multi_route)return;sheet('<h2>固定使用一个地址</h2><p>只可固定已验证可达的地址。固定线路失败时不会自动换线。新地址需先在电脑管理页登记并检测。</p>'+network.endpoints.map(e=>'<button class="sheet-option route-manual" data-action="network-fixed" data-url="'+esc(e.url)+'" '+(e.status!=='available'?'disabled':'')+'><span><strong>'+esc(endpointLabel(e))+'</strong><small class="route-url">'+esc(e.url)+'</small></span><small>'+esc(NETWORK_STATES[e.status]||'待验证')+'</small></button>').join('')+'<button class="textlink" data-action="network-back">返回线路列表</button>');networkView='manual';}
async function changeNetworkMode(mode,url){const network=transport.getNetworkStatus();if(!network.supported||!network.multi_route)return;const config=networkConfigCurrent(network);config.mode=mode;if(mode==='manual'){const endpoint=network.endpoints.find(e=>e.url===url);if(!endpoint||endpoint.status!=='available')return toast('请先验证这个地址');config.manual_url=url;}else delete config.manual_url;await networkOperationRun(async()=>{const result=await transport.configureNetworks(config);if(networkView==='manual')networkView='list';return result;},mode==='manual'?'已固定线路':'已开启自动选优');}
async function runNetworkProbe(){await networkOperationRun(()=>transport.probeNetworks(),'线路检测已完成');}
function notifications(){
  if(!transport.isNative()||typeof window.Pocket.setBackgroundEnabled!=='function'){sheet('<h2>后台提醒</h2><p>当前客户端没有接入系统后台通知。手机 App 可使用可见的后台连接服务；它仍受系统省电设置约束。</p>'+button('close','知道了'));return;}
  let c={};try{c=parse(window.Pocket.getConnection());}catch{}
  sheet('<h2>后台连接与提醒</h2><p>开启后，App 会运行带常驻通知的连接服务，在服务运行期间接收待处理提醒。若被系统停止或网络断开，不能保证送达。</p><div class="identity">系统通知权限<small>'+(c.notificationsGranted?'已授予':'尚未授予')+'</small>后台连接<small>'+(c.backgroundEnabled?'已开启':'未开启')+'</small></div>'+button('background-toggle',c.backgroundEnabled?'关闭后台连接':'开启后台连接',true,'style="width:100%"')+'<button class="textlink" data-action="notification-settings">打开系统通知设置</button>');
}
function showAbout(){sheet('<h2>Codex Pocket</h2><p>独立的个人 Codex 手机客户端，非 OpenAI 官方应用。</p><div class="identity">App '+appVersion+'<small>协议 v1</small>服务端 '+esc(state.data?.server?.version||'未知')+'</div><p>客户端与服务端按自托管方式组织，个人部署配置、凭据和聊天数据与源码分离。许可证与源码入口以随包文档为准。</p>'+button('close','知道了'));}
function sourceInfo(id){const r=state.data?.requests?.find(x=>x.id===id);if(!r)return;sheet('<h2>'+esc(r.thread_title||'来源会话')+'</h2><p>这条请求来自此会话。你的回答会按原请求标识回传，不经过总控转述。</p><div class="identity">'+esc(r.title||'待处理请求')+'<small>'+esc(r.type||'')+'</small></div>'+button('close','返回请求'));}
async function handleAction(action,b){
  try{
    if(configBusy&&['scan','manual-pair','pair','pair-continue'].includes(action)){toast('正在导入，请先完成或取消');return;}
    if(action==='close'){cancelBusyImport();if(state.pair||state.pairPayload){stopPairPoll(true);if(connected()){refresh({quiet:true});connectEvents();}}closeSheet();return;}
    if(action==='refresh'){await refresh();return;}
    if(action==='dismiss-delivered'){state.outbox=null;session.remove(OUTBOX);render();return;}
    if(action==='review-outbox'){sheet('<h2>已经核对电脑上的会话？</h2><p>收起本机提示后，可以继续发新消息。这不会重发上一条，也不会把未知结果改成成功。服务端仍保留原发送回执。</p><div class="actions">'+button('close','继续核对')+button('confirm-outbox-reviewed','收起提示',true)+'</div>');return;}
    if(action==='confirm-outbox-reviewed'){state.outbox=null;session.remove(OUTBOX);closeSheet();render();return;}
    if(action==='scan'){if(state.pair||state.pairPayload)stopPairPoll(true);if(transport.isNative()){closeSheet();transport.scanPairing();}else manualPair();return;}
    if(action==='manual-pair'){manualPair();return;}
    if(action==='import-config'){await importConfig();return;}
    if(action==='pair-continue'){const raw=$('#pair-payload').value;const name=$('#device-name').value;try{if(raw.trim().startsWith('codexpocket://'))await configPreview(raw.trim());else{await pairPreview(raw);$('#device-name').value=name;}}catch(e){pairError(messageOf(e));}return;}
    if(action==='claim-pair'){await claimPair();return;}
    if(action==='cancel-pair'){stopPairPoll(true);closeSheet();if(connected()){refresh({quiet:true});connectEvents();}toast('已停止配对，未取得新的访问权限');return;}
    if(action==='pair'){manualPair();return;}
    if(action==='choose-controller'){await chooseController();return;}
    if(action==='bind-controller'){if(state.outbox?.status==='unknown'){toast('请先核对上一条发送结果，再更换总控');return;}await transport.api('POST','/api/controller',{thread_id:b.dataset.id});closeSheet();await refresh();navigate('chat');toast('已绑定总控');return;}
    if(action==='thread-info'){const t=state.data?.threads?.find(t=>t.id===b.dataset.id);if(t)sheet('<h2>'+esc(t.title||'会话')+'</h2><p>'+esc(t.summary||t.status||'当前只有会话摘要')+'</p><p>日常管理可回到总控，用自然语言安排。</p><div class="actions">'+button('close','返回')+button('bind-controller','设为默认总控',false,'data-id="'+esc(t.id)+'"')+'</div>');return;}
    if(action==='request-source'){sourceInfo(b.dataset.id);return;}
    if(action==='answer'){await answerRequest(b.dataset.id,{choice_id:b.dataset.choice});return;}
    if(action==='image'){await viewImage(b.dataset.media);return;}
    if(action==='remove-image'){const i=state.attachments.findIndex(a=>a.key===b.dataset.id);if(i>=0){const[a]=state.attachments.splice(i,1);URL.revokeObjectURL(a.preview);}renderAttachments();return;}
    if(action==='devices'){await listDevices();return;}
    if(action==='revoke-device'){sheet('<h2>撤销设备访问？</h2><p>这台设备将断开连接，需要重新扫码。不会删除电脑上的会话。</p><div class="actions">'+button('close','取消')+button('confirm-revoke','撤销访问',true,'data-id="'+esc(b.dataset.id)+'" data-current="'+esc(b.dataset.current)+'"')+'</div>');return;}
    if(action==='confirm-revoke'){await transport.api('DELETE','/api/devices/'+encodeURIComponent(b.dataset.id));closeSheet();if(b.dataset.current==='1')await unauthorized();else await listDevices();return;}
    if(action==='cache-limit'){sheet('<h2>保留多少最近内容</h2><p>只缓存总控少量消息。未处理请求和未知回执会保留到核对完成。</p>'+[15,30,60].map(n=>'<button class="sheet-option" data-action="set-cache" data-limit="'+n+'">最近 '+n+' 条</button>').join(''));return;}
    if(action==='set-cache'){await transport.api('POST','/api/settings',{cache_limit:Number(b.dataset.limit)});closeSheet();await refresh();toast('最近内容保留量已更新');return;}
    if(action==='clear-cache'){sheet('<h2>清除本地内容？</h2><p>清除最近消息和图片缓存。配对、未知发送回执、未处理请求和电脑会话都会保留。</p><div class="actions">'+button('close','取消')+button('confirm-clear','清除内容',true)+'</div>');return;}
    if(action==='confirm-clear'){await transport.api('DELETE','/api/cache');clearCache();if(state.data)state.data.messages=[];closeSheet();render();toast('最近内容已清除');return;}
    if(action==='theme'){state.theme=state.theme==='dark'?'light':'dark';try{localStorage.setItem('pocket.theme',state.theme);}catch{}render();return;}
    if(action==='notifications'){notifications();return;}
    if(action==='networks'){showNetworks();return;}
    if(action==='network-edit'){showEndpointEditor(Number(b.dataset.index));return;}
    if(action==='network-add'){showEndpointEditor(-1);return;}
    if(action==='network-save'){await saveEndpoint();return;}
    if(action==='network-remove'){confirmRemoveEndpoint(Number(b.dataset.index));return;}
    if(action==='network-confirm-remove'){await removeEndpoint(Number(b.dataset.index));return;}
    if(action==='network-sync'){const n=transport.getNetworkStatus();if(!n.discovery_supported)return;const c=networkConfigCurrent(n);c.auto_sync=!n.auto_sync;await networkOperationRun(()=>transport.configureNetworks(c),'自动同步设置已更新');return;}
    if(action==='network-auto'){await changeNetworkMode('auto');return;}
    if(action==='network-manual'){showManualNetworks();return;}
    if(action==='network-fixed'){await changeNetworkMode('manual',b.dataset.url);return;}
    if(action==='network-probe'){await runNetworkProbe();return;}
    if(action==='network-back'){showNetworks();return;}
    if(action==='background-toggle'){let c=parse(window.Pocket.getConnection());window.Pocket.setBackgroundEnabled(!c.backgroundEnabled);closeSheet();render();toast('请按系统提示设置通知，后台状态以设置页为准');return;}
    if(action==='notification-settings'){window.Pocket.showNotificationSettings?.();return;}
    if(action==='capabilities'){capabilities();return;}
    if(action==='about'){showAbout();return;}
    if(action==='disconnect'){sheet('<h2>退出这台设备？</h2><p>移除本机访问凭据和最近缓存。服务端配对记录可在电脑或其他可信设备上撤销。</p><div class="actions">'+button('close','取消')+button('confirm-disconnect','退出',true)+'</div>');return;}
    if(action==='confirm-disconnect'){transport.disconnect();clearPrivateClientState();closeSheet();render();return;}
  }catch(e){if(cancelled(e))return;toast(messageOf(e));if(e.status===401)await unauthorized();}
}
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b||b.disabled)return;
  if(b.dataset.nav){navigate(b.dataset.nav);return;}
  if(b.dataset.filter){state.filter=b.dataset.filter;render();return;}
  if(b.dataset.action)handleAction(b.dataset.action,b);
});
document.addEventListener('submit',async e=>{
  if(e.target.id==='composer'){e.preventDefault();await sendMessage();return;}
  if(e.target.id==='endpoint-form'){e.preventDefault();await saveEndpoint();return;}
  if(e.target.classList.contains('question-form')){
    e.preventDefault();const answers={};let valid=true;
    for(const field of e.target.querySelectorAll('[data-question]')){
      const selected=[...field.querySelectorAll('input:checked')].map(x=>x.value);const other=field.querySelector('[data-other]')?.value.trim();if(other)selected.push(other);if(!selected.length)valid=false;answers[field.dataset.question]=selected;
    }
    if(!valid){toast('请回答每个问题后再发送');return;}
    await answerRequest(e.target.dataset.request,{answers});
  }
});
$('#menu').onclick=()=>navigate(state.page==='threads'?'chat':'threads');$('#settings').onclick=()=>navigate('settings');$('#refresh').onclick=()=>refresh();$('#pending-strip').onclick=()=>navigate('inbox');
$('#attach').onclick=()=>$('#file').click();$('#file').onchange=()=>{addFiles($('#file').files);$('#file').value='';};
$('#prompt').addEventListener('input',e=>{state.draft=e.target.value;e.target.style.height='36px';e.target.style.height=Math.min(110,e.target.scrollHeight)+'px';});
$('#prompt').addEventListener('keydown',e=>{if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)&&!e.isComposing){e.preventDefault();sendMessage();}});
$('#sheetback').onclick=e=>{if(e.target===$('#sheetback')){cancelBusyImport();if(state.pair){toast('配对仍在等待电脑确认，可点取消配对');return;}closeSheet();}};
$('#viewer-close').onclick=()=>closeViewer();$('#zoom').onclick=()=>$('#viewer-img').classList.toggle('zoom');$('#viewer-img').onclick=()=>$('#viewer-img').classList.toggle('zoom');
document.addEventListener('keydown',e=>{if(e.key==='Escape'){cancelBusyImport();if(!state.pair)closeSheet();closeViewer();}});
document.addEventListener('input',e=>{if(e.target.id==='search')$('#thread-results').innerHTML=threadRows(state.data?.threads||[],e.target.value);});
document.addEventListener('visibilitychange',()=>{if(document.hidden){if(!transport.isNative())transport.unsubscribe();}else if(connected()){refresh({quiet:true});connectEvents();}});
window.pocketConfigLink=raw=>{configPreview(raw).catch(e=>toast(messageOf(e)));};
window.pocketPairing=raw=>{if(configBusy||state.pair||state.pairPayload){toast('已有导入或配对正在进行，请先完成或取消');return;}pairPreview(raw).catch(e=>{state.pairError=messageOf(e);toast(messageOf(e));render();});};
window.pocketSharedImage=async raw=>{try{const data=typeof raw==='string'?raw:parse(raw).data_url;if(!/^data:image\/(png|jpeg|webp|gif);base64,/.test(data))throw new Error('不支持的分享图片');if(data.length>22*1024*1024)throw new Error('分享图片超过大小限制');const [header,base64]=data.split(',',2);const mime=header.slice(5).split(';')[0];const rawBytes=atob(base64);const bytes=new Uint8Array(rawBytes.length);for(let i=0;i<rawBytes.length;i++)bytes[i]=rawBytes.charCodeAt(i);const blob=new Blob([bytes],{type:mime});addFiles([new File([blob],'分享图片.'+(mime.split('/')[1]||'jpg'),{type:mime})]);navigate('chat');}catch(e){toast(messageOf(e));}};
window.pocketBack=()=>{if(!$('#viewer').hidden){closeViewer();return true;}if(!$('#sheetback').hidden){cancelBusyImport();if(state.pair||state.pairPayload){stopPairPoll(true);if(connected()){refresh({quiet:true});connectEvents();}toast('已取消本次配对');}closeSheet();return true;}if(state.page!=='chat'){navigate('chat');return true;}return false;};
window.pocketNotification=()=>{navigate('inbox');refresh({quiet:true});};
window.pocketConnectionChanged=()=>{render();if(connected()){refresh({quiet:true});connectEvents();}};
for(const[id,name]of [['menu','menu'],['settings','settings'],['refresh','reset'],['attach','plus'],['send','arrow'],['viewer-close','close'],['zoom','expand'],['pending-icon','inbox'],['pending-arrow','chevron']])$('#'+id).innerHTML=icon(name);
async function boot(){
  if(connected()){loadCache();render();await refresh();connectEvents();}else render();
}
boot();
