"""Bind detected private addresses before publishing an identity-signed route list."""
import asyncio
import copy
import base64
import hashlib
import ipaddress
import json
import re
import time
from urllib.parse import urlsplit
from aiohttp import web
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from .network import ServerTLS,endpoints as clean_endpoints,canonical_origin
from .store import Fault


class AutoNetwork:
    def __init__(self,hub,runner,tls_port=32851,provider=None,advertiser=None,interval=15,site_factory=web.TCPSite):
        from .discovery import AddressDiscovery,MDNSAdvertiser
        self.hub,self.runner,self.port=hub,runner,tls_port
        self.tls=ServerTLS(hub.store.directory)
        self.site_factory=site_factory;self.last_cert_check=time.monotonic()
        self.provider=provider or AddressDiscovery(tls_port=tls_port)
        self.advertiser=advertiser or MDNSAdvertiser()
        self.interval=interval;self.sites={};self.scan_lock=asyncio.Lock();self.task=None;self.last_scan=0;self.diagnostics=[];self.state='unavailable';self.closed=False
        self.auto=[];self.current=[];self.detected_interfaces=[];self.excluded_addresses=set()
        self.revision=int(hub.store.setting('discovery_revision',0))
        self.payload=hub.store.setting('discovery_payload','')
        try:
            recovered=json.loads(self.payload).get('revision',0)
            if type(recovered) is int:self.revision=max(self.revision,recovered)
        except (ValueError,TypeError,AttributeError):pass
        # A pre-upgrade list is retained as manual; no old endpoint is silently reclassified.
        if hub.store.setting('manual_endpoints_v3') is None:
            hub.store.set_setting('manual_endpoints_v3',hub.store.setting('network_endpoints',[]))
        hub.discovery=self
    def options(self):
        return {'version':1,'enabled':True,'tls_spki':self.tls.spki,'service_type':'_codexpocket._tcp.','revision':self.revision}
    def status(self):
        return {'enabled':True,'state':self.state,'diagnostics':list(self.diagnostics),'revision':self.revision,'last_scan':self.last_scan,'endpoints':list(self.current),'tls_port':self.port,'excluded_interfaces':self._excluded_interfaces(),'detected_interfaces':copy.deepcopy(self.detected_interfaces)}
    @staticmethod
    def clean_excluded_interfaces(values):
        if not isinstance(values,list) or len(values)>32 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,32}',v) for v in values):
            raise Fault('bad_interfaces','请选择有效网卡，最多32个')
        return sorted(set(values))
    def _excluded_interfaces(self):
        return self.clean_excluded_interfaces(self.hub.store.setting('discovery_excluded_interfaces',[]))
    async def set_excluded_interfaces(self,values):
        values=self.clean_excluded_interfaces(values)
        self.hub.store.set_setting('discovery_excluded_interfaces',values)
        return await self.refresh()
    def _manual(self):
        raw=self.hub.store.setting('manual_endpoints_v3',[])
        if not raw:return []
        return [dict(e,managed=True,source='manual',tls='system') for e in clean_endpoints(raw,allow_http=True) if urlsplit(e['url']).hostname not in self.excluded_addresses]
    def _publish(self):
        manual=self._manual();urls={e['url'] for e in manual}
        auto=[e for e in self.auto if e['url'] not in urls]
        self.current=manual+auto[:max(0,6-len(manual))]
        if len(manual)+len(auto)>6:self.diagnostics.append('候选最多6条；可移除不再使用的手动地址，为自动地址留出位置')
        body={'version':1,'endpoints':self.current,'tls_spki':self.tls.spki,'service_type':'_codexpocket._tcp.'}
        previous=None
        try:previous=json.loads(self.payload)
        except (TypeError,ValueError):pass
        old_without_revision={k:v for k,v in (previous or {}).items() if k!='revision'}
        changed=body!=old_without_revision
        next_revision=self.revision+1 if changed else self.revision
        body['revision']=next_revision
        next_payload=json.dumps(body,ensure_ascii=False,separators=(',',':'),sort_keys=True)
        if len(next_payload.encode())>14000:raise Fault('routes_too_large','自动地址信息过长')
        self.hub.store.db.execute('BEGIN IMMEDIATE')
        try:
            self.hub.store.set_setting('discovery_revision',next_revision)
            self.hub.store.set_setting('discovery_payload',next_payload)
            # Reconcile even an old partially written state without inventing a new revision.
            self.hub.store.set_setting('network_endpoints',self.current)
            self.hub.store.db.execute('COMMIT')
        except Exception:
            self.hub.store.db.execute('ROLLBACK');raise
        self.revision,self.payload=next_revision,next_payload
        if changed:self.hub.emit()
    async def set_manual(self,values,allow_http=False):
        values=clean_endpoints(values,allow_http=allow_http) if values else []
        self.hub.store.set_setting('manual_endpoints_v3',values)
        await self.refresh()
        return self.status()
    async def start(self):
        await self.refresh()
        self.task=asyncio.create_task(self._loop())
    async def _loop(self):
        while not self.closed:
            await asyncio.sleep(self.interval)
            try:await self.refresh()
            except asyncio.CancelledError:raise
            except Exception:self.diagnostics=['自动地址更新暂不可用，保留上次已知线路'];self.state='partial'
    async def refresh(self):
        async with self.scan_lock:
            if self.closed:return self.status()
            if time.monotonic()-self.last_cert_check>86400:
                self.tls.refresh_certificate();self.last_cert_check=time.monotonic()
            snapshot=await self.provider.snapshot()
            detected=list(snapshot.addresses);self.diagnostics=list(snapshot.diagnostics)
            excluded=set(self._excluded_interfaces());groups={}
            for a in detected:
                name=a.get('interface')
                if isinstance(name,str):groups.setdefault(name,set()).add(a['address'])
            self.detected_interfaces=[{'name':name,'addresses':sorted(values),'excluded':name in excluded} for name,values in sorted(groups.items())]
            self.excluded_addresses={a['address'] for a in detected if a.get('interface') in excluded}
            addresses=[a for a in detected if a.get('interface') not in excluded]
            targets={a['address'] for a in addresses if self._private(a['address'])}
            # Loopback permits local diagnostics; never advertised as a phone route.
            targets.add('127.0.0.1')
            bound=set()
            for address in sorted(targets):
                if address not in self.sites:
                    try:
                        site=self.site_factory(self.runner,address,self.port,ssl_context=self.tls.context)
                        await site.start();self.sites[address]=site
                    except OSError:
                        self.diagnostics.append('部分自动地址未能监听，请检查端口或网络接口');continue
                bound.add(address)
            tail_bound=any(a.get('kind')=='tailscale' and a['address'] in bound for a in addresses)
            self.auto=[]
            for entry in snapshot.endpoints:
                try:
                    url=canonical_origin(entry.get('url'));parts=urlsplit(url);host=parts.hostname
                    if (parts.port or 443)!=self.port:continue
                    is_ip=True
                    try:ipaddress.ip_address(host)
                    except ValueError:is_ip=False
                    if (is_ip and host not in bound) or (not is_ip and not tail_bound):continue
                    self.auto.append({'url':url,'label':entry.get('label','自动地址')[:40],'kind':entry.get('kind','lan'),'tls':'pinned','managed':True,'source':'auto'})
                except Fault:continue
            self.auto=self.auto[:6]
            self.last_scan=time.time();self.state='ready' if self.auto and not self.diagnostics else 'partial' if self.auto or self._manual() else 'unavailable'
            self._publish()
            try:
                advertised=[a for a in addresses if a['address'] in bound]
                warnings=await self.advertiser.update(self.hub.identity.id,advertised,self.port,enabled=bool(self.auto))
                if warnings:self.diagnostics.extend(warnings);self.state='partial'
            except Exception:
                self.diagnostics.append('局域网广播暂不可用，仍可使用已知地址与Tailscale');self.state='partial'
            for old in set(self.sites)-targets:
                site=self.sites.pop(old);await site.stop()
            return self.status()
    @staticmethod
    def _private(value):
        try:
            ip=ipaddress.ip_address(value)
            return any(ip in n for n in [ipaddress.ip_network(c) for c in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16','100.64.0.0/10','fc00::/7')] if ip.version==n.version)
        except ValueError:return False
    def proof(self,challenge):
        if not isinstance(challenge,str) or not re.fullmatch('[0-9a-f]{64}',challenge):raise Fault('bad_challenge','发现请求参数无效')
        if not self.current:raise Fault('discovery_unavailable','尚未发现可供手机访问的地址',503)
        data=('codex-pocket-discovery-v1\n'+challenge+'\n'+self.hub.identity.id+'\n'+self.payload).encode()
        signature=base64.b64encode(self.hub.identity.key.sign(data,ec.ECDSA(hashes.SHA256()))).decode()
        return {'protocol':1,'server_id':self.hub.identity.id,'challenge':challenge,'payload':self.payload,'signature':signature}
    async def close(self):
        self.closed=True
        if self.task:self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        try:await self.advertiser.close()
        finally:
            for site in self.sites.values():await site.stop()
            self.sites.clear()
