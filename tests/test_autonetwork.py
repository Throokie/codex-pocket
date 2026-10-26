import base64,copy,datetime,hashlib,json,ssl
from pathlib import Path
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import ec
from server.app import Hub,create_app,create_admin_app
from server.autonetwork import AutoNetwork
from server.discovery import DiscoverySnapshot
from server.network import ServerTLS
from server.store import Fault
from test_server import FakeDesktop

class Provider:
    def __init__(self):self.ip='192.168.40.10';self.empty=False
    async def snapshot(self):
        if self.empty:return DiscoverySnapshot([],[],['暂无网络'])
        return DiscoverySnapshot([{'address':self.ip,'kind':'lan','interface':'wlan0'}],[{'url':f'https://{self.ip}:32851','label':'局域网','kind':'lan'}],[])
class Advertiser:
    def __init__(self):self.calls=[];self.closed=False
    async def update(self,sid,addresses,port,enabled=True):self.calls.append((copy.deepcopy(addresses),enabled));return []
    async def close(self):self.closed=True;return []
class Site:
    fail=set()
    def __init__(self,runner,address,port,ssl_context=None):self.address=address;self.started=False
    async def start(self):
        if self.address in self.fail:raise OSError('isolated bind failure')
        self.started=True
    async def stop(self):self.started=False


def test_tls_identity_is_separate_persistent_and_renewable(tmp_path):
    tls=ServerTLS(tmp_path);again=ServerTLS(tmp_path)
    assert tls.spki==again.spki and tls.context.minimum_version==ssl.TLSVersion.TLSv1_2
    cert=x509.load_pem_x509_certificate((tmp_path/'server-tls-cert.pem').read_bytes())
    assert hashlib.sha256(cert.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)).hexdigest()==tls.spki
    for path in [tmp_path/'server-tls-key.pem',tmp_path/'server-tls-cert.pem']:assert path.stat().st_mode&0o077==0
    from cryptography.x509.oid import NameOID
    key=serialization.load_pem_private_key((tmp_path/'server-tls-key.pem').read_bytes(),None);now=datetime.datetime.now(datetime.timezone.utc)
    short=(x509.CertificateBuilder().subject_name(cert.subject).issuer_name(cert.subject).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(days=1)).not_valid_after(now+datetime.timedelta(days=1)).sign(key,hashes.SHA256()))
    (tmp_path/'server-tls-cert.pem').write_bytes(short.public_bytes(serialization.Encoding.PEM))
    tls.refresh_certificate()
    renewed=x509.load_pem_x509_certificate((tmp_path/'server-tls-cert.pem').read_bytes())
    assert renewed.not_valid_after_utc>now+datetime.timedelta(days=700)
    assert tls.spki==ServerTLS(tmp_path).spki


@pytest.mark.asyncio
async def test_bind_then_publish_dynamic_addresses_and_revision(tmp_path):
    hub=Hub(tmp_path,FakeDesktop());provider=Provider();advertiser=Advertiser();auto=AutoNetwork(hub,None,provider=provider,advertiser=advertiser,site_factory=Site)
    try:
        await auto.refresh();first=auto.revision;old_site=auto.sites[provider.ip]
        assert auto.current[0]['url']=='https://192.168.40.10:32851'
        assert auto.current[0]['tls']=='pinned' and auto.current[0]['managed']
        await auto.refresh();assert auto.revision==first
        provider.ip='192.168.40.11';await auto.refresh()
        assert auto.revision==first+1 and not old_site.started
        assert all(e['url']!='https://192.168.40.10:32851' for e in auto.current)
        assert advertiser.calls[-1][0][0]['address']==provider.ip
        proof=auto.proof('a'*64);key=hub.identity.key.public_key()
        key.verify(base64.b64decode(proof['signature']),f"codex-pocket-discovery-v1\n{'a'*64}\n{hub.identity.id}\n{proof['payload']}".encode(),ec.ECDSA(hashes.SHA256()))
        with pytest.raises(InvalidSignature):key.verify(base64.b64decode(proof['signature']),f"codex-pocket-discovery-v1\n{'b'*64}\n{hub.identity.id}\n{proof['payload']}".encode(),ec.ECDSA(hashes.SHA256()))
        assert json.loads(proof['payload'])['tls_spki']==auto.tls.spki
        # Reconcile old partial state, preserving revision and already-signed payload.
        hub.store.set_setting('network_endpoints',[]);before=auto.payload
        await auto.refresh();assert auto.revision==first+1 and auto.payload==before
        assert hub.store.setting('network_endpoints')==auto.current
    finally:await auto.close();hub.store.close()
    assert advertiser.closed


@pytest.mark.asyncio
async def test_failed_bind_never_advertised_and_empty_withdraws(tmp_path):
    hub=Hub(tmp_path,FakeDesktop());provider=Provider();advertiser=Advertiser();Site.fail={provider.ip};auto=AutoNetwork(hub,None,provider=provider,advertiser=advertiser,site_factory=Site)
    try:
        await auto.refresh();assert auto.current==[] and not advertiser.calls[-1][1]
        assert advertiser.calls[-1][0]==[] and auto.state=='unavailable'
        Site.fail=set();await auto.refresh();assert auto.current
        provider.empty=True;await auto.refresh();assert auto.current==[] and not advertiser.calls[-1][1]
    finally:Site.fail=set();await auto.close();hub.store.close()


