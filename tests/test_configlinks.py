import base64
from contextlib import asynccontextmanager
import hashlib
import json
import sqlite3
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from server.app import Hub, create_app, create_admin_app
from server.configlinks import CONFIG_PREFIX, PAIR_PREFIX, URI_PREFIX, ConfigLinks
from server.store import Store, Fault
from test_server import FakeDesktop


class Discovery:
    def __init__(self):
        self.current = [
            {'url':'https://computer.tail123.ts.net:32851','label':'Tailscale DNS','kind':'tailscale','tls':'pinned','managed':True,'source':'auto'},
            {'url':'https://192.168.1.20:32851','label':'LAN','kind':'lan','tls':'pinned','managed':True,'source':'auto'},
            {'url':'https://100.100.1.2:32851','label':'Tailscale IP','kind':'tailscale','tls':'pinned','managed':True,'source':'auto'},
            {'url':'https://private.example','label':'Public','kind':'public','tls':'system','managed':True,'source':'manual'}]
        self.excluded = []; self.revision = 7
    def options(self):
        return {'version':1,'enabled':True,'tls_spki':'a'*64,'service_type':'_codexpocket._tcp.','revision':self.revision}
    async def refresh(self): return self.status()
    async def set_excluded_interfaces(self, values): self.excluded=values
    def status(self):return {'enabled':True,'excluded_interfaces':self.excluded,'endpoints':self.current}


@asynccontextmanager
async def apps(tmp_path):
    hub=Hub(tmp_path,FakeDesktop());hub.discovery=Discovery()
    hub.store.set_setting('network_endpoints',hub.discovery.current)
    phone=TestClient(TestServer(create_app(hub)));admin=TestClient(TestServer(create_admin_app(hub,'test-admin')))
    await phone.start_server();await admin.start_server()
    try:yield hub,phone,admin
    finally:await phone.close();await admin.close();hub.store.close()


def verify(hub, wrapper, prefix, nonce):
    assert wrapper['protocol']==1 and wrapper['server_id']==hub.identity.id and wrapper['challenge']==nonce
    key=serialization.load_der_public_key(base64.b64decode(hub.identity.public_key))
    assert hashlib.sha256(base64.b64decode(hub.identity.public_key)).hexdigest()==hub.identity.id
    key.verify(base64.b64decode(wrapper['signature']),f"{prefix}\n{nonce}\n{hub.identity.id}\n{wrapper['payload']}".encode(),ec.ECDSA(hashes.SHA256()))
    return json.loads(wrapper['payload'])


