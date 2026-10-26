/* Fixed protocol client. Native secrets never cross the Javascript bridge. */
const native = () => window.Pocket && typeof window.Pocket.request === 'function';
const parse = v => typeof v === 'string' ? JSON.parse(v) : v;
const pending = new Map();
let sequence = 0;
let candidate = null;
let pairingEpoch = 0;
let connectionEpoch = 0;
let activeClaim = null;
const mediaSizes = new Map();
function recordMediaSize(id,size){mediaSizes.delete(id);mediaSizes.set(id,Math.max(0,Number(size)||0));while(mediaSizes.size>256)mediaSizes.delete(mediaSizes.keys().next().value);}
export function imageBytes(id){return mediaSizes.get(id)||0;}
let connection = {paired:false,url:'',deviceName:''};
let abortStream = null;
let streamGeneration = 0;
let eventHandler = () => {};
const store = {
  get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
  put(key,value) { try { sessionStorage.setItem(key,value); } catch {} },
  remove(key) { try { sessionStorage.removeItem(key); } catch {} },
};

export class ApiError extends Error {
  constructor(message,status=0,code='network_error') { super(message); this.name='ApiError'; this.status=status; this.code=code; }
}
function decoded(body) {
  if (body === '' || body == null) return {};
  try { return parse(body); } catch { throw new ApiError('服务返回了无法识别的内容',502,'invalid_response'); }
}
function fail(body,status) {
  const e=body?.error;
  return new ApiError(typeof e==='string'?e:(e?.message || body?.message || '请求没有完成'),status,e?.code || 'request_failed');
}
window.pocketResponse = (id,status,raw) => {
  const task=pending.get(String(id));
  if (!task) return;
  pending.delete(String(id));clearTimeout(task.timer);
  try {
    const body=decoded(raw);
    if(Number(status)<200 || Number(status)>=300) task.reject(fail(body,Number(status)));
    else task.resolve(body);
  } catch(e) { task.reject(e); }
};
window.pocketEvent = raw => {
  try {
    const event=decoded(raw);
    eventHandler(event);
  } catch { eventHandler({type:'disconnected',message:'实时更新暂时中断'}); }
};
function invoke(method,args,timeout=45000) {
  return new Promise((resolve,reject)=>{
    const id=String(++sequence);
    const timer=setTimeout(()=>{
      pending.delete(id);
      reject(new ApiError('等待电脑响应超时，结果可能尚未确认',0,'timeout'));
    },timeout);
    pending.set(id,{resolve,reject,timer});
    try { window.Pocket[method](id,...args); }
    catch(e) { clearTimeout(timer);pending.delete(id);reject(new ApiError(e.message || '原生连接不可用',0,'native_error')); }
  });
}
export function getConnection() {
  if(native()) {
    try { connection={...connection,...decoded(window.Pocket.getConnection())}; }
    catch { connection={paired:false,url:'',deviceName:''}; }
  } else {
    try {
      const value=JSON.parse(store.get('pocket.connection') || 'null');
      connection=value && value.token ? {...value,paired:true} : {paired:false,url:'',deviceName:''};
    } catch { connection={paired:false,url:'',deviceName:''}; }
  }
  return {paired:!!connection.paired,url:connection.url || '',deviceName:connection.deviceName || ''};
}
export function isNative() {return !!native();}
function normalizePayload(value) {
  const p=typeof value==='string'?JSON.parse(value):value;
  if(!p || p.type!=='codex-pocket-pair' || p.version!==1 || typeof p.ticket!=='string' || p.ticket.length<16 || p.ticket.length>2048) throw new ApiError('这不是有效的 Codex Pocket 配对码',0,'invalid_pairing');
  const u=new URL(p.url);
  if(u.username || u.password || u.search || u.hash || !['https:','http:'].includes(u.protocol)) throw new ApiError('配对服务地址不符合要求',0,'invalid_url');
  if(u.protocol==='http:' && !native()) {
    const local=/^(localhost|127(?:\.\d+){3}|\[::1\]|10(?:\.\d+){3}|192\.168(?:\.\d+){2}|172\.(1[6-9]|2\d|3[01])(?:\.\d+){2})$/;
    if(!local.test(u.hostname) || !['localhost','127.0.0.1','[::1]'].includes(location.hostname)) throw new ApiError('请使用 HTTPS 配对服务',0,'https_required');
  }
  return {...p,url:u.href.replace(/\/$/,'')};
}
export function cancelPairing(){unsubscribe();pairingEpoch++;connectionEpoch++;candidate=null;activeClaim=null;if(native()&&typeof window.Pocket.cancelPairing==='function'){try{window.Pocket.cancelPairing();}catch{}}}
export function getConnectionEpoch(){return connectionEpoch;}
function staleError(pairing=false){return new ApiError(pairing?'这轮配对已取消或被新配对替代':'连接已变化，已忽略旧连接的响应',0,pairing?'stale_pairing':'stale_connection');}
function connectionContext(){const c=getConnection();return {epoch:connectionEpoch,url:c.url,paired:c.paired};}
function nativeClearedOriginal(context){
  if(!native() || !context.paired || !context.url || context.epoch!==connectionEpoch)return false;
  try{
    const now=decoded(window.Pocket.getConnection());
    // A missing/failed bridge read is not proof of revocation. Native deliberately
    // clears the current credential before delivering its unauthorized signal.
    return now?.paired===false && typeof now.url==='string' && now.url==='';
  }catch{return false;}
}
function assertContext(context){const now=getConnection();if(context.epoch!==connectionEpoch||context.url!==now.url||context.paired!==now.paired)throw staleError();}
export function preparePairing(raw) {
  const p=normalizePayload(raw);
  cancelPairing();
  if(native()) {
    if(typeof window.Pocket.preparePairing==='function') {
      const answer=decoded(window.Pocket.preparePairing(JSON.stringify(p)));
      if(answer.error) throw fail(answer,400);
      if(answer.url) p.url=answer.url;
    } else if(typeof window.Pocket.setServer==='function') {
      window.Pocket.setServer(p.url);
    } else throw new ApiError('当前 App 版本不支持配对，请更新',0,'unsupported_pairing');
  }
  candidate=p;
  return {url:p.url,version:p.version};
}
export function scanPairing() {
  if(!native() || typeof window.Pocket.scanPairing!=='function') throw new ApiError('当前浏览器不能扫码，请粘贴电脑端配对内容',0,'scanner_unavailable');
  window.Pocket.scanPairing();
}
async function apiRaw(method,path,body,options={}) {
  if(!/^\/(api|pair)\//.test(path) || path.includes('..') || path.includes('\\')) throw new ApiError('不支持的请求路径',0,'invalid_path');
  const pairing=path.startsWith('/pair/');
  let response;
  if(native()) response=await invoke('request',[method,path,body==null?'':JSON.stringify(body)],options.timeout || 45000);
  else {
    const c=getConnection();
    const base=pairing?candidate?.url:c.url;
    if(!base) throw new ApiError('请先配对你的电脑',401,'not_paired');
    const headers={'Accept':'application/json'};
    if(body!=null) headers['Content-Type']='application/json';
    if(!pairing && connection.token) headers.Authorization='Bearer '+connection.token;
    const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),options.timeout || 45000);
    try {
      const r=await fetch(base+path,{method,headers,body:body==null?undefined:JSON.stringify(body),signal:controller.signal,credentials:'omit',redirect:'error'});
      const text=await r.text();const data=decoded(text);
      if(!r.ok) throw fail(data,r.status);
      response=data;
    } catch(e) {
      if(e instanceof ApiError) throw e;
      throw new ApiError(e.name==='AbortError'?'等待电脑响应超时，结果可能尚未确认':'无法连接服务，请检查网络和电脑',0,e.name==='AbortError'?'timeout':'network_error');
    } finally {clearTimeout(timer);}
  }
  return response;
}
export async function api(method,path,body,options={}) {
  const pairing=path.startsWith('/pair/');
  const context=connectionContext();
  const attempt={epoch:pairingEpoch,url:candidate?.url,requestId:body?.request_id};
  const assertPairing=()=>{if(attempt.epoch!==pairingEpoch||attempt.url!==candidate?.url||!candidate)throw staleError(true);if(path==='/pair/status'&&(!activeClaim||activeClaim.id!==attempt.requestId||activeClaim.epoch!==attempt.epoch||activeClaim.url!==attempt.url))throw staleError(true);};
  if(pairing)assertPairing();
  let response;
  try{response=await apiRaw(method,path,body,options);}
  catch(error){if(pairing)assertPairing();else if(!(error.status===401&&nativeClearedOriginal(context)))assertContext(context);throw error;}
  if(pairing)assertPairing();else assertContext(context);
  if(path==='/pair/claim')activeClaim={id:response.request_id,epoch:attempt.epoch,url:attempt.url};
  if(path==='/pair/status' && response.status==='approved') {
    if(!native()) {
      if(!response.token)throw new ApiError('配对完成但未收到访问凭据',502,'invalid_pairing_response');
      connection={paired:true,url:attempt.url,deviceName:options.deviceName||'浏览器',token:response.token};
      store.put('pocket.connection',JSON.stringify(connection));delete response.token;
    }else{
      if(typeof window.Pocket.finishPairing==='function')window.Pocket.finishPairing();
      getConnection();
    }
    connectionEpoch++;
  }
  return response;
}

