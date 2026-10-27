import os
import stat
import pytest
from server import private_files as private
from server.store import Store
from server.network import ServerIdentity, ServerTLS


def test_create_replace_private_and_existing_key_stable(tmp_path):
    directory=private.ensure_private_dir(tmp_path/'private')
    path=directory/'file'
    private.create_private(path,b'first')
    with pytest.raises(FileExistsError):private.create_private(path,b'second')
    private.replace_private(path,b'second')
    assert private.read_private(path)==b'second'
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert stat.S_IMODE(directory.stat().st_mode)==0o700
    identity=ServerIdentity(directory);tls=ServerTLS(directory)
    original={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in directory.glob('*.pem')}
    assert ServerIdentity(directory).id==identity.id
    assert ServerTLS(directory).spki==tls.spki
    assert original=={p.name:(p.read_bytes(),p.stat().st_mtime_ns) for p in directory.glob('*.pem')}


@pytest.mark.parametrize('name',['pair-envelope.key','pocket.sqlite3','pocket.sqlite3-wal','pocket.sqlite3-shm'])
def test_store_rejects_symlink_without_modifying_target(tmp_path,name):
    directory=private.ensure_private_dir(tmp_path/'data')
    victim=tmp_path/'victim';victim.write_bytes(b'untouched')
    (directory/name).symlink_to(victim)
    with pytest.raises((OSError,RuntimeError)):Store(directory)
    assert victim.read_bytes()==b'untouched'


def test_directory_rejects_symlink_and_foreign_owner(monkeypatch,tmp_path):
    real=tmp_path/'real';real.mkdir();link=tmp_path/'link';link.symlink_to(real)
    with pytest.raises((OSError,RuntimeError)):private.ensure_private_dir(link)
    monkeypatch.setattr(private.os,'getuid',lambda:real.stat().st_uid+1)
    with pytest.raises(private.PrivatePathError):private.ensure_private_dir(real)


def test_permissions_only_tightened_and_loose_file_rejected(tmp_path):
    path=tmp_path/'directory';path.mkdir();path.chmod(0o750)
    private.ensure_private_dir(path)
    assert stat.S_IMODE(path.stat().st_mode)==0o700
    file=path/'loose';file.write_bytes(b'original');file.chmod(0o644)
    with pytest.raises(private.PrivatePathError):private.replace_private(file,b'new')
    assert file.read_bytes()==b'original'


def test_sqlite_sidecars_private_without_umask_change(monkeypatch,tmp_path):
    def forbidden(*args):raise AssertionError('global umask changed')
    monkeypatch.setattr(os,'umask',forbidden)
    store=Store(tmp_path/'state')
    store.set_setting('preserved',True)
    key=private.read_private(store.directory/'pair-envelope.key')
    for suffix in ('','-wal','-shm'):
        assert stat.S_IMODE((store.directory/('pocket.sqlite3'+suffix)).stat().st_mode)==0o600
    store.close()
    again=Store(store.directory)
    assert again.setting('preserved') is True
    assert private.read_private(again.directory/'pair-envelope.key')==key
    again.close()


def test_replace_failure_preserves_original_and_cleans_temp(monkeypatch,tmp_path):
    path=tmp_path/'data';private.create_private(path,b'old')
    def fail(*args):raise OSError('injected')
    monkeypatch.setattr(os,'replace',fail)
    with pytest.raises(OSError):private.replace_private(path,b'new')
    assert path.read_bytes()==b'old'
    assert list(tmp_path.iterdir())==[path]


def test_unsupported_helpers_do_not_create(monkeypatch,tmp_path):
    monkeypatch.setattr('server.platform_support.sys.platform','win32')
    with pytest.raises(RuntimeError):private.ensure_private_dir(tmp_path/'new')
    with pytest.raises(RuntimeError):private.create_private(tmp_path/'new',b'')
    assert not (tmp_path/'new').exists()


def test_system_parent_alias_allowed_but_terminal_symlink_rejected(tmp_path):
    # macOS commonly exposes its private temp tree through /var -> /private/var.
    real=tmp_path/'real';real.mkdir()
    alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True)
    directory=private.ensure_private_dir(alias/'new')
    assert directory.is_dir()
    assert stat.S_IMODE((real/'new').stat().st_mode)==0o700
    with pytest.raises(private.PrivatePathError):private.ensure_private_dir(alias)


@pytest.mark.parametrize('constructor',[ServerIdentity,ServerTLS])
def test_network_constructors_reject_unsupported_before_files(monkeypatch,tmp_path,constructor):
    monkeypatch.setattr('server.platform_support.sys.platform','win32')
    with pytest.raises(RuntimeError):constructor(tmp_path/'absent')
    assert not (tmp_path/'absent').exists()


def test_foreign_file_owner_not_rewritten(monkeypatch,tmp_path):
    path=tmp_path/'file';private.create_private(path,b'old')
    monkeypatch.setattr(private.os,'getuid',lambda:path.stat().st_uid+1)
    with pytest.raises(private.PrivatePathError):private.replace_private(path,b'new')
    assert path.read_bytes()==b'old'


def test_partial_creation_failure_removes_only_new_file(monkeypatch,tmp_path):
    path=tmp_path/'new'
    def fail(*args):raise OSError('flush failed')
    monkeypatch.setattr(os,'fsync',fail)
    with pytest.raises(OSError):private.create_private(path,b'partial')
    assert not path.exists()
    existing=tmp_path/'existing';existing.write_bytes(b'old')
    with pytest.raises(FileExistsError):private.create_private(existing,b'new')
    assert existing.read_bytes()==b'old'


@pytest.mark.asyncio
async def test_private_path_error_returns_safe_storage_failure():
    import json
    from server.app import errors
    async def fail(_):
        raise private.PrivatePathError('private-path-should-not-leak')
    response = await errors(None, fail)
    assert response.status == 503
    body = json.loads(response.text)
    assert body['error']['code'] == 'private_storage_unavailable'
    assert 'private-path-should-not-leak' not in response.text


def test_media_permission_rejection_is_handled(monkeypatch, tmp_path):
    from PIL import Image
    from server import app
    hub = app.Hub(tmp_path/'state', object(), 'test', 'test')
    source = tmp_path/'image.png'
    Image.new('RGB', (4, 4)).save(source)
    hub.outbound_media_roots = [tmp_path]
    good = hub.register_output(str(source))
    assert good
    row = hub.store.db.execute('SELECT * FROM media WHERE id=?', (good['id'],)).fetchone()
    hub.store.db.execute('DELETE FROM media WHERE id=?', (good.get('thumbnail_id'),))
    def fail(*_):
        raise private.PrivatePathError('blocked')
    monkeypatch.setattr(app, 'replace_private', fail)
    assert hub.ensure_thumbnail(row['id']) is None
    other = tmp_path/'other.png'
    Image.new('RGB', (5, 5)).save(other)
    assert hub.register_output(str(other)) is None
    hub.store.close()