@pytest.mark.asyncio
async def test_link_is_persistent_bounded_public_and_admin_only(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        r=await admin.post('/admin/config-link',json={});assert r.status==401
        r=await phone.post('/admin/config-link',json={});assert r.status==404
        headers={'Authorization':'Bearer test-admin'}
        r=await admin.post('/admin/config-link',json={},headers=headers);assert r.status==200
        issued=await r.json();assert len(issued['link'].encode())<=2400 and issued['link'].startswith(URI_PREFIX)
        encoded=issued['link'][len(URI_PREFIX):];assert '=' not in encoded
        bootstrap=json.loads(base64.urlsafe_b64decode(encoded+'='*((-len(encoded))%4)))
        assert bootstrap==issued['bootstrap']
        assert bootstrap['url']=='https://100.100.1.2:32851'
        assert len(bootstrap['endpoints'])<=3 and all(e['tls']=='pinned' and e['url'].startswith('https://') for e in bootstrap['endpoints'])
        assert 'ticket' not in bootstrap and 'token' not in bootstrap
        second=await (await admin.post('/admin/config-link',json={},headers=headers)).json()
        assert second['config_id']==issued['config_id']
        status=await (await admin.get('/admin/state',headers=headers)).json()
        assert status['config_link_enabled'] and status['config_link']['active']
        assert issued['config_id'] not in json.dumps(status)
        assert hub.store.db.execute('select count(*) from pairings').fetchone()[0]==0
        directory=hub.store.directory;hub.store.close();hub.store=Store(directory)
        assert hub.store.config_link_matches(issued['config_id'])


@pytest.mark.asyncio
async def test_signed_profile_get_has_no_ticket_and_separate_pair_domain(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        public=hub.config_links.admin_link();cid=public['config_id'];nonce='b'*64
        r=await phone.get('/config/'+cid,params={'challenge':nonce});assert r.status==200 and r.headers['Cache-Control']=='no-store'
        wrapper=await r.json();profile=verify(hub,wrapper,CONFIG_PREFIX,nonce)
        assert profile['config_id']==cid and len(profile['endpoints'])==4 and profile['type']=='codex-pocket-config'
        assert 'ticket' not in profile and 'token' not in profile
        key=serialization.load_der_public_key(base64.b64decode(hub.identity.public_key))
        with pytest.raises(InvalidSignature):key.verify(base64.b64decode(wrapper['signature']),f"{PAIR_PREFIX}\n{nonce}\n{hub.identity.id}\n{wrapper['payload']}".encode(),ec.ECDSA(hashes.SHA256()))
        r=await phone.post('/config/'+cid+'/pair',json={'challenge':'c'*64});assert r.status==200
        pair=verify(hub,await r.json(),PAIR_PREFIX,'c'*64)
        assert pair['type']=='codex-pocket-pair' and pair['config_id']==cid
        assert type(pair['expires_at']) is int  # Native strictly requires integer Unix seconds
        assert pair['expires_at']==pytest.approx(hub.store.clock()+120,abs=1)
        assert pair['revision']==pair['discovery']['revision']==7
        assert pair['server_identity']==profile['server_identity'] and pair['discovery']==profile['discovery']
        assert pair['ticket'] and 'token' not in pair
        assert not hub.store.devices() and not hub.store.pending_pairs()
        claimed=hub.store.claim(pair['ticket'],'test-phone','nonce-01234567890123456789')
        assert claimed['phrase'] and len(hub.store.pending_pairs())==1 and not hub.store.devices()


@pytest.mark.asyncio
async def test_rotation_denies_issued_pending_but_preserves_approved_device(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        old=hub.config_links.admin_link()['config_id']
        approved=json.loads(hub.config_links.pair(old,'a'*64)['payload'])
        a=hub.store.claim(approved['ticket'],'approved-phone','a'*20);hub.store.decide_pair(a['request_id'],True,a['phrase'])
        credentials=hub.store.pair_status(a['request_id'],a['poll_token'])
        pending=json.loads(hub.config_links.pair(old,'b'*64)['payload']);p=hub.store.claim(pending['ticket'],'pending-phone','b'*20)
        issued=json.loads(hub.config_links.pair(old,'c'*64)['payload'])
        new=hub.config_links.admin_link(rotate=True)['config_id'];assert new!=old
        assert (await phone.get('/config/'+old,params={'challenge':'d'*64})).status==404
        assert (await phone.post('/config/'+old+'/pair',json={'challenge':'e'*64})).status==404
        with pytest.raises(Fault):hub.store.claim(issued['ticket'],'another-phone','x'*20)
        with pytest.raises(Fault):hub.store.decide_pair(p['request_id'],True,p['phrase'])
        assert hub.store.pair_status(p['request_id'],p['poll_token'])['status']=='denied'
        assert hub.store.authenticate(credentials['token'])['id']==credentials['device_id']
        assert hub.store.pair_status(a['request_id'],a['poll_token'])['status']=='approved'
        assert len(hub.store.devices())==1


@pytest.mark.asyncio
async def test_invalid_challenges_capability_and_request_shape_never_mint(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        cid=hub.config_links.admin_link()['config_id']
        for challenge in ('A'*64,'a'*63,'a'*65,''):
            assert (await phone.get('/config/'+cid,params={'challenge':challenge})).status==400
        assert (await phone.get('/config/'+cid+'?challenge='+('a'*64)+'&challenge='+('b'*64))).status==400
        assert (await phone.get('/config/'+'0'*64,params={'challenge':'a'*64})).status==404
        r=await phone.post('/config/'+cid+'/pair',data='{"challenge":"'+('a'*64)+'","challenge":"'+('b'*64)+'"}',headers={'Content-Type':'application/json'});assert r.status==400
        r=await phone.post('/config/'+cid+'/pair',json={'challenge':'a'*64,'approve':True});assert r.status==400
        assert hub.store.db.execute('select count(*) from pairings').fetchone()[0]==0


@pytest.mark.asyncio
async def test_cap_link_pending_and_source_limits_are_bounded(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        cid=hub.config_links.admin_link()['config_id']
        for n in range(3):
            assert (await phone.post('/config/'+cid+'/pair',json={'challenge':str(n)*64})).status==200
        assert (await phone.post('/config/'+cid+'/pair',json={'challenge':'d'*64})).status==429
        assert (await phone.post('/config/'+cid+'/pair',json={'challenge':'e'*64})).status==429
        assert hub.store.db.execute("select count(*) from pairings where status='issued'").fetchone()[0]==3
        assert hub.store.db.execute('select count(*) from devices').fetchone()[0]==0


@pytest.mark.asyncio
async def test_config_link_filters_cleartext_unregistered_and_seed_system_tls(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        hub.discovery.current += [{'url':'http://192.168.1.30','kind':'lan','tls':'system','source':'manual'},
                                  {'url':'https://evil.example','kind':'public','tls':'system','source':'manual'}]
        hub.store.set_setting('network_endpoints',hub.discovery.current[:-1])
        public=hub.config_links.admin_link();assert all(e['tls']=='pinned' for e in public['endpoints'])
        full=json.loads(hub.config_links.get(public['config_id'],'f'*64)['payload'])
        assert all(e['url'].startswith('https://') for e in full['endpoints'])
        assert 'evil.example' not in json.dumps(full)
        old=public['config_id'];hub.discovery.current=[{'url':'https://private.example','kind':'public','tls':'system','source':'manual'}]
        with pytest.raises(Fault):hub.config_links.admin_link(rotate=True)
        assert hub.store.config_link_matches(old) and not hub.store.devices()


@pytest.mark.asyncio
async def test_preferences_admin_boundary_and_bounded_payload(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        body={'excluded_interfaces':['enp3s0']}
        assert (await phone.post('/admin/discovery/preferences',json=body)).status==404
        assert (await admin.post('/admin/discovery/preferences',json=body)).status==401
        assert (await admin.post('/admin/discovery/preferences',json=body,headers={'Authorization':'Bearer test-admin'})).status==200
        assert hub.discovery.excluded==['enp3s0']
        assert (await admin.post('/admin/config-link',json={'rotate':'true'},headers={'Authorization':'Bearer test-admin'})).status==400


def test_old_database_schema_is_migrated_without_invalidating_old_pairings(tmp_path):
    db=sqlite3.connect(tmp_path/'pocket.sqlite3')
    db.execute("CREATE TABLE pairings(id TEXT PRIMARY KEY,ticket_hash TEXT UNIQUE NOT NULL,expires_at REAL NOT NULL,status TEXT NOT NULL DEFAULT 'issued',poll_hash TEXT,nonce_hash TEXT,device_name TEXT,phrase TEXT,device_id TEXT,token_envelope TEXT)")
    db.commit();db.close()
    (tmp_path/'pocket.sqlite3').chmod(0o600)
    store=Store(tmp_path)
    try:
        assert 'config_id' in {row['name'] for row in store.db.execute('pragma table_info(pairings)')}
        old=store.mint_pair();assert store.claim(old['ticket'],'old-phone','x'*20)['phrase']
        assert not store.config_link_matches('0'*64)
        store.set_setting('config_link',{'id':'corrupt'})
        assert not store.config_link_matches('0'*64)
    finally:store.close()


@pytest.mark.asyncio
async def test_large_unicode_link_prunes_secondary_seeds_before_persisting(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        hub.name='🌳'*80
        hub.discovery.current=[{'url':'https://'+'.'.join([ch*60]*4)+'.ts.net:32851','label':'🌳'*40,'kind':'tailscale','tls':'pinned','managed':True,'source':'auto'} for ch in 'abc']
        hub.store.set_setting('network_endpoints',hub.discovery.current)
        public=hub.config_links.admin_link()
        assert len(public['link'].encode())<=2400 and 1<=len(public['endpoints'])<3
        assert hub.store.config_link_matches(public['config_id'])


@pytest.mark.asyncio
async def test_config_capability_never_authenticates_chat_or_sending(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        cid=hub.config_links.admin_link()['config_id'];headers={'Authorization':'Bearer '+cid}
        assert (await phone.get('/api/state',headers=headers)).status==401
        assert (await phone.post('/api/messages',json={'text':'must not send'},headers=headers)).status==401
        assert not hub.store.devices()


@pytest.mark.asyncio
async def test_bad_cap_does_not_burn_global_pair_budget(tmp_path):
    async with apps(tmp_path) as (hub,phone,admin):
        cid=hub.config_links.admin_link()['config_id']
        assert (await phone.post('/config/'+'0'*64+'/pair',json={'challenge':'a'*64})).status==404
        assert ('config-pair-global',) not in hub.rate
        assert (await phone.post('/config/'+cid+'/pair',json={'challenge':'a'*64})).status==200
