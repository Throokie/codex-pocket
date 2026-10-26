import base64
import hashlib
import json
from pathlib import Path
import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from aiohttp.test_utils import TestClient,TestServer
from server.network import ServerIdentity,canonical_origin,endpoints
from server.store import Fault
from server.app import Hub,create_app,create_admin_app
from test_server import FakeDesktop


def test_identity_is_stable_and_signature_binds_nonce_origin_and_key(tmp_path):
    a=ServerIdentity(tmp_path);b=ServerIdentity(tmp_path)
    assert a.id==b.id and a.public()==b.public()
    proof=a.proof('a'*64,'https://lan.example:443/',[{'url':'https://lan.example'}])
    key=serialization.load_der_public_key(base64.b64decode(proof['public_key']))
    assert hashlib.sha256(base64.b64decode(proof['public_key'])).hexdigest()==proof['server_id']
    signature=base64.b64decode(proof['signature'])
    key.verify(signature,f"codex-pocket-route-v1\n{'a'*64}\n{a.id}\nhttps://lan.example".encode(),ec.ECDSA(hashes.SHA256()))
    for nonce,origin in [('b'*64,'https://lan.example'),('a'*64,'https://evil.example'),('a'*64,'https://lan.example:8443')]:
        with pytest.raises(InvalidSignature):key.verify(signature,f'codex-pocket-route-v1\n{nonce}\n{a.id}\n{origin}'.encode(),ec.ECDSA(hashes.SHA256()))
    assert (tmp_path/'server-identity.pem').stat().st_mode&0o077==0


def test_unknown_origin_cannot_relay_identity_and_bad_nonce_rejected(tmp_path):
    identity=ServerIdentity(tmp_path)
    for origin in ['https://evil.example','https://real.example:444','http://real.example']:
        with pytest.raises(Fault):identity.proof('c'*64,origin,[{'url':'https://real.example'}])
    for challenge in ['', 'f'*63, 'F'*64, 'a'*65, None]:
        with pytest.raises(Fault):identity.proof(challenge,'https://real.example',[{'url':'https://real.example'}])


def test_explicit_routes_canonicalize_and_remain_bounded():
    assert canonical_origin('HTTPS://EXAMPLE.COM:443/')=='https://example.com'
    assert canonical_origin('https://[2001:0db8:0:0::1]:443/')=='https://[2001:db8::1]'
    assert canonical_origin('http://100.64.0.1:80/',True)=='http://100.64.0.1'
    routes=endpoints([{'url':'https://example.com:443/','label':'主地址','kind':'public'},{'url':'https://example.com','kind':'public'}])
    assert len(routes)==1
    with pytest.raises(Fault):endpoints([{'url':f'https://r{i}.example'} for i in range(7)])
    with pytest.raises(Fault):endpoints([{'url':'https://example.com','kind':'unknown'}])


@pytest.mark.parametrize('url',['http://8.8.8.8','http://100.128.0.1','http://192.0.2.1','https://user:pass@host','https://host/api','https://host?x=1','https://host#secret','https://host:0','https://host:65536','https://[fe80::1%25eth0]',' https://host','https://host\\evil'])
def test_bad_origins_rejected(url):
    with pytest.raises(Fault):canonical_origin(url,True)


@pytest.mark.asyncio
async def test_public_proof_has_no_auth_and_admin_only_registers_origins(tmp_path):
    hub=Hub(tmp_path,FakeDesktop())
    phone=TestClient(TestServer(create_app(hub)));admin=TestClient(TestServer(create_admin_app(hub,'test-admin')))
    await phone.start_server();await admin.start_server()
    headers={'Authorization':'Bearer test-admin'}
    try:
        r=await phone.post('/admin/networks',json={'endpoints':[{'url':'https://evil.example'}]})
        assert r.status==404
        r=await admin.post('/admin/networks',json={'endpoints':[{'url':'https://real.example','kind':'public'}]})
        assert r.status==401
        r=await admin.post('/admin/networks',json={'endpoints':[{'url':'https://real.example','kind':'public'}]},headers=headers)
        assert r.status==200
        r=await phone.get('/identity',params={'challenge':'a'*64,'origin':'https://real.example'})
        proof=await r.json();assert r.status==200 and proof['origin']=='https://real.example'
        assert set(proof)=={'protocol','server_id','public_key','challenge','origin','signature'}
        assert r.headers['Cache-Control']=='no-store'
        r=await phone.get('/identity',params={'challenge':'a'*64,'origin':'https://evil.example'})
        assert r.status==403
        r=await phone.get('/identity?challenge='+('a'*64)+'&challenge='+('b'*64)+'&origin=https://real.example')
        assert r.status==400
        r=await admin.post('/admin/pair',json={'url':'https://real.example','endpoints':[{'url':'https://real.example','kind':'public'},{'url':'https://tail.example','kind':'tailscale'}]},headers=headers)
        payload=(await r.json())['payload'];assert len(payload['endpoints'])==2 and payload['server_identity']==hub.identity.public()
        assert 'token' not in payload
    finally:
        await phone.close();await admin.close();hub.store.close()


@pytest.mark.asyncio
async def test_cancel_during_sse_prepare_does_not_leak_stream_slot(tmp_path,monkeypatch):
    from aiohttp import web,ServerDisconnectedError
    from test_server import paired
    hub=Hub(tmp_path,FakeDesktop())
    phone=TestClient(TestServer(create_app(hub)))
    await phone.start_server()
    try:
        headers,_,_=await paired(hub,phone)
        original=web.StreamResponse.prepare
        async def interrupted(response,request):
            if response.headers.get('Content-Type')=='text/event-stream':raise ConnectionResetError('isolated disconnect before headers')
            return await original(response,request)
        monkeypatch.setattr(web.StreamResponse,'prepare',interrupted)
        for _ in range(4):
            try:
                response=await phone.get('/api/events',headers=headers)
                assert response.status!=429
                response.close()
            except ServerDisconnectedError:pass
            assert sum(len(q) for q in hub.watchers.values())==0
    finally:
        await phone.close();hub.store.close()
