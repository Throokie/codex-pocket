"""POSIX private state primitives, without changing the process-wide umask."""
import os
import secrets
import stat
from pathlib import Path
from .platform_support import capabilities, UNSUPPORTED_MESSAGE


class PrivatePathError(PermissionError):
    """Private state failed its ownership, type or permission policy."""


def require_posix():
    if capabilities()['private_files'] != 'posix':
        raise RuntimeError(UNSUPPORTED_MESSAGE)


def _check(info, directory=False):
    correct_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not correct_type or info.st_uid != os.getuid():
        raise PrivatePathError('私有路径必须为当前用户拥有的非符号链接常规文件或目录')
    if not directory and info.st_mode & 0o077:
        raise PrivatePathError('私有文件不能授予组或其他用户权限')


def ensure_private_dir(path):
    require_posix()
    path = Path(os.path.abspath(path))
    # Do not resolve the terminal component: O_NOFOLLOW must inspect that name.
    # Existing parent aliases are allowed (macOS /var -> /private/var).
    missing = []
    current = path
    while not current.exists() and not current.is_symlink():
        missing.append(current)
        current = current.parent
    if current == path and not stat.S_ISDIR(current.lstat().st_mode):
        raise PrivatePathError('私有目录不能为符号链接或常规文件')
    if not current.is_dir():
        raise PrivatePathError('私有目录的父路径无效')
    for item in reversed(missing):
        try:
            item.mkdir(mode=0o700)
        except FileExistsError:
            pass
        _check(item.lstat(), directory=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        _check(info, directory=True)
        os.fchmod(fd, stat.S_IMODE(info.st_mode) & 0o700)
    finally:
        os.close(fd)
    return path


def check_private(path):
    require_posix()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        _check(os.fstat(fd))
        return os.fstat(fd)
    finally:
        os.close(fd)


def read_private(path):
    require_posix()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        _check(os.fstat(source.fileno()))
        return source.read()


def create_private(path, data):
    require_posix()
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as target:
            _check(os.fstat(target.fileno()))
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
    except BaseException:
        Path(path).unlink(missing_ok=True)
        raise


def replace_private(path, data):
    require_posix()
    path = Path(path)
    if path.exists() or path.is_symlink():
        check_private(path)
    temporary = path.with_name('.' + path.name + '-' + secrets.token_hex(16) + '.tmp')
    created = False
    try:
        create_private(temporary, data)
        created = True
        os.replace(temporary, path)
    finally:
        if created:
            temporary.unlink(missing_ok=True)
