"""Installable entry point; diagnostic commands never initialize private state."""
import argparse
import json
import os
from pathlib import Path
import stat
import sys
from . import __version__
from .platform_support import capabilities, UNSUPPORTED_MESSAGE


def diagnose(data=None):
    support = capabilities()
    directory = Path(data).expanduser() if data else Path('.local')
    state = {'present': None, 'state': 'unsupported'}
    if support['private_files'] == 'posix':
        try:
            info = directory.lstat()
            private = (stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
                       and not stat.S_IMODE(info.st_mode) & 0o077)
            state = {'present': True, 'state': 'private' if private else 'insecure'}
        except FileNotFoundError:
            state = {'present': False, 'state': 'absent'}
        except OSError:
            state = {'present': None, 'state': 'unknown'}
    return {'protocol': 1, 'version': __version__, 'platform': sys.platform,
            'capabilities': support,
            'desktop_adapter': 'implemented-unverified' if support['desktop_ipc'] == 'unix' else 'unsupported',
            'data_directory': state}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == 'serve':
        if capabilities()['desktop_ipc'] != 'unix' or capabilities()['private_files'] != 'posix':
            print(UNSUPPORTED_MESSAGE, file=sys.stderr)
            return 3
        from .__main__ import main as serve
        serve(args[1:])
        return 0
    if args and args[0] == 'manager':
        from .manager import main as manager
        if len(args) != 1:
            parser = argparse.ArgumentParser(prog='codex-pocket manager')
            parser.parse_args(args[1:])
        manager()
        return 0
    parser = argparse.ArgumentParser(prog='codex-pocket', description='Codex Pocket install and capability checks; unofficial.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command')
    doctor = commands.add_parser('doctor', help='Read-only capability check; no desktop connection or private-file reads')
    doctor.add_argument('--json', action='store_true')
    doctor.add_argument('--data', default=None, help='Directory metadata to check; never created')
    commands.add_parser('serve', help='Start the local service (Linux/macOS; desktop compatibility requires validation)')
    commands.add_parser('manager', help='Open the running local manager (Linux/macOS)')
    parsed = parser.parse_args(args)
    if parsed.command == 'doctor':
        result = diagnose(parsed.data)
        if parsed.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            print('Codex Pocket '+__version__)
            print('Platform: '+result['platform'])
            print('Desktop adapter: '+result['desktop_adapter'])
            print('Private files: '+result['capabilities']['private_files'])
            print('Data directory: '+result['data_directory']['state'])
            print('Installation does not prove desktop compatibility.')
        return 0
    parser.print_help()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
