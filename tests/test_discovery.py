import asyncio
import json
import sys
import pytest
from server import discovery as d


def interface(name='enp3s0', addresses=('192.168.1.20',), flags=None, **extra):
    return {'ifname':name,'ifindex':2,'flags':['UP','BROADCAST','MULTICAST'] if flags is None else flags,
            'operstate':'UP','link_type':'ether','addr_info':[{'local':ip,'scope':'global'} for ip in addresses],**extra}


def ts(addresses=('100.100.1.2','fd7a:115c:a1e0::1234'), dns='machine.tail123.ts.net.'):
    return {'BackendState':'Running','Self':{'TailscaleIPs':list(addresses),'DNSName':dns},
            'Peer':{'sensitive-peer-sentinel':{'UserID':'sensitive-user'}},'User':{'private':'secret'}}


def runner(ip, tail):
    async def run(argv):
        value=ip if argv[0]=='ip' else tail
        if isinstance(value,Exception):raise value
        return value if isinstance(value,bytes) else json.dumps(value).encode()
    return run


def test_local_filters_ipv4_ipv6_and_virtual_interfaces():
    rows=[interface(addresses=('192.168.1.20','10.2.3.4','172.16.2.3','fcaa::2','8.8.8.8','127.0.0.1','169.254.1.2','fe80::1','2001:db8::1','100.64.1.2'))]
    rows += [interface(name=n) for n in ['lo','docker0','veth123','virbr0','br-test','tailscale0','wg0','tun0','bond0','cni0']]
    rows += [interface(name='wlan0',flags=['DOWN']),interface(name='eth9',linkinfo={'info_kind':'bridge'})]
    result=d.parse_ip(rows)
    assert {r['address'] for r in result}=={'192.168.1.20','10.2.3.4','172.16.2.3','fcaa::2'}
    assert all(r['interface']=='enp3s0' and r['interface_index']==2 for r in result)


def test_tentative_deprecated_scope_and_duplicates():
    row=interface(addresses=('192.168.1.20','192.168.1.20'))
    row['addr_info'] += [{'local':'10.1.1.1','scope':'global','tentative':True},
                         {'local':'10.1.1.2','scope':'global','flags':['deprecated']},
                         {'local':'10.1.1.3','scope':'link'},
                         {'local':'10.1.1.4','scope':'global','preferred_life_time':0}]
    assert len(d.parse_ip([row]))==1


def test_tailscale_only_self_running_and_real_tailnet_addresses():
    value=ts(('100.64.1.2','100.127.255.1','fd7a:115c:a1e0::1','192.168.1.2','100.128.0.1','8.8.8.8'))
    addresses,dns,running=d.parse_tailscale(value)
    assert {r['address'] for r in addresses}=={'100.64.1.2','100.127.255.1','fd7a:115c:a1e0::1'}
    assert dns=='machine.tail123.ts.net' and running
    value['BackendState']='NeedsLogin'
    assert d.parse_tailscale(value)==([],None,False)
    assert d.parse_tailscale(ts(dns='user:password@evil.example'))[1] is None


@pytest.mark.asyncio
async def test_snapshot_priority_bound_no_peers_and_dedup():
    snapper=d.AddressDiscovery(platform="linux",runner=runner([interface(addresses=tuple(f'10.1.1.{n}' for n in range(1,10)))],ts()))
    snap=await snapper.snapshot()
    assert len(snap.endpoints)==6
    assert snap.endpoints[0]['url']=='https://machine.tail123.ts.net:32851'
    assert snap.endpoints[1]['kind']=='lan'
    assert snap.endpoints[2]['kind']=='tailscale' and snap.endpoints[3]['url'].startswith('https://[')
    assert all(e['managed'] and e['source']=='auto' and e['tls']=='pinned' for e in snap.endpoints)
    assert 'sensitive' not in repr(snap) and 'sensitive' not in repr(snapper._cache)
    assert not snap.diagnostics


@pytest.mark.asyncio
async def test_transient_failures_cache_then_expire_and_recover():
    now=[0];snapper=d.AddressDiscovery(platform="linux",runner=runner([interface()],ts()),clock=lambda:now[0])
    first=await snapper.snapshot()
    snapper.runner=runner(d.DiscoveryCommandError('timeout'),d.DiscoveryCommandError('unavailable'))
    now[0]=20;cached=await snapper.snapshot()
    assert cached.endpoints==first.endpoints and len(cached.diagnostics)==4
    now[0]=121;expired=await snapper.snapshot()
    assert not expired.addresses and not expired.endpoints
    snapper.runner=runner([interface(addresses=('10.1.1.7',))],ts())
    recovered=await snapper.snapshot()
    assert '10.1.1.7' in repr(recovered) and not recovered.diagnostics


@pytest.mark.asyncio
async def test_authoritative_empty_or_logout_withdraws_immediately():
    snapper=d.AddressDiscovery(platform="linux",runner=runner([interface()],ts()))
    await snapper.snapshot();snapper.runner=runner([],{'BackendState':'Stopped'})
    snap=await snapper.snapshot()
    assert not snap.endpoints and not snap.addresses


@pytest.mark.asyncio
async def test_command_failure_and_invalid_input_never_echo_sensitive_json():
    for bad in [b'not-json-SECRET',b'x'*(d.MAX_OUTPUT+1),d.DiscoveryCommandError('missing'),PermissionError('secret-error')]:
        snap=await d.AddressDiscovery(platform="linux",runner=runner(bad,bad)).snapshot()
        assert not snap.endpoints and len(snap.diagnostics)==2
        assert 'SECRET' not in repr(snap) and 'secret-error' not in repr(snap)