async function uploadRaw(file) {
  if(native()) {
    const bytes=await file.arrayBuffer();const chunks=[];const view=new Uint8Array(bytes);
    for(let i=0;i<view.length;i+=8192) chunks.push(String.fromCharCode(...view.subarray(i,i+8192)));
    return invoke('upload',[file.type,btoa(chunks.join(''))],90000);
  }
  const c=getConnection();if(!c.paired) throw new ApiError('请先配对电脑',401,'not_paired');
  const r=await fetch(c.url+'/api/media',{method:'POST',headers:{Authorization:'Bearer '+connection.token,'Content-Type':file.type},body:file,credentials:'omit',redirect:'error'});
  const body=decoded(await r.text());if(!r.ok) throw fail(body,r.status);return body;
}
async function imageURLRaw(id) {
  if(typeof id!=='string' || !/^[A-Za-z0-9_-]{1,150}$/.test(id)) throw new ApiError('图片标识无效',400,'invalid_media');
  if(native() && typeof window.Pocket.media==='function') {
    const body=await invoke('media',[id],60000);
    if(typeof body.data_url!=='string' || !/^data:image\/(png|jpeg|webp|gif);base64,/.test(body.data_url)) throw new ApiError('图片内容无效',502,'invalid_media');
    return {url:body.data_url,bytes:Math.ceil((body.data_url.length-body.data_url.indexOf(',')-1)*.75)};
  }
  if(native()) return {url:location.origin+'/api/media/'+encodeURIComponent(id),bytes:0};
  const c=getConnection();const r=await fetch(c.url+'/api/media/'+encodeURIComponent(id),{headers:{Authorization:'Bearer '+connection.token},credentials:'omit',redirect:'error'});
  if(!r.ok) throw fail(decoded(await r.text()),r.status);
  if(!/^image\/(png|jpeg|webp|gif)(;|$)/.test(r.headers.get('Content-Type')||'')) throw new ApiError('图片类型不支持',502,'invalid_media');
  const blob=await r.blob();return {url:URL.createObjectURL(blob),bytes:blob.size};
}
export async function upload(file){const context=connectionContext();try{const result=await uploadRaw(file);assertContext(context);return result;}catch(error){if(!(error.status===401&&nativeClearedOriginal(context)))assertContext(context);throw error;}}
export async function imageURL(id){const context=connectionContext();let result;try{result=await imageURLRaw(id);assertContext(context);recordMediaSize(id,result.bytes);return result.url;}catch(error){if(result?.url?.startsWith('blob:'))URL.revokeObjectURL(result.url);if(!(error.status===401&&nativeClearedOriginal(context)))assertContext(context);throw error;}}
export function disconnect() {
  unsubscribe();cancelPairing();
  if(native()) { try { window.Pocket.disconnect(); } catch {} }
  else store.remove('pocket.connection');
  candidate=null;connection={paired:false,url:'',deviceName:''};
}
export function unsubscribe() {
  streamGeneration++;
  if(abortStream) {abortStream.abort();abortStream=null;}
  if(native()) {try {window.Pocket.unsubscribe();} catch {}}
}
export function subscribe(handler,after=0) {
  unsubscribe();const streamContext=connectionContext();const current=()=>{const now=getConnection();return streamContext.epoch===connectionEpoch&&streamContext.url===now.url&&streamContext.paired===now.paired;};eventHandler=event=>{
    if(native() && event.type==='unauthorized'){
      // Do not let a late old-stream event sign out a newly paired device. An
      // actual native revoke is actionable only after the original grant is gone.
      if(nativeClearedOriginal(streamContext))handler(event);
      return;
    }
    if(current())handler(event);
  };
  if(native()) {
    try {window.Pocket.subscribe();} catch(e) {handler({type:'disconnected',message:e.message});}
    return;
  }
  const generation=++streamGeneration;
  const sleep=ms=>new Promise(r=>setTimeout(r,ms));
  (async()=>{
    let retry=1000;let cursor=after;
    while(generation===streamGeneration && current() && getConnection().paired) {
      const controller=new AbortController();abortStream=controller;
      try {
        const r=await fetch(connection.url+'/api/events?after='+encodeURIComponent(cursor),{headers:{Authorization:'Bearer '+connection.token,Accept:'text/event-stream'},signal:controller.signal,credentials:'omit',redirect:'error'});
        if(!current()||generation!==streamGeneration)return;
        if(r.status===401) {disconnect();handler({type:'unauthorized'});return;}
        if(!r.ok || !r.body) throw new Error('实时连接暂不可用');
        retry=1000;handler({type:'connected'});
        const reader=r.body.getReader();const decoder=new TextDecoder();let buffer='';
        while(generation===streamGeneration && current()) {
          const {done,value}=await reader.read();if(done) break;
          buffer+=decoder.decode(value,{stream:true});
          if(buffer.length>262144) throw new Error('实时消息过大');
          let split;
          while((split=buffer.indexOf('\n\n'))!==-1) {
            const block=buffer.slice(0,split);buffer=buffer.slice(split+2);
            let data='';let kind='update';
            for(const line of block.split('\n')) {
              if(line.startsWith('id:')) cursor=line.slice(3).trim();
              else if(line.startsWith('event:')) kind=line.slice(6).trim();
              else if(line.startsWith('data:')) data+=line.slice(5).trim();
            }
            if(data) {try {handler({...JSON.parse(data),type:kind});} catch {}}
          }
        }
        if(generation===streamGeneration && current()) handler({type:'disconnected'});
      } catch(e) {
        if(generation!==streamGeneration || !current() || e.name==='AbortError') return;
        handler({type:'disconnected'});
      }
      if(generation!==streamGeneration||!current()) return;
      await sleep(retry);retry=Math.min(30000,retry*2);
    }
  })();
}

