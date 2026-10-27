"""Small authenticated HTTP bridge; desktop IPC is kept behind an adapter."""
from .private_files import ensure_private_dir, replace_private, PrivatePathError
import asyncio
import base64
import collections
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit
from aiohttp import web
from PIL import Image, UnidentifiedImageError
from . import __version__
from .store import Fault, Store
from .network import ServerIdentity, endpoints, canonical_origin
from .configlinks import ConfigLinks
from .resources import admin_resource

MAX_IMAGE = 15 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000


def js(value, status=200):
    return web.json_response(value, status=status, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})


async def read_limited(request, limit):
    chunks, size = [], 0
    async for chunk in request.content.iter_chunked(65536):
        size += len(chunk)
        if size > limit:
            raise Fault('too_large', '请求内容过大', 413)
        chunks.append(chunk)
    return b''.join(chunks)


async def body_json(request, max_bytes=131072):
    if request.content_type != 'application/json':
        raise Fault('bad_content_type', '请使用JSON请求')
    if request.content_length is not None and request.content_length > max_bytes:
        raise Fault('too_large', '请求内容过大', 413)
    raw = await read_limited(request, max_bytes)
    if len(raw) > max_bytes:
        raise Fault('too_large', '请求内容过大', 413)
    def unique(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError('duplicate key')
            d[k] = v
        return d
    try:
        obj = json.loads(raw, object_pairs_hook=unique)
        if not isinstance(obj, dict):
            raise ValueError()
        return obj
    except (ValueError, UnicodeError):
        raise Fault('bad_json', 'JSON格式无效')


class Hub:
    def __init__(self, directory, adapter, controller_id=None, name='我的电脑'):
        self.store = Store(directory)
        self.identity = ServerIdentity(self.store.directory)
        self.discovery = None
        self.config_links = ConfigLinks(self)
        self.adapter, self.name = adapter, name
        if controller_id:
            self.store.set_setting('controller_id', controller_id)
        self.state = {'threads': [], 'messages': [], 'requests': [], 'controller': None, 'capabilities': {k: False for k in ['chat', 'images', 'live', 'approvals', 'user_input', 'interrupt']}}
        self.revision = 0
        self.watchers = {}
        self.refresh_lock = asyncio.Lock()
        self.write_lock = asyncio.Lock()
        self.image_semaphore = asyncio.Semaphore(2)
        self.refresh_pending = None
        self.refresh_requested = False
        self.rate = collections.OrderedDict()
        self.media_dir = self.store.directory / 'media'
        ensure_private_dir(self.media_dir)
        self.stopping = False
        self.last_adapter_error = None
        self.outbound_media_roots = []

    def limited(self, key, maximum, seconds=60):
        now = time.monotonic()
        bucket = self.rate.get(key, collections.deque())
        while bucket and bucket[0] < now - seconds:
            bucket.popleft()
        if len(bucket) >= maximum:
            raise Fault('rate_limited', '操作过于频繁，请稍后再试', 429)
        bucket.append(now)
        self.rate[key] = bucket
        self.rate.move_to_end(key)
        while len(self.rate) > 2048:
            self.rate.popitem(last=False)

    async def start(self):
        await self.adapter.start(self.changed)
        await self.refresh()

    async def close(self):
        self.stopping = True
        if self.refresh_pending:
            self.refresh_pending.cancel()
            await asyncio.gather(self.refresh_pending, return_exceptions=True)
        for queues in self.watchers.values():
            for queue in queues:
                self._notify(queue, None)
        await self.adapter.close()
        self.store.close()

    async def changed(self):
        if self.stopping:
            return
        self.refresh_requested = True
        if not self.refresh_pending or self.refresh_pending.done():
            self.refresh_pending = asyncio.create_task(self._debounced())

    async def _debounced(self):
        while self.refresh_requested and not self.stopping:
            # One worker and one pending bit preserve the final event of a burst,
            # including events arriving while the preceding read is in flight.
            await asyncio.sleep(.08)
            if self.stopping:
                return
            self.refresh_requested = False
            await self.refresh()

    def _notify(self, queue, value):
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(value)

    def emit(self):
        self.revision += 1
        for queues in self.watchers.values():
            for queue in queues:
                self._notify(queue, self.revision)

    async def refresh(self):
        async with self.refresh_lock:
            try:
                state = await self.adapter.snapshot(self.store.setting('controller_id'), self.store.setting('cache_limit', 30))
                # Never serialize adapter internal provenance or arbitrary fields.
                result = {k: state.get(k, self.state.get(k)) for k in ('threads', 'messages', 'requests', 'controller', 'capabilities')}
                result['threads'] = [{k: t.get(k) for k in ('id', 'title', 'status')} for t in (result['threads'] or [])[:50]]
                result['messages'] = self.safe_messages(result.get('messages') or [])
                result['requests'] = [self.safe_request(r) for r in (result['requests'] or [])[:100]]
                result['controller'] = {k: result['controller'].get(k) for k in ('id', 'title')} if result.get('controller') else None
                result['capabilities'] = {k: bool((result.get('capabilities') or {}).get(k, False)) for k in ('chat','images','live','approvals','user_input','interrupt')}
                self.last_adapter_error = getattr(self.adapter,'last_error',None)
            except Exception:
                # Keep visible cache but make stale transport explicit; no exception text leaks paths.
                result = dict(self.state)
                result['capabilities'] = dict(result['capabilities'], chat=False, live=False, approvals=False, user_input=False)
                self.last_adapter_error = '桌面连接暂不可用，正在保留最近内容'
            if result != self.state:
                self.state = result
                self.emit()

    def safe_request(self, r):
        fields = ('id','thread_id','thread_title','type','title','body','options','questions','status','created_at')
        result = {k: r.get(k) for k in fields if k in r}
        result['options'] = [{k: o.get(k) for k in ('id','label','description') if k in o} for o in result.get('options', []) if isinstance(o, dict)]
        result['questions'] = [{k:q.get(k) for k in ('id','question','options','is_multiple','allow_other','is_secret') if k in q} for q in result.get('questions', []) if isinstance(q, dict)]
        return result

    def safe_messages(self, messages):
        total, output = 0, []
        limit = self.store.setting('cache_limit', 30)
        for m in reversed(messages[-limit:]):
            text = str(m.get('text') or '')[:24000]
            if total + len(text.encode()) > 350000:
                break
            total += len(text.encode())
            images = []
            for image in m.get('images', [])[:4]:
                mid = image.get('id')
                if mid and self.store.db.execute('SELECT 1 FROM media WHERE id=?', (mid,)).fetchone():
                    images.append({'id':mid,'url':'/api/media/'+mid,'mime':image.get('mime','image/png'),'thumbnail_id':self.ensure_thumbnail(mid)})
                elif image.get('_path'):
                    registered = self.register_output(image['_path'])
                    if registered:
                        images.append(registered)
            output.append({'id':str(m.get('id','')), 'role':m.get('role') if m.get('role') in ('user','assistant') else 'assistant', 'text':text,'images':images,'created_at':m.get('created_at')})
        return list(reversed(output))

    def ensure_thumbnail(self, mid):
        tid = mid + '-thumb'
        row = self.store.db.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()
        if not row:
            return None
        if self.store.db.execute('SELECT 1 FROM media WHERE id=?',(tid,)).fetchone():
            return tid
        try:
            with Image.open(row['path']) as im:
                im.thumbnail((720,720))
                image = im.convert('RGB')
                out = io.BytesIO()
                image.save(out,format='JPEG',quality=80)
                data = out.getvalue()
            if len(data)>600000:
                return None
            target = self.media_dir/(tid+'.jpg')
            replace_private(target,data)
            self.store.db.execute('INSERT INTO media VALUES(?,?,?,?,?,?,1)',(tid,row['device_id'],'image/jpeg',len(data),str(target),time.time()))
            return tid
        except (OSError,ValueError):
            return None

    def register_output(self, raw_path):
        # Exact desktop-reported image paths still need an administrator-approved root.
        try:
            path = Path(raw_path).resolve(strict=True)
            roots = [self.media_dir.resolve()] + [Path(p).resolve() for p in self.outbound_media_roots]
            if not any(path.is_relative_to(root) for root in roots) or not path.is_file() or path.stat().st_size > MAX_IMAGE:
                return None
            existing = self.store.db.execute('SELECT id,mime FROM media WHERE path=?',(str(path),)).fetchone()
            if existing:
                return {'id':existing['id'],'url':'/api/media/'+existing['id'],'mime':existing['mime'],'thumbnail_id':self.ensure_thumbnail(existing['id'])}
            mid = str(uuid.uuid5(uuid.NAMESPACE_URL, str(path)+':'+str(path.stat().st_mtime_ns)))
            row = self.store.db.execute('SELECT mime FROM media WHERE id=?', (mid,)).fetchone()
            if row:
                return {'id':mid,'url':'/api/media/'+mid,'mime':row['mime'],'thumbnail_id':self.ensure_thumbnail(mid)}
            raw = path.read_bytes()
            safe, mime = sanitize_image(raw)
            target = self.media_dir / (mid + ('.jpg' if mime=='image/jpeg' else '.png'))
            replace_private(target,safe)
            self.store.db.execute('INSERT INTO media VALUES(?,?,?,?,?,?,1)', (mid,'desktop',mime,len(safe),str(target),time.time()))
            return {'id':mid,'url':'/api/media/'+mid,'mime':mime,'thumbnail_id':self.ensure_thumbnail(mid)}
        except (OSError, ValueError, Fault):
            return None

    def public_state(self, device):
        return dict(self.state, protocol=1, revision=self.revision,
                    server={'name':self.name,'version':__version__,'discovery_revision':self.discovery.revision if self.discovery else 0},device={'id':device['id'],'name':device['name']},
                    settings={'cache_limit':self.store.setting('cache_limit',30)}, connection_error=self.last_adapter_error)

    def auth(self, request):
        header = request.headers.get('Authorization','')
        return self.store.authenticate(header[7:] if header.startswith('Bearer ') else '')


def sanitize_image(raw):
    if len(raw) > MAX_IMAGE:
        raise Fault('image_too_large', '单张图片最多15MB', 413)
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format not in ('PNG','JPEG','WEBP','GIF') or image.width * image.height > 40_000_000:
                raise ValueError()
            image.seek(0)
            image.load()
            # Re-encode a still frame without EXIF, scripts or untrusted metadata.
            mode = 'RGBA' if 'A' in image.getbands() else 'RGB'
            clean = Image.new(mode, image.size)
            clean.paste(image.convert(mode))
            output = io.BytesIO()
            if image.format == 'JPEG':
                clean.convert('RGB').save(output, format='JPEG', quality=95)
                mime = 'image/jpeg'
            else:
                clean.save(output, format='PNG', optimize=False)
                mime = 'image/png'
            if output.tell() > MAX_IMAGE:
                raise ValueError()
            return output.getvalue(), mime
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise Fault('bad_image', '无法读取图片，请使用正常的PNG/JPEG/WebP图片')


@web.middleware
async def errors(request, handler):
    try:
        return await handler(request)
    except Fault as exc:
        return js({'error':{'code':exc.code,'message':exc.message}}, exc.status)
    except PrivatePathError:
        return js({'error':{'code':'private_storage_unavailable','message':'私有存储权限异常，请在电脑检查数据目录'}},503)
    except web.HTTPException:
        raise
    except (asyncio.CancelledError, ConnectionResetError):
        raise
    except Exception:
        return js({'error':{'code':'internal_error','message':'服务暂时无法完成请求，请保留发送编号后核对'}},500)


@web.middleware
async def origin_policy(request, handler):
    origin = request.headers.get('Origin')
    allowed = request.app['origins']
    own = str(request.url.origin())
    if origin and origin not in allowed and origin != own:
        raise Fault('origin_forbidden','请求来源不受支持',403)
    if request.method == 'OPTIONS':
        response = web.Response(status=204)
    else:
        response = await handler(request)
    if origin and (origin in allowed or origin == own):
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Headers'] = 'Authorization,Content-Type,Last-Event-ID'
        response.headers['Access-Control-Allow-Methods'] = 'GET,POST,DELETE,OPTIONS'
        response.headers['Vary'] = 'Origin'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


def create_app(hub, web_root=None, extra_origins=()):
    app = web.Application(client_max_size=MAX_IMAGE+1024, middlewares=[errors,origin_policy])
    app['hub'], app['origins'] = hub, {'https://appassets.androidplatform.net',*extra_origins}

    async def health(request):
        return js({'ok':True,'protocol':1})

    async def identity(request):
        hub.limited(('identity', request.remote), 300)
        if len(request.query.getall('challenge',[]))!=1 or len(request.query.getall('origin',[]))!=1:
            raise Fault('bad_challenge','身份检测参数无效')
        return js(hub.identity.proof(request.query['challenge'],request.query['origin'],hub.store.setting('network_endpoints',[])))

    async def discovery(request):
        hub.limited(('discovery',request.remote),300)
        if hub.discovery is None:raise Fault('discovery_unavailable','此服务尚未启用自动发现',503)
        if len(request.query.getall('challenge',[]))!=1:raise Fault('bad_challenge','发现请求参数无效')
        return js(hub.discovery.proof(request.query['challenge']))

    async def config_profile(request):
        hub.limited(('config-get-source', request.remote), 30)
        config_id = request.match_info['config_id']
        hub.store.require_config_link(config_id)
        hub.limited(('config-get-global',), 120)
        hub.limited(('config-get-link', config_id), 60)
        if len(request.query.getall('challenge', [])) != 1:
            raise Fault('bad_challenge', '配置请求参数无效')
        return js(hub.config_links.get(config_id, request.query['challenge']))

    async def config_pair(request):
        hub.limited(('config-pair-source', request.remote), 4)
        config_id = request.match_info['config_id']
        hub.store.require_config_link(config_id)
        hub.limited(('config-pair-global',), 10)
        hub.limited(('config-pair-link', config_id), 6)
        b = await body_json(request, max_bytes=1024)
        if set(b) != {'challenge'}:
            raise Fault('bad_challenge', '配置请求参数无效')
        return js(hub.config_links.pair(config_id, b['challenge']))

    async def claim(request):
        hub.limited(('pair',request.remote),15)
        b = await body_json(request)
        return js(hub.store.claim(b.get('ticket'),b.get('device_name'),b.get('client_nonce')))

    async def pair_status(request):
        hub.limited(('pair-status',request.remote),180)
        b = await body_json(request)
        result = hub.store.pair_status(b.get('request_id'),b.get('poll_token'))
        result['server_name'] = hub.name
        result['server_identity'] = hub.identity.public()
        result['endpoints'] = hub.store.setting('network_endpoints',[])
        if hub.discovery:result['discovery']=hub.discovery.options()
        return js(result)

    async def state(request):
        device = hub.auth(request)
        hub.limited(('state',device['id']),600)
        return js(hub.public_state(device))

    async def events(request):
        device = hub.auth(request)
        did = device['id']
        queues = hub.watchers.setdefault(did, set())
        if len(queues) >= 3:
            raise Fault('streams_limit','当前设备连接过多',429)
        queue = asyncio.Queue(maxsize=1)
        queues.add(queue)
        response = web.StreamResponse(headers={'Content-Type':'text/event-stream','Cache-Control':'no-cache, no-transform','X-Accel-Buffering':'no'})
        revision = hub.revision
        try:
            await response.prepare(request)
            await response.write(('id: %s\nevent: update\ndata: %s\n\n'%(revision,json.dumps({'revision':revision,'discovery_revision':hub.discovery.revision if hub.discovery else 0}))).encode())
            while not hub.stopping:
                # Check revocation both on events and heartbeat, then close proactively.
                if not any(d['id']==did for d in hub.store.devices()):
                    break
                try:
                    value = await asyncio.wait_for(queue.get(),15)
                    if value is None:
                        break
                    await response.write(('id: %s\nevent: update\ndata: %s\n\n'%(value,json.dumps({'revision':value,'discovery_revision':hub.discovery.revision if hub.discovery else 0}))).encode())
                except asyncio.TimeoutError:
                    await response.write(b': heartbeat\n\n')
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            queues.discard(queue)
            if not queues:
                hub.watchers.pop(did,None)
        return response

    async def send(request):
        device = hub.auth(request)
        hub.limited(('write',device['id']),30)
        b = await body_json(request)
        if set(b)-{'client_id','controller_id','text','attachments'}:
            raise Fault('bad_fields','不支持的发送参数')
        text, mids = b.get('text',''), b.get('attachments',[])
        if not isinstance(text,str) or len(text)>24000 or not isinstance(mids,list) or len(mids)>4 or not(text.strip() or mids):
            raise Fault('bad_message','消息为空或过长')
        tid = hub.store.setting('controller_id')
        if not tid or not hub.state['capabilities'].get('chat'):
            raise Fault('desktop_unavailable','请先选择可用的总控会话',409)
        attachments = []
        for mid in mids:
            row = hub.store.db.execute('SELECT * FROM media WHERE id=? AND device_id=?',(str(mid),device['id'])).fetchone()
            if not row:
                raise Fault('bad_media','图片不属于当前设备或已过期',404)
            attachments.append({'id':row['id'],'path':row['path'],'mime':row['mime']})
        async with hub.write_lock:
            device = hub.auth(request)
            if not isinstance(b.get('controller_id'), str) or b['controller_id'] != hub.store.setting('controller_id'):
                raise Fault('controller_changed', '总控已改变，请刷新确认目标后再发送', 409)
            tid = b['controller_id']
            duplicate = hub.store.reserve(device['id'],b.get('client_id'),'message',tid,{'text':text,'attachments':mids})
            if duplicate:
                if duplicate['status']=='rejected':
                    raise Fault('delivery_rejected','此发送已被明确拒绝，未派发；请核对目标后重新发送',409)
                return js(duplicate)
            try:
                result = await asyncio.wait_for(hub.adapter.send(tid,text,attachments),40)
                status = result.get('status','unknown') if isinstance(result,dict) else result
                status = status if status=='delivered' else 'unknown'
            except Exception as exc:
                code = getattr(exc,'code',None)
                if code in {'invalid_thread','stale_request','invalid_answer','thread_busy','images_unavailable','invalid_media','stale_thread','unsupported'}:
                    hub.store.finish(device['id'],b['client_id'],'rejected')
                    raise Fault(code,str(exc),409)
                status = 'unknown'
            hub.store.finish(device['id'],b['client_id'],status)
            for mid in mids:
                hub.store.db.execute('UPDATE media SET used=1 WHERE id=?',(mid,))
        await hub.refresh()
        return js({'status':status,'client_id':b['client_id']})

    async def answer(request):
        device = hub.auth(request)
        hub.limited(('write',device['id']),30)
        b = await body_json(request)
        rid = request.match_info['id']
        if set(b)-{'client_id','choice_id','answers'}:
            raise Fault('bad_fields','不支持的回答参数')
        async with hub.write_lock:
            device = hub.auth(request)
            # A duplicate known delivery may be retried after the native card disappears.
            duplicate = hub.store.db.execute('SELECT 1 FROM deliveries WHERE device_id=? AND client_id=?',(device['id'],str(b.get('client_id')))).fetchone()
            if not duplicate:
                card = next((r for r in hub.state['requests'] if r.get('id')==rid and r.get('status')=='pending'),None)
                if not card:
                    raise Fault('request_expired','请求已处理或已失效，请刷新',409)
                if b.get('choice_id') is not None and b['choice_id'] not in [o['id'] for o in card.get('options',[])]:
                    raise Fault('bad_choice','选项已失效或无效',409)
                if b.get('answers') is not None and not isinstance(b['answers'],dict):
                    raise Fault('bad_answers','回答格式无效')
            old = hub.store.reserve(device['id'],b.get('client_id'),'answer',rid,{k:v for k,v in b.items() if k!='client_id'})
            if old:
                if old['status']=='rejected':
                    raise Fault('delivery_rejected','这个回答已被明确拒绝，请刷新原请求',409)
                return js(old)
            try:
                result = await asyncio.wait_for(hub.adapter.answer(rid,b.get('choice_id'),b.get('answers')),20)
                status = result.get('status','unknown') if isinstance(result,dict) else result
                status = status if status=='delivered' else 'unknown'
            except Exception as exc:
                code = getattr(exc,'code',None)
                if code in {'invalid_thread','stale_request','invalid_answer','thread_busy','images_unavailable','invalid_media','stale_thread','unsupported'}:
                    hub.store.finish(device['id'],b['client_id'],'rejected')
                    raise Fault(code,str(exc),409)
                status = 'unknown'
            hub.store.finish(device['id'],b['client_id'],status)
        await hub.refresh()
        return js({'status':status,'client_id':b['client_id']})

    async def controller(request):
        device = hub.auth(request)
        hub.limited(('settings',device['id']),30)
        b = await body_json(request)
        tid = b.get('thread_id')
        if tid not in [t['id'] for t in hub.state['threads']]:
            raise Fault('unknown_thread','请选择当前可用的本机会话',404)
        async with hub.write_lock:
            device = hub.auth(request)
            hub.store.set_setting('controller_id',tid)
            await hub.refresh()
        hub.emit()
        return js(hub.public_state(device))

    async def settings(request):
        device = hub.auth(request)
        b = await body_json(request)
        value = b.get('cache_limit')
        if set(b) != {'cache_limit'} or type(value) is not int or value not in (15,30,60):
            raise Fault('bad_settings','最近内容可选15、30或60条')
        hub.store.set_setting('cache_limit',value)
        await hub.refresh()
        hub.emit()
        return js({'cache_limit':value})

    async def upload(request):
        device = hub.auth(request)
        hub.limited(('upload',device['id']),20)
        if request.content_type not in ('image/png','image/jpeg','image/webp','image/gif'):
            raise Fault('bad_image_type','只支持图片',415)
        usage = hub.store.db.execute('SELECT COALESCE(sum(size),0) FROM media').fetchone()[0]
        if usage > 250*1024*1024:
            raise Fault('media_full','图片缓存已满，请在电脑管理端清理',507)
        async with hub.image_semaphore:
            raw = await read_limited(request, MAX_IMAGE)
            safe,mime = await asyncio.to_thread(sanitize_image,raw)
        mid = str(uuid.uuid4())
        target = hub.media_dir / (mid+('.jpg' if mime=='image/jpeg' else '.png'))
        replace_private(target,safe)
        hub.store.db.execute('INSERT INTO media VALUES(?,?,?,?,?,?,0)',(mid,device['id'],mime,len(safe),str(target),time.time()))
        return js({'id':mid,'mime':mime,'size':len(safe),'thumbnail_id':hub.ensure_thumbnail(mid)})

    async def media(request):
        device = hub.auth(request)
        mid = request.match_info['id']
        row = hub.store.db.execute('SELECT * FROM media WHERE id=?',(mid,)).fetchone()
        visible = {mid for m in hub.state['messages'] for i in m.get('images',[]) for mid in (i['id'],i.get('thumbnail_id')) if mid}
        if not row or (row['device_id']!=device['id'] and mid not in visible):
            raise Fault('media_not_found','图片不可用',404)
        return web.FileResponse(row['path'],headers={'Content-Type':row['mime'],'Cache-Control':'private, max-age=300','X-Content-Type-Options':'nosniff'})

    async def cache(request):
        hub.auth(request)
        # Desktop history, unresolved requests and delivery ledger are never deleted here.
        hub.state = dict(hub.state,messages=[])
        hub.emit()
        return js({'ok':True})

    async def devices(request):
        device = hub.auth(request)
        return js({'devices':hub.store.devices(device['id'])})

    async def revoke(request):
        hub.auth(request)
        did = request.match_info['id']
        hub.store.revoke(did)
        for queue in hub.watchers.get(did,[]):
            hub._notify(queue,None)
        return js({'ok':True})

    app.add_routes([web.get('/config/{config_id}',config_profile),web.post('/config/{config_id}/pair',config_pair),web.get('/discovery',discovery),web.get('/identity',identity),web.get('/health',health),web.post('/pair/claim',claim),web.post('/pair/status',pair_status),web.get('/api/state',state),web.get('/api/events',events),web.post('/api/messages',send),web.post('/api/requests/{id}/answer',answer),web.post('/api/controller',controller),web.post('/api/settings',settings),web.post('/api/media',upload),web.get('/api/media/{id}',media),web.delete('/api/cache',cache),web.get('/api/devices',devices),web.delete('/api/devices/{id}',revoke)])
    if web_root:
        root = Path(web_root).resolve()
        async def static(request):
            name = request.match_info.get('name','index.html')
            if name not in ('index.html','app.js','style.css','transport.js','icons.js','icon.svg'):
                raise web.HTTPNotFound()
            path = root/name
            if not path.is_file():
                raise web.HTTPNotFound()
            response = web.FileResponse(path)
            response.headers.update({'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Content-Security-Policy':"default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'"})
            return response
        app.add_routes([web.get('/',static),web.get('/{name}',static)])
    return app


def create_admin_app(hub, admin_token):
    """Separate local management surface: never included in the phone route table."""
    @web.middleware
    async def admin_auth(request,handler):
        if request.path == '/' and request.method=='GET':
            return await handler(request)
        expected = 'Bearer '+admin_token
        if not hmac.compare_digest(request.headers.get('Authorization',''),expected):
            raise Fault('admin_forbidden','本机管理凭据无效',401)
        origin=request.headers.get('Origin')
        if origin and origin != str(request.url.origin()):
            raise Fault('origin_forbidden','管理操作来源无效',403)
        return await handler(request)
    app = web.Application(client_max_size=65536,middlewares=[errors,admin_auth])
    async def index(request):
        return web.FileResponse(admin_resource(),headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'self'; frame-ancestors 'none'"})
    async def status(request):
        return js({'pending':hub.store.pending_pairs(),'devices':hub.store.devices(),'state':{'controller':hub.state.get('controller'),'capabilities':hub.state.get('capabilities')},'name':hub.name,'server_identity':hub.identity.public(),'endpoints':hub.store.setting('network_endpoints',[]),'discovery':hub.discovery.status() if hub.discovery else {'enabled':False,'state':'unavailable','diagnostics':['自动发现未开启']},'config_link_enabled':hub.config_links.metadata()['enabled'],'config_link':hub.config_links.metadata(),'delivery':{'revision':hub.revision,'message_ids':[m.get('id') for m in hub.state.get('messages',[])],'subscribers':sum(len(qs) for qs in hub.watchers.values())},'manual_endpoints':hub.store.setting('manual_endpoints_v3',[e for e in hub.store.setting('network_endpoints',[]) if e.get('source')!='auto'])})
    async def config_link(request):
        b = await body_json(request, max_bytes=1024)
        if set(b) - {'rotate'} or 'rotate' in b and type(b['rotate']) is not bool:
            raise Fault('bad_config', '配置链接选项无效')
        if hub.discovery:
            await hub.discovery.refresh()
        return js(hub.config_links.admin_link(rotate=b.get('rotate', False)))

    async def discovery_preferences(request):
        if hub.discovery is None:
            raise Fault('discovery_unavailable', '自动发现未开启', 503)
        b = await body_json(request, max_bytes=4096)
        if set(b) != {'excluded_interfaces'} or not isinstance(b['excluded_interfaces'], list):
            raise Fault('bad_interfaces', '请选择要排除的本机接口')
        await hub.discovery.set_excluded_interfaces(b['excluded_interfaces'])
        return js({'ok': True, 'discovery': hub.discovery.status(), 'endpoints': hub.discovery.current})

    async def mint(request):
        import qrcode
        b=await body_json(request)
        automatic=b.get('auto',hub.discovery is not None)
        if automatic:
            if hub.discovery is None:raise Fault('discovery_unavailable','请使用新版服务并启用自动发现',503)
            if 'manual_endpoints' in b:await hub.discovery.set_manual(b['manual_endpoints'],allow_http=b.get('allow_http') is True)
            else:await hub.discovery.refresh()
            configured=list(hub.discovery.current)
            if not configured:raise Fault('discovery_unavailable','尚未找到可用地址，请检查电脑网络或添加可选公网地址',503)
            preferred=next((e for e in configured if e.get('kind')=='lan'),configured[0])
            url=preferred['url']
            # QR carries bounded connection seeds; the signed manifest supplies the complete list.
            stable=next((e for e in configured if e.get('kind')=='tailscale' and (urlsplit(e['url']).hostname or '').endswith('.ts.net')),None)
            public=next((e for e in configured if e.get('source')=='manual' and e.get('kind')=='public'),None)
            seeds=[]
            for entry in [preferred,stable,public,*[e for e in configured if e.get("kind")=="lan"],*configured]:
                if entry is not None and entry['url'] not in {e['url'] for e in seeds}:seeds.append(entry)
            configured=seeds[:3]
        else:
            configured=endpoints(b.get('endpoints'),primary=b.get('url'),allow_http=b.get('allow_http') is True)
            url=canonical_origin(b.get('url') or configured[0]['url'],allow_http=b.get('allow_http') is True)
        payload={'type':'codex-pocket-pair','version':1,'url':url,'ticket':'x'*43,'server_identity':hub.identity.public(),'endpoints':configured}
        if automatic:payload['discovery']=hub.discovery.options()
        encoded=json.dumps(payload,separators=(',',':'),ensure_ascii=False)
        while automatic and len(encoded.encode())>2400 and len(payload['endpoints'])>1:
            payload['endpoints'].pop();encoded=json.dumps(payload,separators=(',',':'),ensure_ascii=False)
        if len(encoded.encode())>2400:
            raise Fault('bad_endpoints','配对内容过长，请缩短线路地址或名称')
        pair=hub.store.mint_pair()
        payload['ticket']=pair['ticket']
        encoded=json.dumps(payload,separators=(',',':'),ensure_ascii=False)
        qr=qrcode.make(encoded,error_correction=qrcode.constants.ERROR_CORRECT_L)
        output=io.BytesIO();qr.save(output,format='PNG')
        if not automatic:hub.store.set_setting('network_endpoints',configured)
        return js({'request_id':pair['request_id'],'expires_at':pair['expires_at'],'qr_data_url':'data:image/png;base64,'+base64.b64encode(output.getvalue()).decode(),'payload':payload})
    async def network_config(request):
        b=await body_json(request)
        if b.get('auto',hub.discovery is not None):
            if hub.discovery is None:raise Fault('discovery_unavailable','自动发现未开启',503)
            await hub.discovery.set_manual(b.get('manual_endpoints',b.get('endpoints',[])),allow_http=b.get('allow_http') is True)
            return js({'ok':True,'endpoints':hub.discovery.current,'discovery':hub.discovery.status(),'manual_endpoints':hub.discovery._manual()})
        configured=endpoints(b.get('endpoints'),allow_http=b.get('allow_http') is True)
        hub.store.set_setting('network_endpoints',configured)
        return js({'ok':True,'endpoints':configured,'server_identity':hub.identity.public()})
    async def refresh_discovery(request):
        if hub.discovery is None:raise Fault('discovery_unavailable','自动发现未开启',503)
        await hub.discovery.refresh()
        return js({'ok':True,'endpoints':hub.discovery.current,'discovery':hub.discovery.status(),'manual_endpoints':hub.discovery._manual()})
    async def decide(request):
        b=await body_json(request)
        if type(b.get('approve')) is not bool:
            raise Fault('bad_decision','缺少明确允许或拒绝')
        return js(hub.store.decide_pair(request.match_info['id'],b['approve'],b.get('phrase')))
    async def revoke(request):
        did=request.match_info['id'];hub.store.revoke(did)
        for q in hub.watchers.get(did,[]):hub._notify(q,None)
        return js({'ok':True})
    app.add_routes([web.get('/',index),web.get('/admin/state',status),web.post('/admin/config-link',config_link),web.post('/admin/discovery/preferences',discovery_preferences),web.post('/admin/pair',mint),web.post('/admin/networks',network_config),web.post('/admin/discovery/refresh',refresh_discovery),web.post('/admin/pair/{id}/decision',decide),web.delete('/admin/devices/{id}',revoke)])
    return app
