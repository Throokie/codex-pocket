#!/usr/bin/env python3
"""Open this user's local manager without printing its capability URL."""
import os
from pathlib import Path
import subprocess

runtime=Path(os.environ.get('POCKET_DATA',str(Path.home()/'.local/share/codex-pocket')))
link=runtime/'本机管理链接.txt'
if not link.is_file():
    raise SystemExit('Start Codex Pocket service before opening its local manager.')
url=link.read_text().strip()
if not url.startswith('http://127.0.0.1:32849/#token='):
    raise SystemExit('Unexpected local manager address; inspect the private runtime configuration.')
subprocess.run(['xdg-open',url],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
