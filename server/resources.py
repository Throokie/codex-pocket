"""Locate installed package assets without relying on the working directory."""
from importlib.resources import files
from pathlib import Path

WEB_ASSETS = ('index.html', 'app.js', 'icons.js', 'style.css', 'transport.js')


def web_resource(name):
    if name not in WEB_ASSETS:
        raise ValueError('Unknown bundled web resource')
    resource = files('server').joinpath('web', name)
    if resource.is_file():
        return resource
    source_root = Path(__file__).resolve().parents[1]
    source = source_root/'app'/'web'/name
    if (source_root/'pyproject.toml').is_file() and source.is_file():
        return source
    raise RuntimeError('Codex Pocket web resources are missing; reinstall the package.')


def web_root():
    resource = web_resource('index.html')
    # pip unpacks wheels; aiohttp's static router requires a filesystem directory.
    if not isinstance(resource, Path):
        raise RuntimeError('Install Codex Pocket as an unpacked wheel before serving web resources.')
    root = resource.parent
    if not all((root/name).is_file() for name in WEB_ASSETS):
        raise RuntimeError('Codex Pocket web resources are incomplete; reinstall the package.')
    return root


def admin_resource():
    resource = files('server').joinpath('admin.html')
    if not resource.is_file():
        raise RuntimeError('Codex Pocket manager resource is missing; reinstall the package.')
    return resource
