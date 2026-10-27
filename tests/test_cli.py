import json
from pathlib import Path
import subprocess
from unittest.mock import patch
import pytest
from server import cli, __version__, platform_support


def test_version_and_help_have_no_service_side_effects(capsys):
    with pytest.raises(SystemExit) as result:
        cli.main(['--version'])
    assert result.value.code == 0 and capsys.readouterr().out.strip() == __version__
    with pytest.raises(SystemExit) as result:
        cli.main(['--help'])
    assert result.value.code == 0 and 'doctor' in capsys.readouterr().out


def test_doctor_json_never_reads_files_connects_or_creates(tmp_path, capsys):
    target = tmp_path/'absent'
    with patch.object(Path, 'read_text', side_effect=AssertionError('file content read')), patch.object(Path, 'read_bytes', side_effect=AssertionError('file content read')), patch('asyncio.open_unix_connection', side_effect=AssertionError('connection')):
        assert cli.main(['doctor', '--json', '--data', str(target)]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data['protocol'] == 1 and data['version'] == __version__
    assert data['data_directory'] == {'present': False, 'state': 'absent'}
    assert data['desktop_adapter'] == 'implemented-unverified'
    assert str(tmp_path) not in json.dumps(data) and not target.exists()


def test_doctor_rejects_symlink_metadata(tmp_path):
    target = tmp_path/'target'
    target.mkdir(mode=0o700)
    link = tmp_path/'link'
    link.symlink_to(target, target_is_directory=True)
    assert cli.diagnose(str(link))['data_directory'] == {'present': True, 'state': 'insecure'}


def test_windows_install_entry_refuses_serve_without_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(platform_support.sys, 'platform', 'win32')
    target = tmp_path/'not-created'
    assert cli.main(['serve', '--data', str(target)]) == 3
    assert '已拒绝启动' in capsys.readouterr().err
    assert not target.exists()
    assert cli.main(['doctor', '--json', '--data', str(target)]) == 0
    state = json.loads(capsys.readouterr().out)
    assert state['desktop_adapter'] == 'unsupported'
    assert state['data_directory'] == {'present': None, 'state': 'unsupported'}


def test_serve_forwards_original_arguments_without_dispatch_loop(monkeypatch):
    from server import __main__ as entry
    received = []
    monkeypatch.setattr(entry, 'main', lambda argv: received.append(argv))
    assert cli.main(['serve', '--data', 'safe-data', '--caller-thread', 'fixture']) == 0
    assert received == [['--data', 'safe-data', '--caller-thread', 'fixture']]


def test_manager_process_failure_does_not_expose_capability(tmp_path, monkeypatch):
    from server import manager
    runtime = tmp_path/'runtime'
    runtime.mkdir(mode=0o700)
    link = runtime/'本机管理链接.txt'
    url = 'http://127.0.0.1:32849/#token=private-test-sentinel'
    link.write_text(url)
    link.chmod(0o600)
    monkeypatch.setenv('POCKET_DATA', str(runtime))
    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, args[0])
    monkeypatch.setattr(manager.subprocess, 'run', fail)
    with pytest.raises(SystemExit) as result:
        manager.main()
    assert url not in str(result.value) and 'private-test-sentinel' not in str(result.value)
