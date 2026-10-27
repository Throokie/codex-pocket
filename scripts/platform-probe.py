#!/usr/bin/env python3
"""Metadata only: no IPC connection, file contents, credentials, or file creation."""
import json
import os
from pathlib import Path
import stat
import subprocess
import sys


def metadata(path):
    try:
        info = path.lstat()
        return {'present': True, 'type': 'symlink' if stat.S_ISLNK(info.st_mode) else 'socket' if stat.S_ISSOCK(info.st_mode) else 'directory' if stat.S_ISDIR(info.st_mode) else 'file' if stat.S_ISREG(info.st_mode) else 'other',
                'owned_by_current_user': info.st_uid == os.getuid() if hasattr(os, 'getuid') else None,
                'mode': oct(stat.S_IMODE(info.st_mode)) if os.name == 'posix' else None}
    except FileNotFoundError:
        return {'present': False}
    except OSError:
        return {'present': None, 'error': 'metadata_unavailable'}


def command(argv, allow_no_match=False):
    try:
        with subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
            try:
                value, _ = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                return None
            if allow_no_match and process.returncode == 1:
                return ''
            if process.returncode or len(value) > 1024 * 1024:
                return None
            return value.decode('utf-8', errors='replace')
    except OSError:
        return None


def inspect_platform():
    # Unlike tempfile.gettempdir(), this never creates a probe file to pick a directory.
    tmp = Path(os.environ.get('TMPDIR') or os.environ.get('TEMP') or os.environ.get('TMP') or ('C:/Windows/Temp' if os.name == 'nt' else '/tmp'))
    root = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
    result = {'platform': sys.platform, 'python': sys.version.split()[0], 'metadata_only': True,
              'temp_path_bytes': len(os.fsencode(str(tmp))), 'desktop_ipc': metadata(root/'ipc/ipc.sock')}
    directory = tmp/'codex-app-tools'
    result['tools_directory'] = metadata(directory)
    try:
        result['tool_sockets'] = [{'name': p.name, **metadata(p)} for p in sorted(directory.glob('*.sock'))[:10]] if directory.is_dir() and not directory.is_symlink() else []
    except OSError:
        result['tool_sockets'] = []
    if sys.platform == 'darwin':
        result['app_locations'] = []
        for prefix, folder in [('/Applications', Path('/Applications')), ('~/Applications', Path.home()/'Applications')]:
            try:
                result['app_locations'] += [prefix+'/'+p.name for p in sorted(folder.glob('Codex*.app'))[:10]]
            except OSError:
                pass
        search = command(['/usr/bin/mdfind', 'kMDItemFSName == "Codex.app"c'])
        # Only report matching bundle basenames/count, never user or project paths.
        names = [Path(line).name for line in (search or '').splitlines() if Path(line).name.lower() == 'codex.app']
        result['spotlight'] = {'available': search is not None, 'matching_bundles': len(names[:100])}
        ports = command(['/usr/sbin/networksetup', '-listallhardwareports'])
        devices, port = [], None
        for line in (ports or '').splitlines():
            if line.startswith('Hardware Port: '):
                port = line.split(': ', 1)[1][:80]
            elif line.startswith('Device: '):
                device = line.split(': ', 1)[1]
                if device and len(device) < 32:
                    devices.append({'hardware_port': port, 'device': device})
        result['hardware_ports'] = devices[:32]
        interfaces = command(['/sbin/ifconfig', '-l'])
        result['interfaces'] = (interfaces or '').split()[:64]
        user_temp = command(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'])
        user_temp = user_temp.strip() if user_temp else None
        result['darwin_user_temp'] = {'available': bool(user_temp), 'path_bytes': len(os.fsencode(user_temp)) if user_temp else None, 'tools_directory': metadata(Path(user_temp)/'codex-app-tools') if user_temp else {'present': None}}
        processes = command(['/usr/bin/pgrep', '-x', 'Codex'], allow_no_match=True)
        result['codex_running'] = bool(processes.strip()) if processes is not None else None
    elif sys.platform == 'win32':
        try:
            result['named_pipes'] = [name for name in os.listdir('\\\\.\\pipe\\') if 'codex' in name.lower()][:32]
        except OSError:
            result['named_pipes'] = []
            result['pipe_listing_available'] = False
        roots = [('local-programs', os.environ.get('LOCALAPPDATA'), 'Programs'), ('program-files', os.environ.get('ProgramFiles'), '')]
        result['app_locations'] = []
        for label, value, suffix in roots:
            if not value:
                continue
            folder = Path(value)/suffix
            try:
                result['app_locations'] += [label+'/'+p.name for p in sorted(folder.glob('*Codex*'))[:10]]
            except OSError:
                pass
        processes = command(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', "@(Get-Process -Name Codex -ErrorAction SilentlyContinue).Count"])
        result['codex_running'] = int(processes.strip()) > 0 if processes is not None and processes.strip().isdigit() else None
    return result


if __name__ == '__main__':
    print(json.dumps(inspect_platform(), ensure_ascii=False, indent=2))
