import importlib.util
from pathlib import Path
from unittest.mock import patch
import pytest
from server.discovery import AddressDiscovery

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT/path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manager_platform_and_url_validation():
    mod = load('pocket_manager', 'scripts/open-manager.py')
    url = 'http://127.0.0.1:32849/#token=not-a-device-credential'
    assert mod.manager_command(url, 'linux') == ['xdg-open', url]
    assert mod.manager_command(url, 'darwin') == ['open', url]
    with pytest.raises(RuntimeError):
        mod.manager_command(url, 'win32')
    with pytest.raises(RuntimeError):
        mod.manager_command('https://example.invalid/#token=test', 'darwin')


@pytest.mark.asyncio
async def test_darwin_skips_linux_command_preserves_tailscale():
    calls = []
    async def runner(argv):
        calls.append(argv)
        assert argv[0] == 'tailscale'
        return b'{"BackendState":"Running","Self":{"TailscaleIPs":["100.100.100.10"]}}'
    with patch('server.platform_support.sys.platform', 'darwin'):
        result = await AddressDiscovery(runner=runner).snapshot()
    assert len(calls) == 1
    assert any('本平台自动局域网发现尚在适配' in s for s in result.diagnostics)
    assert any(e['url'] == 'https://100.100.100.10:32851' for e in result.endpoints)


def test_probe_does_not_read_contents_or_connect(tmp_path, monkeypatch):
    mod = load('pocket_probe', 'scripts/platform-probe.py')
    monkeypatch.setenv('CODEX_HOME', str(tmp_path/'codex'))
    monkeypatch.setenv('TMPDIR', str(tmp_path/'temp'))
    with patch.object(Path, 'read_text', side_effect=AssertionError('no file reads')), patch.object(Path, 'read_bytes', side_effect=AssertionError('no file reads')), patch.object(mod.sys, 'platform', 'linux'):
        result = mod.inspect_platform()
    assert result['metadata_only'] and result['desktop_ipc'] == {'present': False}
    assert not (tmp_path/'temp').exists() and not (tmp_path/'codex').exists()
