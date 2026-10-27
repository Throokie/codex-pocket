#!/usr/bin/env python3
"""Open this user's local manager without printing its capability URL."""
import os
from pathlib import Path
import subprocess
from .platform_support import capabilities
from .private_files import read_private


def manager_command(url, platform=None):
    opener = capabilities(platform)['opener']
    if not opener:
        raise RuntimeError('Local manager is not supported on this platform.')
    if not url.startswith('http://127.0.0.1:32849/#token='):
        raise RuntimeError('Unexpected local manager address; inspect the private runtime configuration.')
    return [opener, url]


def main():
    if not capabilities()['opener']:
        raise SystemExit('Local manager is not supported on this platform.')
    runtime = Path(os.environ.get('POCKET_DATA', str(Path.home()/'.local/share/codex-pocket')))
    link = runtime/'本机管理链接.txt'
    try:
        url = read_private(link).decode('utf-8').strip()
        command = manager_command(url)
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, ValueError, RuntimeError, UnicodeError, subprocess.SubprocessError):
        raise SystemExit('Cannot open local manager; check that the service is running and its private configuration is valid.') from None


if __name__ == '__main__':
    main()
