import asyncio
import io
import json
import os
import uuid
from pathlib import Path
import pytest
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from server.app import Hub, create_app, create_admin_app
from server.store import Store, Fault

class FakeDesktop:
    def __init__(self):
        self.sent=[];self.answered=[];self.callback=None;self.pending=True;self.fail=False
    async def start(self,cb):self.callback=cb
    async def close(self):pass
    async def snapshot(self,cid,limit):
        return {'controller':{'id':'native-1','title':'总控'} if cid else None,
          'threads':[{'id':'native-1','title':'总控','status':'idle','secret':'NO'}],
          'messages':[{'id':'m1','role':'assistant','text':'准备好了','images':[],'secret':'NO'}] if cid else [],
          'requests':[{'id':'r1','thread_id':'native-1','thread_title':'总控','type':'command','title':'测试命令','body':'printf okay','options':[{'id':'accept','label':'允许一次','native':'NO'}],'questions':[],'status':'pending','native':{'secret':'NO'}}] if self.pending else [],
          'capabilities':{'chat':True,'images':True,'live':True,'approvals':True,'user_input':True,'interrupt':False}}
    async def send(self,tid,text,attachments):
        self.sent.append((tid,text,attachments))
        if self.fail:raise OSError('transport closed')
        return 'delivered'
    async def answer(self,rid,choice,answers):
        self.answered.append((rid,choice,answers));self.pending=False;return 'delivered'

async def clients(tmp_path):
    d=FakeDesktop();h=Hub(tmp_path/'private',d,'native-1');await h.start()
    phone=TestClient(TestServer(create_app(h)))
    admin=TestClient(TestServer(create_admin_app(h,'admin-secret-for-tests')))
    await phone.start_server();await admin.start_server()
    return h,d,phone,admin

async def paired(h,phone,name='手机'):
    ticket=h.store.mint_pair()['ticket']
    r=await phone.post('/pair/claim',json={'ticket':ticket,'device_name':name,'client_nonce':uuid.uuid4().hex})
    assert r.status==200
    claim=await r.json()
    h.store.decide_pair(claim['request_id'],True,claim['phrase'])
    r=await phone.post('/pair/status',json={'request_id':claim['request_id'],'poll_token':claim['poll_token']})
    data=await r.json();return {'Authorization':'Bearer '+data['token']},data,claim

