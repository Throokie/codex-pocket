#!/usr/bin/env python3
"""Check distribution contents and rebuild the wheel from its source archive."""
import hashlib
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from zipfile import ZipFile

folder = Path(sys.argv[1]).resolve()
wheel, = folder.glob('*.whl')
source, = folder.glob('*.tar.gz')
with ZipFile(wheel) as archive:
    members = archive.namelist()
    for name in members:
        parts = Path(name).parts
        assert not any(p in parts for p in ['tests', 'scripts', '__pycache__', '.local', '.git', 'build'])
        assert not name.endswith(('.apk', '.aab', '.db', '.db-wal', '.db-shm', '.sqlite', '.sqlite3', '.sqlite3-wal', '.sqlite3-shm', '.key', '.pem', '.keystore', '.jks', '.pyc', '.env'))
        assert Path(name).name not in ('admin-token', '本机管理链接.txt') and not Path(name).name.startswith('.env.')
    for name in ['server/admin.html', *['server/web/'+n for n in ['index.html', 'app.js', 'icons.js', 'style.css', 'transport.js']]]:
        assert archive.read(name), name
    assert archive.read('server/THIRD_PARTY_NOTICES.md')
    assert any(name.endswith('.dist-info/licenses/LICENSE') and archive.read(name) for name in members)
    original = {name: archive.read(name) for name in members}
with tempfile.TemporaryDirectory(prefix='pocket-sdist-') as temporary:
    root = Path(temporary)
    with tarfile.open(source) as archive:
        archive.extractall(root, filter='data')
    project, = [p for p in root.iterdir() if p.is_dir()]
    license_root = Path(__file__).resolve().parents[1]/'third_party'
    for original_license in license_root.rglob('*'):
        if original_license.is_file():
            assert (project/'third_party'/original_license.relative_to(license_root)).read_bytes() == original_license.read_bytes(), original_license.name
    output = root/'rebuilt'
    subprocess.run([sys.executable, '-m', 'build', '--wheel', '--outdir', str(output), str(project)], check=True)
    rebuilt, = output.glob('*.whl')
    with ZipFile(rebuilt) as archive:
        assert original == {name: archive.read(name) for name in archive.namelist()}, 'sdist did not reproduce wheel contents'
files = [wheel, source]
(folder/'SHA256SUMS.txt').write_text(''.join(hashlib.sha256(p.read_bytes()).hexdigest()+'  '+p.name+'\n' for p in sorted(files)))
print('Wheel assets, exclusion policy and source rebuild verified.')