@pytest.mark.asyncio
async def test_no_address_manual_input_needed_for_qr_and_manifest_is_public_signed(tmp_path):
    hub=Hub(tmp_path,FakeDesktop());auto=AutoNetwork(hub,None,provider=Provider(),advertiser=Advertiser(),site_factory=Site)
    phone=TestClient(TestServer(create_app(hub)));admin=TestClient(TestServer(create_admin_app(hub,'test-admin')))
    await phone.start_server();await admin.start_server();headers={'Authorization':'Bearer test-admin'}
    try:
        await auto.refresh()
        response=await admin.post('/admin/pair',json={'auto':True},headers=headers)
        assert response.status==200
        qr=(await response.json())['payload'];assert qr['discovery']['tls_spki']==auto.tls.spki
        assert qr['url'] in {e['url'] for e in qr['endpoints']}
        assert next(e for e in qr['endpoints'] if e['url']==qr['url'])['tls']=='pinned'
        response=await phone.get('/discovery',params={'challenge':'c'*64})
        assert response.status==200 and (await response.json())['payload']==auto.payload
        response=await phone.get('/discovery?challenge='+('c'*64)+'&challenge='+('d'*64));assert response.status==400
        response=await phone.post('/admin/discovery/refresh',json={});assert response.status==404
        response=await admin.post('/admin/discovery/refresh',json={},headers=headers);assert response.status==200
        await auto.set_manual([{'url':'https://pocket.example','kind':'public','label':'公网'}])
        manual=next(e for e in auto.current if e['source']=='manual');assert manual['tls']=='system' and manual['managed']
    finally:await phone.close();await admin.close();await auto.close();hub.store.close()


@pytest.mark.asyncio
async def test_periodic_scan_updates_without_manual_refresh(tmp_path):
    import asyncio
    hub=Hub(tmp_path,FakeDesktop());provider=Provider();auto=AutoNetwork(hub,None,provider=provider,advertiser=Advertiser(),site_factory=Site,interval=.02)
    try:
        await auto.start();first=auto.revision;provider.ip='192.168.40.12'
        async with asyncio.timeout(1):
            while auto.revision==first:await asyncio.sleep(.01)
        assert auto.current[0]['url']=='https://192.168.40.12:32851'
    finally:await auto.close();hub.store.close()


@pytest.mark.asyncio
async def test_qr_keeps_second_lan_before_redundant_tailscale_ip(tmp_path):
    class MultiProvider:
        async def snapshot(self):
            records=[('10.20.30.1','lan'),('100.100.100.10','tailscale'),('fd7a:115c:a1e0::10','tailscale'),('192.168.50.20','lan')]
            return DiscoverySnapshot([{'address':ip,'kind':kind,'interface':'wlan0' if ip.startswith('192.') else 'eth0'} for ip,kind in records],[{'url':'https://'+('['+ip+']' if ':' in ip else ip)+':32851','kind':kind,'label':kind} for ip,kind in records],[])
    hub=Hub(tmp_path,FakeDesktop());auto=AutoNetwork(hub,None,provider=MultiProvider(),advertiser=Advertiser(),site_factory=Site)
    admin=TestClient(TestServer(create_admin_app(hub,'test-admin')));await admin.start_server()
    try:
        response=await admin.post('/admin/pair',json={'auto':True},headers={'Authorization':'Bearer test-admin'})
        assert response.status==200
        qr=(await response.json())['payload'];urls=[e['url'] for e in qr['endpoints']]
        assert len(urls)<=3 and 'https://192.168.50.20:32851' in urls
        assert urls.index('https://192.168.50.20:32851')<urls.index('https://100.100.100.10:32851')
    finally:await admin.close();await auto.close();hub.store.close()

@pytest.mark.asyncio
async def test_excluded_interface_removed_from_routes_listener_and_broadcast(tmp_path):
    class MultiProvider:
        async def snapshot(self):
            rows=[{'address':'10.44.0.1','kind':'lan','interface':'eno2'}, {'address':'10.44.1.10','kind':'lan','interface':'wlan0'}, {'address':'100.64.0.8','kind':'tailscale'}]
            return DiscoverySnapshot(rows,[{'url':'https://'+r['address']+':32851','label':'自动','kind':r['kind']} for r in rows],[])
    hub=Hub(tmp_path,FakeDesktop());advertiser=Advertiser();auto=AutoNetwork(hub,None,provider=MultiProvider(),advertiser=advertiser,site_factory=Site)
    try:
        await auto.refresh();old=auto.sites['10.44.0.1'];first=auto.revision
        await auto.set_excluded_interfaces(['eno2'])
        assert auto.revision>first and not old.started and '10.44.0.1' not in auto.sites
        assert '10.44.0.1' not in {a['address'] for a in advertiser.calls[-1][0]}
        assert 'https://10.44.0.1:32851' not in {e['url'] for e in auto.current}
        assert 'https://10.44.1.10:32851' in {e['url'] for e in auto.current}  # no blanket 10/8 filtering
        assert auto.status()['excluded_interfaces']==['eno2']
        assert next(x for x in auto.status()['detected_interfaces'] if x['name']=='eno2')['excluded']
        assert hub.store.setting('discovery_excluded_interfaces')==['eno2']
        with pytest.raises(Fault,match='请选择有效网卡'):await auto.set_excluded_interfaces('eno2')
        assert auto.status()['excluded_interfaces']==['eno2']
        await auto.set_manual([{'url':'https://10.44.0.1:32851','kind':'lan','label':'专用'}])
        assert hub.store.setting('manual_endpoints_v3')  # operator config is retained, just not published
        assert 'https://10.44.0.1:32851' not in {e['url'] for e in auto.current}
        await auto.set_excluded_interfaces([])
        assert 'https://10.44.0.1:32851' in {e['url'] for e in auto.current}
    finally:await auto.close();hub.store.close()