@pytest.mark.asyncio
async def test_injected_runner_timeout_is_bounded(monkeypatch):
    monkeypatch.setattr(d,'COMMAND_TIMEOUT',.01)
    async def slow(argv):await asyncio.sleep(5)
    snap=await d.AddressDiscovery(platform="linux",runner=slow).snapshot()
    assert not snap.endpoints and all('超时' in x for x in snap.diagnostics)


@pytest.mark.asyncio
async def test_subprocess_stdout_cap_and_timeout(monkeypatch):
    monkeypatch.setattr(d,'MAX_OUTPUT',128)
    with pytest.raises(d.DiscoveryCommandError) as result:
        await d.run_local((sys.executable,'-c','import sys;sys.stdout.write("x"*4096)'))
    assert result.value.kind=='too_large'
    monkeypatch.setattr(d,'COMMAND_TIMEOUT',.03)
    with pytest.raises(d.DiscoveryCommandError) as result:
        await d.run_local((sys.executable,'-c','import time;time.sleep(5)'))
    assert result.value.kind=='timeout'


class FakeInfo:
    def __init__(self,*args,**kwargs):self.args=args;self.kwargs=kwargs


class FakeZC:
    created=[]
    def __init__(self,**kwargs):self.kwargs=kwargs;self.events=[];self.created.append(self)
    async def async_register_service(self,info,**kwargs):
        self.events.append(('register',info))
        async def complete():self.events.append(('announced',info))
        return complete()
    async def async_unregister_service(self,info):
        self.events.append(('unregister',info))
        async def complete():self.events.append(('goodbye',info))
        return complete()
    async def async_close(self):self.events.append(('close',None))


@pytest.mark.asyncio
async def test_mdns_only_bound_lan_update_removes_old_addresses_and_close():
    FakeZC.created=[]
    advertiser=d.MDNSAdvertiser(zeroconf_factory=FakeZC,info_factory=FakeInfo)
    lan=d.parse_ip([interface(addresses=('192.168.1.20','fcaa::2'))])
    inputs=lan+[{'address':'100.100.1.2','kind':'tailscale'}, {'address':'127.0.0.1','kind':'lan','interface':'lo'}]
    assert await advertiser.update('a'*64,inputs,32851)==[]
    first=FakeZC.created[0];info=first.events[0][1]
    assert first.kwargs['interfaces']==['192.168.1.20',2]
    assert info.args==(d.SERVICE,'Codex-Pocket-aaaaaaaa.'+d.SERVICE)
    assert set(info.kwargs['properties'])=={'id','v','tlsport'}
    assert len(info.kwargs['addresses'])==2
    assert await advertiser.update('a'*64,inputs,32851)==[] and len(FakeZC.created)==1
    assert await advertiser.update('a'*64,d.parse_ip([interface(addresses=('10.2.2.2',))]),32851)==[]
    assert [e[0] for e in first.events]==['register','announced','unregister','goodbye','close']
    await advertiser.close()
    assert FakeZC.created[-1].events[-1][0]=='close'


@pytest.mark.asyncio
async def test_mdns_disable_and_failure_are_bounded_generic_and_recover():
    class FailZC(FakeZC):
        async def async_register_service(self,*args,**kwargs):raise OSError('SECRET')
    inputs=d.parse_ip([interface()])
    advertiser=d.MDNSAdvertiser(zeroconf_factory=FailZC,info_factory=FakeInfo)
    result=await advertiser.update('a'*64,inputs,32851)
    assert result and 'SECRET' not in repr(result) and advertiser._zc is None
    advertiser._factory=FakeZC
    assert await advertiser.update('a'*64,inputs,32851)==[]
    assert await advertiser.update('a'*64,inputs,32851,False)==[] and advertiser._zc is None


def test_malformed_optional_fields_and_vlan_do_not_break_discovery():
    row=interface();row['addr_info'][0]['flags']=9
    assert d.parse_ip([row])==[]
    assert d.parse_ip([interface(name='enp3s0.20')])==[]


@pytest.mark.asyncio
async def test_cancel_partial_mdns_registration_closes_resources():
    started=asyncio.Event()
    class SlowZC(FakeZC):
        async def async_register_service(self,info,**kwargs):
            started.set();await asyncio.sleep(5)
    advertiser=d.MDNSAdvertiser(zeroconf_factory=SlowZC,info_factory=FakeInfo)
    task=asyncio.create_task(advertiser.update('a'*64,d.parse_ip([interface()]),32851))
    await started.wait();task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert advertiser._zc is None and FakeZC.created[-1].events[-1][0]=='close'


@pytest.mark.asyncio
async def test_tailscale_command_disables_peer_collection_at_source():
    calls=[]
    async def own_only(argv):
        calls.append(argv)
        if argv[0]=='tailscale':
            assert argv==('tailscale','status','--json','--peers=false')
            return json.dumps(ts()).encode()
        return b'[]'
    snapshot=await d.AddressDiscovery(platform="linux",runner=own_only).snapshot()
    assert snapshot.endpoints and len(calls)==2
    assert sum(argv[0]=='tailscale' for argv in calls)==1
