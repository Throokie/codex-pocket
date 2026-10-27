"""Single capability gate; platform names do not imply desktop acceptance."""
import sys

UNSUPPORTED_MESSAGE = '当前平台尚不支持：Codex 桌面连接与私钥保护未经验证，已拒绝启动。见 docs/PLATFORMS.md'


def capabilities(platform=None):
    platform = sys.platform if platform is None else platform
    supported = platform in ('linux', 'darwin')
    return {'desktop_ipc': 'unix' if supported else 'unsupported',
            'private_files': 'posix' if supported else 'unsupported',
            'lan_discovery': 'linux-ip' if platform == 'linux' else 'unavailable',
            'opener': {'linux': 'xdg-open', 'darwin': 'open'}.get(platform)}


def require_supported():
    value = capabilities()
    if value['desktop_ipc'] != 'unix' or value['private_files'] != 'posix':
        raise RuntimeError(UNSUPPORTED_MESSAGE)
    return value
