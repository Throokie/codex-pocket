#!/usr/bin/env python3
"""Install one candidate wheel into a new venv and test outside the checkout."""
import hashlib
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import venv

root = Path(__file__).resolve().parents[1]
candidate = Path(sys.argv[1]).resolve()
for line in (candidate/'SHA256SUMS.txt').read_text().splitlines():
    expected, name = line.split('  ', 1)
    assert Path(name).name == name
    assert hashlib.sha256((candidate/name).read_bytes()).hexdigest() == expected
wheel, = candidate.glob('*.whl')
with tempfile.TemporaryDirectory(prefix='pocket-venv-') as folder:
    environment = Path(folder)/'venv'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment/('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    subprocess.run([str(python), '-m', 'pip', 'install', str(wheel)], check=True)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True)
    subprocess.run([str(python), str(root/'scripts/check-installed.py')], cwd=folder, check=True)