// Route selection belongs to the native client. The logical server origin remains
// stable while active_url changes, so probing never advances a connection epoch.
const routeKinds=new Set(['lan','tailscale','private','public']);
function networkError(value){return typeof value==='string'?value:(value?.message || '网络状态暂时不可用');}
function cleanNetworkStatus(raw){
  const value=decoded(raw);
  if(value.supported!==true)return {supported:false,error:value.error?networkError(value.error):'当前客户端不支持多线路'};
  if(!['auto','manual'].includes(value.mode))return {supported:false,error:'网络状态格式无效，请更新客户端'};
  return {
    auto_sync:value.auto_sync===true,discovery_supported:value.discovery_supported===true,discovery_state:['disabled','idle','syncing','searching','ready','error'].includes(value.discovery_state)?value.discovery_state:'disabled',last_sync:typeof value.last_sync==='number'?value.last_sync:null,revision:Number.isInteger(value.revision)?value.revision:null,discovery_error:typeof value.discovery_error==='string'?value.discovery_error:'',
    supported:true,mode:value.mode,active_url:typeof value.active_url==='string'?value.active_url:'',
    manual_url:typeof value.manual_url==='string'?value.manual_url:'',
    multi_route:value.multi_route===true,identity_verified:value.identity_verified===true,
    development_http:value.development_http===true,probing:value.probing===true,
    error:value.error?networkError(value.error):'',
    endpoints:(Array.isArray(value.endpoints)?value.endpoints:[]).slice(0,6).filter(e=>e&&typeof e.url==='string').map(e=>{
      const result={managed:e.managed===true,source:e.source==='auto'?'auto':'manual',tls:e.tls==='pinned'?'pinned':'system',url:e.url,label:typeof e.label==='string'?e.label:'',kind:routeKinds.has(e.kind)?e.kind:'private',status:['available','unreachable','identity_mismatch','unverified'].includes(e.status)?e.status:'unverified',active:e.active===true};
      if(result.status==='available'&&typeof e.latency_ms==='number'&&Number.isFinite(e.latency_ms)&&e.latency_ms>=0)result.latency_ms=e.latency_ms;
      return result;
    }),
  };
}
export function getNetworkStatus(){
  if(!native()||typeof window.Pocket.getNetworkStatus!=='function')return {supported:false,error:native()?'当前 App 版本不支持多线路，请更新后使用':'浏览器备用入口不支持自动选路，请使用手机 App'};
  try{return cleanNetworkStatus(window.Pocket.getNetworkStatus());}catch(error){return {supported:false,error:messageForNetwork(error)};}
}
function messageForNetwork(error){return error?.message || '网络状态读取失败，请稍后重试';}
function networkConfig(raw){
  if(!raw||!['auto','manual'].includes(raw.mode)||!Array.isArray(raw.endpoints)||raw.endpoints.length<1||raw.endpoints.length>6)throw new ApiError('请配置 1 到 6 个地址，并选择连接模式',400,'bad_network_config');
  const urls=new Set();const endpoints=raw.endpoints.map(e=>{
    if(!e||typeof e.url!=='string'||typeof e.label!=='string'||e.label.trim().length>40||!routeKinds.has(e.kind))throw new ApiError('线路名称、地址或类型无效',400,'bad_network_config');
    const u=new URL(e.url);
    if(!['http:','https:'].includes(u.protocol)||u.username||u.password||u.search||u.hash||!['','/'].includes(u.pathname))throw new ApiError('请填写不含账号、路径和参数的服务根地址',400,'bad_network_url');
    const url=u.origin;if(urls.has(url))throw new ApiError('这个地址已在列表中',400,'duplicate_endpoint');urls.add(url);
    return {url,label:e.label.trim(),kind:e.kind,managed:e.managed===true,source:e.managed===true&&e.source==='auto'?'auto':'manual',tls:e.managed===true&&e.tls==='pinned'?'pinned':'system'};
  });
  const result={mode:raw.mode,endpoints,auto_sync:raw.auto_sync===true};
  if(raw.mode==='manual'){
    let manual;try{manual=new URL(raw.manual_url).origin;}catch{throw new ApiError('请选择要固定的地址',400,'manual_endpoint_required');}
    if(!urls.has(manual))throw new ApiError('固定地址不在当前列表中',400,'manual_endpoint_required');result.manual_url=manual;
  }
  return result;
}
export async function configureNetworks(config){
  if(!native()||typeof window.Pocket.configureNetworks!=='function')throw new ApiError('当前客户端不支持网络设置',0,'networks_unavailable');
  const context=connectionContext();const safe=networkConfig(config);
  try{const result=await invoke('configureNetworks',[JSON.stringify(safe)],60000);assertContext(context);return cleanNetworkStatus(result);}catch(error){assertContext(context);throw error;}
}
export async function probeNetworks(){
  if(!native()||typeof window.Pocket.probeNetworks!=='function')throw new ApiError('当前客户端不支持线路检测',0,'networks_unavailable');
  const context=connectionContext();
  try{const result=await invoke('probeNetworks',[],60000);assertContext(context);return cleanNetworkStatus(result);}catch(error){assertContext(context);throw error;}
}

export function previewConfigLink(raw){
  if(!native()||typeof window.Pocket.previewConfigLink!=='function')throw new ApiError('请更新手机 App 后导入配置链接',0,'config_unavailable');
  const result=decoded(window.Pocket.previewConfigLink(raw));if(result.error)throw fail(result,400);return result;
}
export async function importConfigLink(raw){
  if(!native()||typeof window.Pocket.importConfigLink!=='function')throw new ApiError('当前客户端不支持配置链接',0,'config_unavailable');
  const context=connectionContext();const result=await invoke('importConfigLink',[raw],60000);assertContext(context);return result;
}
