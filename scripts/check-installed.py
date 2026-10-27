#!/usr/bin/env python3
"""Run with an isolated venv interpreter, from outside the source checkout."""
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error


def main():
    import server
    from server.resources import WEB_ASSETS, web_resource, admin_resource
    assert Path(server.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()), 'Imported source checkout instead of installed wheel'
    cli = Path(sys.executable).parent/('codex-pocket.exe' if os.name == 'nt' else 'codex-pocket')
    version = importlib.metadata.version('codex-pocket')
    assert subprocess.check_output([str(cli), '--version'], text=True).strip() == version == server.__version__
    subprocess.run([str(cli), '--help'], check=True, stdout=subprocess.DEVNULL)
    hashes = {name: hashlib.sha256(web_resource(name).read_bytes()).hexdigest() for name in WEB_ASSETS}
    assert all(web_resource(name).read_bytes() for name in WEB_ASSETS)
    assert b'Codex Pocket' in admin_resource().read_bytes()
    checks = ['installed package imported outside checkout', 'version matches metadata', 'help works', 'five web assets and manager present']
    with tempfile.TemporaryDirectory(prefix='pocket-install-') as folder:
        root = Path(folder)
        state = root/'private'
        empty_temp = root/'isolated-temp'
        empty_temp.mkdir(mode=0o700)
        env = dict(os.environ, PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1', CODEX_HOME=str(root/'absent-codex'), TMPDIR=str(empty_temp), TMP=str(empty_temp), TEMP=str(empty_temp))
        report = json.loads(subprocess.check_output([str(cli), 'doctor', '--json', '--data', str(state)], env=env, text=True))
        assert report['protocol'] == 1 and report['version'] == version and report['platform'] == sys.platform
        assert set(report) == {'protocol', 'version', 'platform', 'capabilities', 'desktop_adapter', 'data_directory'}
        assert not state.exists()
        checks.append('doctor reports capabilities without creating data')
        if sys.platform == 'win32':
            result = subprocess.run([str(cli), 'serve', '--data', str(state)], env=env, capture_output=True, text=True, encoding='utf-8', timeout=15)
            assert result.returncode == 3 and '已拒绝启动' in result.stderr
            assert report['desktop_adapter'] == 'unsupported' and not state.exists()
            checks.append('Windows serve refuses with code 3 before data creation (not desktop support)')
        else:
            holders = [socket.socket(), socket.socket()]
            for holder in holders:
                holder.bind(('127.0.0.1', 0))
            port, admin_port = [holder.getsockname()[1] for holder in holders]
            for holder in holders:
                holder.close()
            process = subprocess.Popen([str(cli), 'serve', '--no-discovery', '--bind', '127.0.0.1', '--port', str(port), '--admin-port', str(admin_port), '--data', str(state), '--controller', 'install-test', '--caller-thread', 'install-test', '--ipc', str(root/'absent-ipc.sock')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                deadline = time.monotonic()+45
                while True:
                    assert process.poll() is None, 'Installed service exited before becoming healthy'
                    try:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                            assert json.load(response) == {'ok': True, 'protocol': 1}
                        break
                    except (OSError, urllib.error.URLError):
                        if time.monotonic() >= deadline:
                            raise AssertionError('Installed service did not become healthy')
                        time.sleep(.2)
                # Only a token generated in this disposable test namespace, never printed.
                token = (state/'admin-token').read_text().strip()
                request = urllib.request.Request(f'http://127.0.0.1:{admin_port}/admin/state', headers={'Authorization': 'Bearer '+token})
                with urllib.request.urlopen(request, timeout=3) as response:
                    status = json.load(response)
                assert status['state']['capabilities']['chat'] is False
                assert status['state']['capabilities']['live'] is False
                with urllib.request.urlopen(f'http://127.0.0.1:{admin_port}/', timeout=3) as response:
                    assert b'Codex Pocket' in response.read()
                while not (state/'本机管理链接.txt').exists():
                    assert process.poll() is None, 'Service exited before manager link became ready'
                    if time.monotonic() >= deadline:
                        raise AssertionError('Manager link was not created before readiness deadline')
                    time.sleep(.1)
                for name in ['pocket.sqlite3', 'pocket.sqlite3-wal', 'pocket.sqlite3-shm', 'pair-envelope.key', 'admin-token', 'server-identity.pem', '本机管理链接.txt']:
                    assert stat.S_IMODE((state/name).stat().st_mode) == 0o600, name
                checks += ['installed service and manager start on loopback', 'absent desktop leaves chat/live disabled', 'private database, sidecars and credentials stay 0600']
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
    print(json.dumps({'platform': sys.platform, 'version': version, 'checks': checks, 'asset_sha256': hashes, 'desktop_acceptance': False}, indent=2))


if __name__ == '__main__':
    main()
