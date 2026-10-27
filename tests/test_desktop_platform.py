"""Platform/error boundary tests; no running desktop is contacted."""
import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

spec = importlib.util.spec_from_file_location('platform_desktop', Path(__file__).resolve().parents[1] / 'adapters/desktop.py')
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class PlatformTests(unittest.IsolatedAsyncioTestCase):
    async def test_windows_rejected_before_tasks_or_uid(self):
        adapter = desktop.DesktopAdapter('caller')
        with patch.object(desktop.sys, 'platform', 'win32'), patch.object(desktop.os, 'getuid', side_effect=AssertionError('UID used')):
            self.assertFalse(desktop._safe_socket('private-path'))
            self.assertEqual(adapter._pipe_paths(), [])
            with self.assertRaises(desktop.DesktopError) as caught:
                await adapter.start(AsyncMock())
            self.assertEqual(caught.exception.code, 'desktop_unavailable')
            self.assertIn('Windows', str(caught.exception))
            self.assertEqual(adapter._tasks, [])

    async def test_unsafe_socket_never_connects(self):
        connect = AsyncMock()
        with patch.object(desktop, '_safe_socket', return_value=False), patch.object(asyncio, 'open_unix_connection', connect):
            with self.assertRaises(desktop.DesktopError) as caught:
                await desktop._open_local_socket('/private/unsafe.sock')
            self.assertEqual(caught.exception.code, 'desktop_unavailable')
            self.assertNotIn('/private', str(caught.exception))
            connect.assert_not_awaited()

    async def test_os_failures_are_sanitized_for_both_connections(self):
        for error in [OSError('AF_UNIX path too long /private/socket'), PermissionError('/private/socket'), ValueError('embedded null /private/socket')]:
            for tool in [False, True]:
                with self.subTest(error=type(error).__name__, tool=tool):
                    connect = AsyncMock(side_effect=error)
                    with patch.object(desktop, '_safe_socket', return_value=True), patch.object(asyncio, 'open_unix_connection', connect):
                        with self.assertRaises(desktop.DesktopError) as caught:
                            if tool:
                                await desktop.DesktopAdapter('caller')._tool_rpc('/private/socket', 'tools/list', {})
                            else:
                                await desktop._open_local_socket('/private/socket')
                        self.assertEqual(caught.exception.code, 'desktop_unavailable')
                        self.assertNotIn('/private', str(caught.exception))
                        self.assertTrue(caught.exception.__suppress_context__)
                        connect.assert_awaited_once_with('/private/socket')

    async def test_success_keeps_exact_path(self):
        pair = (object(), object())
        connect = AsyncMock(return_value=pair)
        with patch.object(desktop, '_safe_socket', return_value=True), patch.object(asyncio, 'open_unix_connection', connect):
            self.assertIs(await desktop._open_local_socket('/private/socket'), pair)
            connect.assert_awaited_once_with('/private/socket')
