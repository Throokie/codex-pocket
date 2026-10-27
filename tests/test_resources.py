from pathlib import Path
import pytest
from server.resources import WEB_ASSETS, admin_resource, web_resource, web_root


def test_source_resources_are_independent_of_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    root = web_root()
    assert isinstance(root, Path) and root.is_dir()
    for name in WEB_ASSETS:
        assert web_resource(name).read_bytes() and (root/name).is_file()
    assert b'<!doctype html>' in admin_resource().read_bytes().lower()


def test_resource_names_cannot_escape():
    for name in ['../admin.html', '/etc/passwd', 'missing.js']:
        with pytest.raises(ValueError):
            web_resource(name)


def test_missing_installed_assets_do_not_use_unrelated_app_package(tmp_path, monkeypatch):
    import pytest
    from server import resources
    package = tmp_path/'server'
    package.mkdir()
    other = tmp_path/'app/web'
    other.mkdir(parents=True)
    (other/'index.html').write_text('unrelated package')
    monkeypatch.setattr(resources, '__file__', str(package/'resources.py'))
    monkeypatch.setattr(resources, 'files', lambda _: package)
    with pytest.raises(RuntimeError, match='missing'):
        resources.web_resource('index.html')
