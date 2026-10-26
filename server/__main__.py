import argparse
import asyncio
import os
import secrets
import signal
from pathlib import Path
from aiohttp import web
from .app import Hub, create_app, create_admin_app
from . import __version__


async def run(args):
    from adapters.desktop import DesktopAdapter
    directory=Path(args.data).expanduser().resolve()
    directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    tokenfile=directory/'admin-token'
    if not tokenfile.exists():
        fd=os.open(tokenfile,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(40))
    token=tokenfile.read_text().strip()
    adapter=DesktopAdapter(caller_thread_id=args.caller_thread or args.controller, ipc_path=args.ipc)
    hub=Hub(directory,adapter,args.controller,args.name)
    hub.outbound_media_roots=[Path(p).expanduser().resolve() for p in args.media_root]
    await hub.start()
    root=Path(__file__).resolve().parents[1]
    public=web.AppRunner(create_app(hub,root/'app/web'),access_log=None)
    admin=web.AppRunner(create_admin_app(hub,token),access_log=None)
    await public.setup();await admin.setup()
    site=web.TCPSite(public,args.bind,args.port)
    # The public route table never contains admin endpoints. Token auth remains mandatory
    # even on this separate loopback listener; forwarded loopback IP is not identity.
    management=web.TCPSite(admin,'127.0.0.1',args.admin_port)
    await site.start();await management.start()
    automatic=None
    if not args.no_discovery:
        from .autonetwork import AutoNetwork
        automatic=AutoNetwork(hub,public,tls_port=args.tls_port)
        if args.exclude_interface is not None:
            await automatic.set_excluded_interfaces(args.exclude_interface)
        await automatic.start()
    link=directory/'本机管理链接.txt'
    link.write_text('http://127.0.0.1:'+str(args.admin_port)+'/#token='+token+'\n')
    os.chmod(link,0o600)
    print(f'Codex Pocket {__version__} 已启动，API {args.bind}:{args.port}；本机管理链接保存在 {link}',flush=True)
    print('非官方客户端。请勿将管理端口映射到公网；手机只连接API端口。',flush=True)
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):
        loop.add_signal_handler(sig,stop.set)
    try:await stop.wait()
    finally:
        hub.stopping=True
        for queues in list(hub.watchers.values()):
            for queue in list(queues):hub._notify(queue,None)
        if automatic:await automatic.close()
        await public.cleanup();await admin.cleanup();await hub.close()


def main():
    parser=argparse.ArgumentParser(description='Codex Pocket 自托管会话桥（非官方）')
    parser.add_argument('--data',default='.local')
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=32848)
    parser.add_argument('--admin-port',type=int,default=32849)
    parser.add_argument('--tls-port',type=int,default=32851,help='自动发现的私网加密端口')
    parser.add_argument('--exclude-interface',action='append',default=None,help='不向手机发布的网卡；可重复，保存到私有配置')
    parser.add_argument('--no-discovery',action='store_true',help='禁用自动私网监听和局域网广播，保留手动旧版模式')
    parser.add_argument('--controller',default=None,help='本机总控thread ID；也可配对后选择')
    parser.add_argument('--caller-thread',default=None,help='桌面工具调用来源thread ID，默认同总控')
    parser.add_argument('--ipc',default=None,help='可选桌面IPC路径，默认本用户Codex目录')
    parser.add_argument('--name',default='我的电脑')
    parser.add_argument('--media-root',action='append',default=[],help='允许回传图片的目录，严格白名单，可重复')
    args=parser.parse_args()
    args.caller_thread=args.caller_thread or args.controller or os.environ.get('CODEX_THREAD_ID')
    if not args.caller_thread:parser.error('需要 --caller-thread（有效本机Codex会话ID）；可与 --controller 相同')
    if not 1<=args.tls_port<=65535:parser.error('TLS端口必须为1至65535')
    if not args.no_discovery and args.tls_port in (args.port,args.admin_port):parser.error('自动TLS端口不能与API或管理端口相同')
    if args.no_discovery and args.exclude_interface:parser.error('排除网卡仅适用于自动发现模式')
    if args.port==args.admin_port:parser.error('API与管理端口必须不同')
    asyncio.run(run(args))

if __name__=='__main__':main()