@pytest.mark.asyncio
async def test_pairing_requires_local_decision_and_one_time_ticket(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        ticket=h.store.mint_pair()['ticket']
        claim=await (await p.post('/pair/claim',json={'ticket':ticket,'device_name':'手机','client_nonce':uuid.uuid4().hex})).json()
        r=await p.post('/pair/status',json={'request_id':claim['request_id'],'poll_token':claim['poll_token']})
        assert (await r.json())['status']=='pending'
        assert (await p.get('/api/state')).status==401
        assert (await p.post('/pair/claim',json={'ticket':ticket,'device_name':'抢占','client_nonce':uuid.uuid4().hex})).status==409
        assert (await p.post('/pair/status',json={'request_id':claim['request_id'],'poll_token':'wrong'})).status==403
        with pytest.raises(Fault):h.store.decide_pair(claim['request_id'],True,'different')
        h.store.decide_pair(claim['request_id'],True,claim['phrase'])
        result=await (await p.post('/pair/status',json={'request_id':claim['request_id'],'poll_token':claim['poll_token']})).json()
        again=await (await p.post('/pair/status',json={'request_id':claim['request_id'],'poll_token':claim['poll_token']})).json()
        assert result['token']==again['token']
        dbbytes=(tmp_path/'private'/'pocket.sqlite3').read_bytes()
        assert result['token'].encode() not in dbbytes
    finally:await p.close();await a.close();await h.close()

@pytest.mark.asyncio
async def test_admin_routes_never_on_phone_listener_and_need_credential(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        for path in ['/admin/state','/admin/pair','/admin/pair/x/decision']:
            assert (await p.get(path)).status==404
        assert (await a.get('/admin/state')).status==401
        assert (await a.get('/admin/state',headers={'Authorization':'Bearer admin-secret-for-tests','Origin':'https://evil.invalid'})).status==403
        assert (await a.get('/admin/state',headers={'Authorization':'Bearer admin-secret-for-tests'})).status==200
        assert (await p.post('/api/settings',headers={'Origin':'https://evil.invalid'},json={'cache_limit':60})).status==403
    finally:await p.close();await a.close();await h.close()

@pytest.mark.asyncio
async def test_nonce_binding_unknown_not_replayed_and_restart(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        hdr,_,_=await paired(h,p)
        b={'client_id':str(uuid.uuid4()),'text':'你好','attachments':[],'controller_id':'native-1'}
        assert (await (await p.post('/api/messages',headers=hdr,json=b)).json())['status']=='delivered'
        assert (await (await p.post('/api/messages',headers=hdr,json=b)).json())['duplicate']
        assert len(d.sent)==1
        assert (await p.post('/api/messages',headers=hdr,json=dict(b,text='更换正文'))).status==409
        d.fail=True;b2=dict(b,client_id=str(uuid.uuid4()))
        assert (await (await p.post('/api/messages',headers=hdr,json=b2)).json())['status']=='unknown'
        assert (await (await p.post('/api/messages',headers=hdr,json=b2)).json())['status']=='unknown'
        assert len(d.sent)==2
    finally:await p.close();await a.close();await h.close()
    store=Store(tmp_path/'private')
    try:
        assert store.db.execute('SELECT count(*) FROM deliveries').fetchone()[0]==2
    finally:store.close()

@pytest.mark.asyncio
async def test_native_request_stale_and_duplicate_answer(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        hdr,_,_=await paired(h,p)
        b={'client_id':str(uuid.uuid4()),'choice_id':'not-original'}
        assert (await p.post('/api/requests/r1/answer',headers=hdr,json=b)).status==409
        b['choice_id']='accept'
        r=await p.post('/api/requests/r1/answer',headers=hdr,json=b);assert (await r.json())['status']=='delivered'
        r=await p.post('/api/requests/r1/answer',headers=hdr,json=b);assert (await r.json())['duplicate']
        assert len(d.answered)==1
        assert (await p.post('/api/requests/r1/answer',headers=hdr,json=dict(b,client_id=str(uuid.uuid4())))).status==409
        state=await (await p.get('/api/state',headers=hdr)).json()
        assert 'NO' not in json.dumps(state)
    finally:await p.close();await a.close();await h.close()

@pytest.mark.asyncio
async def test_images_reencoded_not_arbitrary_files_and_owned(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        hdr,_,_=await paired(h,p)
        wrong=await p.post('/api/media',headers=dict(hdr,**{'Content-Type':'image/png'}),data=b'<html>not an image</html>');assert wrong.status==400
        image=Image.new('RGB',(20,15),'red');buf=io.BytesIO();image.save(buf,format='PNG')
        r=await p.post('/api/media',headers=dict(hdr,**{'Content-Type':'image/png'}),data=buf.getvalue());assert r.status==200
        media=await r.json();assert media['mime']=='image/png'
        r=await p.get('/api/media/'+media['id'],headers=hdr);assert r.status==200;assert (await r.read()).startswith(b'\x89PNG')
        other,_,_=await paired(h,p,'其他设备')
        assert (await p.get('/api/media/'+media['id'],headers=other)).status==404
        r=await p.post('/api/messages',headers=other,json={'client_id':str(uuid.uuid4()),'text':'看图','attachments':[media['id']],'controller_id':'native-1'});assert r.status==404
        r=await p.post('/api/messages',headers=hdr,json={'client_id':str(uuid.uuid4()),'text':'看图','attachments':[media['id']],'controller_id':'native-1'});assert r.status==200
        assert d.sent[0][2][0]['path'].startswith(str(tmp_path/'private'/'media'))
        assert (await p.get('/api/media/../../etc/passwd',headers=hdr)).status==404
    finally:await p.close();await a.close();await h.close()

@pytest.mark.asyncio
async def test_revocation_closes_stream_and_preserves_native_requests(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        hdr,data,_=await paired(h,p)
        stream=await p.get('/api/events',headers=hdr)
        initial=await stream.content.readuntil(b'\n\n');assert b'event: update' in initial
        assert (await p.delete('/api/cache',headers=hdr)).status==200
        assert h.state['messages']==[] and len(h.state['requests'])==1
        assert (await p.delete('/api/devices/'+data['device_id'],headers=hdr)).status==200
        assert (await p.get('/api/state',headers=hdr)).status==401
        # May receive one pending update before EOF; revocation must terminate promptly.
        await asyncio.wait_for(stream.content.read(),2)
        assert stream.content.at_eof()
    finally:await p.close();await a.close();await h.close()

def test_expiry_and_delivery_crash_recovery(tmp_path):
    now=[1000.0];s=Store(tmp_path,lambda:now[0]);ticket=s.mint_pair();now[0]+=121
    with pytest.raises(Fault):s.claim(ticket['ticket'],'phone','a'*32)
    cid=str(uuid.uuid4());assert s.reserve('device',cid,'message','thread',{'text':'x'}) is None
    s.close();s=Store(tmp_path,lambda:now[0]);assert s.reserve('device',cid,'message','thread',{'text':'x'})['status']=='unknown';s.close()

@pytest.mark.asyncio
async def test_controller_race_rejected_before_external_send(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        hdr,_,_=await paired(h,p)
        h.store.set_setting('controller_id','changed-elsewhere')
        r=await p.post('/api/messages',headers=hdr,json={'client_id':str(uuid.uuid4()),'controller_id':'native-1','text':'必须发送到我看到的原会话','attachments':[]})
        assert r.status==409
        assert not d.sent
    finally:await p.close();await a.close();await h.close()

@pytest.mark.asyncio
async def test_revoked_waiting_write_never_dispatches(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        hdr,data,_=await paired(h,p)
        await h.write_lock.acquire()
        job=asyncio.create_task(p.post('/api/messages',headers=hdr,json={'client_id':str(uuid.uuid4()),'controller_id':'native-1','text':'排队消息','attachments':[]}))
        await asyncio.sleep(.03)
        h.store.revoke(data['device_id'])
        h.write_lock.release()
        response=await job
        assert response.status==401 and not d.sent
    finally:
        if h.write_lock.locked():h.write_lock.release()
        await p.close();await a.close();await h.close()

@pytest.mark.asyncio
async def test_native_validation_rejection_is_not_unknown(tmp_path):
    from adapters.desktop import DesktopError
    h,d,p,a=await clients(tmp_path)
    async def busy(*args):raise DesktopError('thread_busy','请等当前轮次结束')
    d.send=busy
    try:
        hdr,_,_=await paired(h,p)
        b={'client_id':str(uuid.uuid4()),'controller_id':'native-1','text':'看图','attachments':[]}
        r=await p.post('/api/messages',headers=hdr,json=b);assert r.status==409
        assert (await r.json())['error']['code']=='thread_busy'
        r=await p.post('/api/messages',headers=hdr,json=b);assert r.status==409
        assert (await r.json())['error']['code']=='delivery_rejected'
    finally:await p.close();await a.close();await h.close()

@pytest.mark.asyncio
async def test_camera_jpeg_stays_compressed_and_has_small_thumbnail(tmp_path):
    h,d,p,a=await clients(tmp_path)
    try:
        hdr,_,_=await paired(h,p)
        image=Image.new('RGB',(2400,1600),'navy');raw=io.BytesIO();image.save(raw,format='JPEG',quality=95)
        r=await p.post('/api/media',headers=dict(hdr,**{'Content-Type':'image/jpeg'}),data=raw.getvalue())
        info=await r.json();assert r.status==200 and info['mime']=='image/jpeg'
        assert info['size']<500000 and info['thumbnail_id'].endswith('-thumb')
        thumb=await p.get('/api/media/'+info['thumbnail_id'],headers=hdr)
        assert thumb.status==200 and len(await thumb.read())<100000
    finally:await p.close();await a.close();await h.close()
