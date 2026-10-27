import asyncio
from types import SimpleNamespace
import pytest
from server import platform_support
from server.store import Store
from server.__main__ import run


@pytest.mark.parametrize('name,ipc,private,lan,opener', [
    ('linux','unix','posix','linux-ip','xdg-open'),
    ('darwin','unix','posix','unavailable','open'),
    ('win32','unsupported','unsupported','unavailable',None),
    ('freebsd','unsupported','unsupported','unavailable',None),
])
def test_capability_table(name,ipc,private,lan,opener):
    assert platform_support.capabilities(name)==dict(desktop_ipc=ipc,private_files=private,lan_discovery=lan,opener=opener)


@pytest.mark.parametrize('platform',['win32','freebsd'])
def test_unsupported_store_and_run_create_nothing(monkeypatch,tmp_path,platform):
    monkeypatch.setattr(platform_support.sys,'platform',platform)
    directory=tmp_path/'must-not-exist'
    with pytest.raises(RuntimeError,match='已拒绝启动'):
        Store(directory)
    with pytest.raises(RuntimeError,match='已拒绝启动'):
        asyncio.run(run(SimpleNamespace(data=str(directory))))
    assert not directory.exists()


def test_cli_refuses_before_parsing_or_constructing_hub(monkeypatch,tmp_path):
    from server import __main__ as entry
    monkeypatch.setattr(platform_support.sys,'platform','win32')
    monkeypatch.setattr(platform_support.sys,'argv',['pocket','--data',str(tmp_path/'new')])
    def forbidden(*args,**kwargs):raise AssertionError('Hub must not start')
    monkeypatch.setattr(entry,'Hub',forbidden)
    with pytest.raises(SystemExit,match='已拒绝启动'):entry.main()
    assert not (tmp_path/'new').exists()
