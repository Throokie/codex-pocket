#!/usr/bin/env python3
"""ADB file bridge. Standard library only; always select an explicit device."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

PACKAGE = 'io.github.codexpocket.app'
ROOT = '/sdcard/Android/data/' + PACKAGE + '/files/CodexPocket'
MAX_IMAGE = 12 * 1024 * 1024


class AdbError(Exception):
    pass


class Client:
    def __init__(self, serial, port=None, executable='adb'):
        self.prefix = [executable]
        if port is not None:
            self.prefix += ['-P', str(port)]
        self.prefix += ['-s', serial]

    def run(self, *args, timeout=20, check=True):
        try:
            p = subprocess.run(self.prefix + list(args), capture_output=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdbError('ADB unavailable or timed out; inspect the selected device before retrying.') from exc
        if check and p.returncode:
            # Do not print remote output: it may contain file contents or imported data.
            raise AdbError('ADB operation failed; check device authorization, selected serial and app installation.')
        return p

    def command(self, command, file=None, request_id=None, wait=30):
        rid = request_id or uuid.uuid4().hex
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', rid):
            raise AdbError('Invalid request ID.')
        args = ['shell', 'am', 'broadcast', '--include-stopped-packages', '--receiver-foreground',
                '-n', PACKAGE + '/.AdbCommandReceiver',
                '-a', PACKAGE + '.ADB_COMMAND', '--es', 'command', command,
                '--es', 'request_id', rid]
        if file is not None:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', file) or '..' in file:
                raise AdbError('Unsafe inbox filename.')
            args += ['--es', 'file', file]
        self.run(*args)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            p = self.run('exec-out', 'head', '-c', '16385', ROOT + '/outbox/result-' + rid + '.json', check=False)
            if p.returncode == 0 and len(p.stdout) <= 16384:
                try:
                    data = json.loads(p.stdout)
                except (ValueError, UnicodeError):
                    data = {}
                if not isinstance(data, dict):
                    data = {}
                if (data.get('version') == 1 and data.get('request_id') == rid
                        and data.get('command') == command and isinstance(data.get('status'), str)
                        and data['status'] in {'ok', 'prepared', 'error'}):
                    # Only publish the bounded receipt fields. Never blindly echo files.
                    receipt = {k: data[k] for k in ('version', 'request_id', 'command', 'status', 'code', 'updated_at', 'paired', 'history_epoch', 'history_size') if k in data}
                    print(json.dumps(receipt, ensure_ascii=False))
                    if data['status'] == 'error':
                        raise AdbError('App rejected the operation; see the receipt code above.')
                    return data
            time.sleep(.25)
        raise AdbError('No final receipt yet. Do not blindly repeat imports; inspect outbox/result-' + rid + '.json first.')


def main(argv=None):
    p = argparse.ArgumentParser(description='Codex Pocket ADB files: no root, no UI automation. Import prepares a preview/draft; it never sends or approves.')
    p.add_argument('--serial', required=True, help='Explicit adb device serial (required even with one device)')
    p.add_argument('--adb-port', type=int, help='Optional existing ADB server port')
    p.add_argument('--adb', default='adb', help='ADB executable')
    p.add_argument('--foreground', action='store_true', help='Open App first if a vendor blocks background file commands')
    sub = p.add_subparsers(dest='command', required=True)
    for name in ('init', 'status', 'paths', 'reset-history'):
        sub.add_parser(name)
    for name in ('import-pairing', 'import-image'):
        q = sub.add_parser(name)
        q.add_argument('file', type=Path)
    q = sub.add_parser('pull')
    q.add_argument('directory', type=Path, help='New local directory for outbox and media (no credential export)')
    a = p.parse_args(argv)
    if not a.serial.strip() or a.serial != a.serial.strip():
        p.error('--serial must be a nonempty exact device serial')
    if a.adb_port is not None and not 1 <= a.adb_port <= 65535:
        p.error('--adb-port must be 1..65535')
    if a.command == 'paths':
        print(json.dumps({k: ROOT + '/' + k for k in ('inbox', 'outbox', 'media')}, indent=2))
        return 0
    c = Client(a.serial, a.adb_port, a.adb)
    if a.foreground or a.command in {'import-pairing', 'import-image'}:
        c.run('shell', 'am', 'start', '-n', PACKAGE + '/.MainActivity')
        time.sleep(.5)
    if a.command in ('init', 'status'):
        c.command(a.command)
    elif a.command == 'reset-history':
        state = c.command('init')
        epoch = state.get('history_epoch')
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise AdbError('App lacks a valid import history epoch; update App before maintenance.')
        c.command('reset-history', request_id='e' + str(epoch) + '-' + uuid.uuid4().hex)
    elif a.command == 'pull':
        if a.directory.exists():
            raise AdbError('Use a new local directory to avoid overwriting existing files.')
        if c.run('shell', 'test', '-d', ROOT, check=False).returncode != 0:
            c.command('init')
        a.directory.mkdir(parents=True)
        for name in ('outbox', 'media'):
            c.run('pull', ROOT + '/' + name, str(a.directory / name), timeout=120)
        print(json.dumps({'status': 'ok', 'directory': str(a.directory.resolve())}))
    else:
        f = a.file
        if f.is_symlink() or not f.is_file():
            raise AdbError('Input must be a regular file, not a symlink.')
        limit = 65536 if a.command == 'import-pairing' else MAX_IMAGE
        if not 0 < f.stat().st_size <= limit:
            raise AdbError('Input is empty or exceeds the supported size limit.')
        suffix = '.json' if a.command == 'import-pairing' else f.suffix.lower()
        if a.command == 'import-image' and suffix not in {'.png', '.jpg', '.jpeg', '.webp', '.gif'}:
            raise AdbError('Use PNG, JPEG, WebP or GIF. The app also verifies the actual content.')
        state = c.command('init')
        epoch = state.get('history_epoch')
        if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 0:
            raise AdbError('App lacks a valid import history epoch; update App 0.3.1 before importing.')
        rid = 'e' + str(epoch) + '-' + uuid.uuid4().hex
        remote = rid + suffix
        c.run('push', str(f.resolve()), ROOT + '/inbox/' + remote, timeout=60)
        # Native consumes valid input. On rejection it may leave the file for inspection.
        c.command(a.command, file=remote, request_id=rid)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except AdbError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
